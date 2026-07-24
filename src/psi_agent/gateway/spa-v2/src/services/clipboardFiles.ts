/** Extract File objects from a paste/drop DataTransfer (any kind, not only images). */

const MIME_EXT: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/gif': 'gif',
  'image/webp': 'webp',
  'image/bmp': 'bmp',
  'application/pdf': 'pdf',
}

function pasteFileName(type: string): string {
  const ext = MIME_EXT[type] || (type.includes('/') ? type.split('/')[1]!.replace(/[^a-z0-9]/gi, '') : 'bin') || 'bin'
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  return `paste-${stamp}.${ext}`
}

function normalizeClipboardFile(file: File): File {
  if (file.name && file.name.trim()) return file
  return new File([file], pasteFileName(file.type || 'application/octet-stream'), {
    type: file.type || 'application/octet-stream',
    lastModified: file.lastModified,
  })
}

/** Prefer ``files`` list; also scan ``items`` for file/image entries (screenshots). */
export function filesFromClipboard(data: DataTransfer | null | undefined): File[] {
  if (!data) return []
  const out: File[] = []
  const seen = new Set<string>()

  const push = (raw: File | null) => {
    if (!raw) return
    const file = normalizeClipboardFile(raw)
    const key = `${file.name}\0${file.size}\0${file.type}\0${file.lastModified}`
    if (seen.has(key)) return
    seen.add(key)
    out.push(file)
  }

  if (data.files?.length) {
    for (const file of Array.from(data.files)) push(file)
  }
  if (data.items?.length) {
    for (const item of Array.from(data.items)) {
      if (item.kind === 'file') push(item.getAsFile())
    }
  }
  return out
}
