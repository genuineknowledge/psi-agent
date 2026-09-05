import type { ChatFile, ChatMessage } from '../haitun-agent/model'
import { mimeType } from '../services/renderMd'
import { readWorkspaceFile, revealWorkspacePath } from '../services/api'

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

/** Absolute Windows / POSIX / UNC path — SEND markers are usually absolute. */
export function isAbsoluteFsPath(path: string): boolean {
  const p = path.trim()
  return /^([a-zA-Z]:[\\/]|\\\\|\/)/.test(p)
}

/** Join relative SEND path under workspace; leave absolute paths unchanged. */
export function resolveDeliverablePath(path: string, workspaceRoot = ''): string {
  const raw = path.trim()
  if (!raw) return raw
  if (isAbsoluteFsPath(raw)) return raw
  const root = workspaceRoot.replace(/[\\/]+$/, '')
  const rel = raw.replace(/^[\\/]+/, '')
  if (!root) return rel
  return `${root}/${rel}`.replace(/\\/g, '/')
}

/**
 * Ask Gateway to open the OS file manager at ``path`` (select file when possible).
 */
export async function revealDeliverableInFolder(
  path: string,
  workspaceRoot = '',
): Promise<void> {
  const full = resolveDeliverablePath(path, workspaceRoot)
  if (!full) {
    throw new Error('没有可打开的文件路径。')
  }
  await revealWorkspacePath(full)
}

/**
 * Ensure a chat/deliverable file has base64 ``data`` for preview/download.
 * After refresh, history only has ``path`` from ``[SEND:]`` — load via Gateway.
 *
 * **刻意为之**：不传 ``root`` 约束。SEND 路径来自本会话历史，可能在 workspace
 * 内，也可能在 ``Downloads/.psi/`` 等绝对位置；用 workspace root 会误 403。
 */
export async function ensureChatFileData(
  file: ChatFile,
  workspaceRoot = '',
): Promise<ChatFile> {
  if (file.data.trim()) return file
  const src = file.path?.trim()
  if (!src) {
    throw new Error('历史记录中没有该文件的路径，无法从磁盘读取预览。')
  }
  const full = resolveDeliverablePath(src, workspaceRoot)
  const res = await readWorkspaceFile(full, '')
  return {
    name: res.name || file.name,
    data: res.data,
    path: full,
  }
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
