import { streamChat } from './api'
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
  files: File[] = [],
  signal?: AbortSignal,
  handlers: StreamHandlers = {},
): Promise<{ text: string; blobs: ChatFile[] }> {
  const fd = new FormData()
  const chunks: { type: string; text?: string }[] = []
  if (text.trim()) chunks.push({ type: 'text', text: text.trim() })
  fd.append('chunks', JSON.stringify(chunks))
  for (const f of files) fd.append('file', f, f.name)

  let full = ''
  const blobs: ChatFile[] = []
  const reader = await streamChat(sessionId, fd, signal)
  for await (const chunk of readSSE(reader)) {
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
  return { text: full, blobs }
}
