import { streamChat } from './api'
import { appendChatFilesToFormData } from './chatFiles'
import { readSSE } from './sse'
import type { ChatFile } from '../haitun-agent/model'

export type StreamHandlers = {
  onText?: (delta: string) => void
  onBlob?: (name: string, data: string) => void
  onReasoning?: (delta: string) => void
  onError?: (message: string) => void
}

/** POST multipart chat and stream assistant text/blobs into handlers. */
export async function streamSessionChat(
  sessionId: string,
  text: string,
  files: Array<File | ChatFile> = [],
  signal?: AbortSignal,
  handlers: StreamHandlers = {},
): Promise<{ text: string; blobs: ChatFile[] }> {
  const fd = new FormData()
  const chunks: { type: string; text?: string }[] = []
  if (text.trim()) chunks.push({ type: 'text', text: text.trim() })
  fd.append('chunks', JSON.stringify(chunks))
  appendChatFilesToFormData(fd, files)

  let full = ''
  const blobs: ChatFile[] = []
  const reader = await streamChat(sessionId, fd, signal)
  const cancelReader = () => {
    void reader.cancel().catch(() => {})
  }
  if (signal) {
    if (signal.aborted) {
      cancelReader()
      throw new DOMException('Aborted', 'AbortError')
    }
    signal.addEventListener('abort', cancelReader, { once: true })
  }
  try {
    for await (const chunk of readSSE(reader)) {
      if (signal?.aborted) {
        throw new DOMException('Aborted', 'AbortError')
      }
      if (chunk.type === 'text' && typeof chunk.text === 'string') {
        full += chunk.text
        handlers.onText?.(chunk.text)
      } else if (chunk.type === 'blob' && typeof chunk.name === 'string') {
        const data = typeof chunk.data === 'string' ? chunk.data : ''
        blobs.push({ name: chunk.name, data })
        handlers.onBlob?.(chunk.name, data)
      } else if (chunk.type === 'reasoning' && typeof chunk.text === 'string') {
        handlers.onReasoning?.(chunk.text)
      } else if (chunk.type === 'error' && typeof chunk.error === 'string') {
        handlers.onError?.(chunk.error)
        throw new Error(chunk.error)
      }
    }
    if (signal?.aborted) {
      throw new DOMException('Aborted', 'AbortError')
    }
    return { text: full, blobs }
  } finally {
    signal?.removeEventListener('abort', cancelReader)
  }
}
