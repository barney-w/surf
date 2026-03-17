import type { AgentChatConfig } from '@surf-kit/agent'
import { fetch } from 'expo/fetch'

/** Timeout in milliseconds — matches LLM_TIMEOUT_SECONDS on the API. */
const SSE_TIMEOUT_MS = 90_000

/** Maximum number of retry attempts before propagating the error. */
const MAX_RETRIES = 3

/**
 * Creates an SSE stream adapter for React Native using expo/fetch.
 *
 * React Native's built-in fetch and XMLHttpRequest do not support incremental
 * streaming (ReadableStream / onprogress). Expo's fetch implementation provides
 * a proper ReadableStream with getReader() support, enabling real-time SSE
 * event processing as chunks arrive from the server.
 *
 * Resilience features:
 * - 90 s timeout per attempt (AbortController)
 * - Exponential-backoff retry (up to 3 retries, capped at 10 s)
 */
export function createNativeSSEAdapter(): NonNullable<AgentChatConfig['streamAdapter']> {
  return async (url, options, onEvent) => {
    let attempts = 0

    while (attempts <= MAX_RETRIES) {
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), SSE_TIMEOUT_MS)

      // If the caller supplied its own signal, forward its abort to our
      // controller so cancellation still works as expected.
      if (options.signal) {
        options.signal.addEventListener('abort', () => controller.abort())
      }

      try {
        const response = await fetch(url, {
          method: options.method,
          headers: options.headers,
          body: options.body,
          signal: controller.signal,
        })

        clearTimeout(timeoutId)

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`)
        }

        const reader = response.body?.getReader()
        if (!reader) {
          throw new Error('No response body for SSE stream')
        }

        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          // Last element is either empty (text ended with \n) or an incomplete
          // line — stash it for the next chunk.
          buffer = lines.pop() ?? ''

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            const data = line.slice(6).trim()
            if (data === '[DONE]') continue

            try {
              const event = JSON.parse(data) as { type: string; [key: string]: unknown }
              onEvent(event)
            } catch {
              // Skip malformed events
            }
          }
        }

        // Process any remaining buffered data
        if (buffer.startsWith('data: ')) {
          const data = buffer.slice(6).trim()
          if (data && data !== '[DONE]') {
            try {
              const event = JSON.parse(data) as { type: string; [key: string]: unknown }
              onEvent(event)
            } catch {
              // Skip malformed events
            }
          }
        }

        // Stream completed successfully — exit the retry loop.
        break
      } catch (error) {
        clearTimeout(timeoutId)

        // If the caller explicitly aborted, do not retry.
        if (options.signal?.aborted) throw error

        if (attempts >= MAX_RETRIES) throw error
        attempts++

        // Exponential back-off: 2 s, 4 s, 8 s (capped at 10 s).
        const delayMs = Math.min(1000 * 2 ** attempts, 10_000)
        await new Promise((resolve) => setTimeout(resolve, delayMs))
      }
    }
  }
}
