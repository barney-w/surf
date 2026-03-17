/**
 * Unit tests for the native SSE adapter.
 *
 * These tests mock `expo/fetch` and exercise the stream-parsing, timeout,
 * and retry logic added to `createNativeSSEAdapter`.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { createNativeSSEAdapter } from '../sseAdapter'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Encode a string as a Uint8Array chunk, mimicking ReadableStream output. */
function encode(text: string): Uint8Array {
  return new TextEncoder().encode(text)
}

/** Build a mock ReadableStream reader from an array of string chunks. */
function mockReader(chunks: string[]) {
  let index = 0
  return {
    read: jest.fn(async () => {
      if (index >= chunks.length) return { done: true, value: undefined }
      return { done: false, value: encode(chunks[index++]) }
    }),
  }
}

/** Build a minimal mock Response whose body.getReader() returns `reader`. */
function mockResponse(reader: ReturnType<typeof mockReader>, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Internal Server Error',
    body: { getReader: () => reader },
  }
}

// ---------------------------------------------------------------------------
// Mock expo/fetch
// ---------------------------------------------------------------------------
const fetchMock = jest.fn()
jest.mock('expo/fetch', () => ({ fetch: fetchMock }))

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('createNativeSSEAdapter', () => {
  const defaultOptions = {
    method: 'POST' as const,
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
    signal: undefined as AbortSignal | undefined,
  }

  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('parses SSE events from a ReadableStream and invokes onEvent', async () => {
    const reader = mockReader([
      'data: {"type":"delta","content":"hello"}\n\ndata: {"type":"delta","content":" world"}\n\n',
    ])
    fetchMock.mockResolvedValue(mockResponse(reader))

    const events: any[] = []
    const adapter = createNativeSSEAdapter()
    await adapter('https://api.test/chat', defaultOptions, (e) => events.push(e))

    expect(events).toEqual([
      { type: 'delta', content: 'hello' },
      { type: 'delta', content: ' world' },
    ])
  })

  it('skips [DONE] sentinel events', async () => {
    const reader = mockReader([
      'data: {"type":"delta","content":"hi"}\ndata: [DONE]\n',
    ])
    fetchMock.mockResolvedValue(mockResponse(reader))

    const events: any[] = []
    const adapter = createNativeSSEAdapter()
    await adapter('https://api.test/chat', defaultOptions, (e) => events.push(e))

    expect(events).toHaveLength(1)
    expect(events[0]).toEqual({ type: 'delta', content: 'hi' })
  })

  it('skips malformed JSON without throwing', async () => {
    const reader = mockReader([
      'data: {"type":"delta","content":"ok"}\ndata: NOT-JSON\ndata: {"type":"done"}\n',
    ])
    fetchMock.mockResolvedValue(mockResponse(reader))

    const events: any[] = []
    const adapter = createNativeSSEAdapter()
    await adapter('https://api.test/chat', defaultOptions, (e) => events.push(e))

    expect(events).toEqual([
      { type: 'delta', content: 'ok' },
      { type: 'done' },
    ])
  })

  it('handles buffered partial lines across chunks', async () => {
    // The first chunk ends mid-line; the second chunk completes it.
    const reader = mockReader([
      'data: {"type":"del',
      'ta","content":"split"}\n',
    ])
    fetchMock.mockResolvedValue(mockResponse(reader))

    const events: any[] = []
    const adapter = createNativeSSEAdapter()
    await adapter('https://api.test/chat', defaultOptions, (e) => events.push(e))

    expect(events).toEqual([{ type: 'delta', content: 'split' }])
  })

  it('retries on transient fetch failures with exponential backoff', async () => {
    const reader = mockReader([
      'data: {"type":"ok"}\n',
    ])

    // Fail twice, succeed on third attempt
    fetchMock
      .mockRejectedValueOnce(new Error('network error'))
      .mockRejectedValueOnce(new Error('network error'))
      .mockResolvedValueOnce(mockResponse(reader))

    const events: any[] = []
    const adapter = createNativeSSEAdapter()

    // Run the adapter — it will await setTimeout-based delays, so we need
    // to advance fake timers.  We run the promise and flush timers in a loop.
    const promise = adapter('https://api.test/chat', defaultOptions, (e) => events.push(e))

    // Advance past first retry delay (2 s)
    await jest.advanceTimersByTimeAsync(2_000)
    // Advance past second retry delay (4 s)
    await jest.advanceTimersByTimeAsync(4_000)

    await promise

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(events).toEqual([{ type: 'ok' }])
  })

  it('throws after MAX_RETRIES exhausted', async () => {
    fetchMock.mockRejectedValue(new Error('persistent failure'))

    const adapter = createNativeSSEAdapter()
    const promise = adapter('https://api.test/chat', defaultOptions, jest.fn())

    // Advance through all retry delays (2 s + 4 s + 8 s)
    await jest.advanceTimersByTimeAsync(2_000)
    await jest.advanceTimersByTimeAsync(4_000)
    await jest.advanceTimersByTimeAsync(8_000)

    await expect(promise).rejects.toThrow('persistent failure')
    // 1 initial + 3 retries = 4 total
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('processes remaining buffer data after stream ends', async () => {
    // Stream ends with data still in the buffer (no trailing newline)
    const reader = mockReader([
      'data: {"type":"buffered"}',
    ])
    fetchMock.mockResolvedValue(mockResponse(reader))

    const events: any[] = []
    const adapter = createNativeSSEAdapter()
    await adapter('https://api.test/chat', defaultOptions, (e) => events.push(e))

    expect(events).toEqual([{ type: 'buffered' }])
  })
})
