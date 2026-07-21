import type { ChatFile, ChatMessage } from '../haitun-agent/model'
import { mimeType } from '../services/renderMd'

export { mimeType }

export function decodeBase64Utf8(data: string): string {
  const raw = data.includes(',') ? data.split(',')[1]! : data
  const binary = atob(raw)
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0))
  return new TextDecoder('utf-8', { fatal: false }).decode(bytes)
}

export function dataUrlForChatFile(file: ChatFile): string {
  const mime = mimeType(file.name)
  if (file.data.startsWith('data:')) return file.data
  return `data:${mime};base64,${file.data}`
}

export function downloadChatFile(file: ChatFile): void {
  const a = document.createElement('a')
  a.href = dataUrlForChatFile(file)
  a.download = file.name
  a.click()
}

/** Latest ChatFile per basename from message attachments (live SSE blobs). */
export function collectDeliverableFiles(
  deliverableNames: string[],
  messages: ChatMessage[],
): ChatFile[] {
  const byBase = new Map<string, ChatFile>()
  for (const msg of messages) {
    for (const f of msg.files ?? []) {
      const base = f.name.split(/[/\\]/).pop() || f.name
      byBase.set(base, f)
      byBase.set(f.name, f)
    }
  }
  const out: ChatFile[] = []
  const seen = new Set<string>()
  for (const name of deliverableNames) {
    const base = name.split(/[/\\]/).pop() || name
    const file = byBase.get(name) ?? byBase.get(base)
    if (!file) continue
    const key = file.name
    if (seen.has(key)) continue
    seen.add(key)
    out.push(file)
  }
  return out
}

export function findDeliverableFile(
  name: string,
  files: ChatFile[],
): ChatFile | undefined {
  const base = name.split(/[/\\]/).pop() || name
  return files.find((f) => f.name === name || f.name.split(/[/\\]/).pop() === base)
}
