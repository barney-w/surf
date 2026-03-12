import type { AgentChatConfig } from '@surf-kit/agent'
import { fetch } from 'expo/fetch'

/**
 * Creates an SSE stream adapter for React Native using expo/fetch.
 *
 * React Native's built-in fetch and XMLHttpRequest do not support incremental
 * streaming (ReadableStream / onprogress). Expo's fetch implementation provides
 * a proper ReadableStream with getReader() support, enabling real-time SSE
 * event processing as chunks arrive from the server.
 */
export function createNativeSSEAdapter(): NonNullable<AgentChatConfig['streamAdapter']> {
  return async (url, options, onEvent) => {
    const response = await fetch(url, {
      method: options.method,
      headers: options.headers,
      body: options.body,
      signal: options.signal,
    })

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
  }
}
