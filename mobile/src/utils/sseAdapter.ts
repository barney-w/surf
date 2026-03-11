import type { AgentChatConfig } from '@surf-kit/agent'

/**
 * Creates an SSE stream adapter for React Native using XMLHttpRequest.
 * React Native's fetch does not support ReadableStream / getReader(), but XHR
 * fires onprogress events which we can use to parse SSE lines progressively.
 *
 * Handles line buffering correctly — an SSE line may be split across multiple
 * onprogress callbacks so we keep a running buffer of incomplete data.
 */
export function createNativeSSEAdapter(): NonNullable<AgentChatConfig['streamAdapter']> {
  return (url, options, onEvent) => {
    return new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open(options.method, url)

      // Apply request headers
      for (const [key, value] of Object.entries(options.headers)) {
        xhr.setRequestHeader(key, value)
      }

      // Track how far we have consumed the responseText so we only process
      // new data on each onprogress call.
      let lastIndex = 0
      // Buffer for incomplete lines that span across onprogress events.
      let lineBuffer = ''

      const processChunk = (newText: string) => {
        // Prepend any leftover data from the previous chunk
        const text = lineBuffer + newText
        const lines = text.split('\n')
        // The last element is either an empty string (if text ended with \n)
        // or an incomplete line — either way, stash it for next time.
        lineBuffer = lines.pop() ?? ''

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

      xhr.onprogress = () => {
        const newText = xhr.responseText.substring(lastIndex)
        lastIndex = xhr.responseText.length
        processChunk(newText)
      }

      xhr.onload = () => {
        // Process any remaining buffered data that did not end with a newline
        if (lineBuffer.length > 0) {
          processChunk('\n')
        }

        if (xhr.status >= 200 && xhr.status < 300) {
          resolve()
        } else {
          reject(new Error(`HTTP ${xhr.status}: ${xhr.statusText}`))
        }
      }

      xhr.onerror = () => reject(new Error('Network error'))

      xhr.onabort = () => {
        const abortError = new Error('Aborted')
        abortError.name = 'AbortError'
        reject(abortError)
      }

      // Wire up the AbortSignal so callers can cancel the request
      if (options.signal.aborted) {
        xhr.abort()
        return
      }
      options.signal.addEventListener('abort', () => xhr.abort())

      xhr.send(options.body)
    })
  }
}
