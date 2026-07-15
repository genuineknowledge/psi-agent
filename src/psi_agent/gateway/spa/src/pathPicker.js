export function filterPickerEntries(entries, query) {
  const q = (query || '').trim().toLowerCase()
  if (!q || !Array.isArray(entries)) return entries
  return entries.filter(e => typeof e.name === 'string' && e.name.toLowerCase().includes(q))
}

export function sortPickerEntries(entries) {
  if (!Array.isArray(entries)) return []
  return [...entries].sort((a, b) => {
    const ak = a.kind === 'directory' ? 0 : 1
    const bk = b.kind === 'directory' ? 0 : 1
    if (ak !== bk) return ak - bk
    return String(a.name).localeCompare(String(b.name), undefined, { sensitivity: 'base' })
  })
}

export function buildSavePath(directory, fileName) {
  const dir = (directory || '').replace(/\\/g, '/').replace(/\/+$/, '')
  const name = (fileName || '').trim().replace(/^[/\\]+/, '')
  if (!dir) return name
  if (!name) return dir
  return `${dir}/${name}`
}
