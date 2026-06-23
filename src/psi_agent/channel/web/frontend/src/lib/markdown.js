// Minimal markdown renderer: paragraphs, ordered/unordered lists, **bold**, `code`.
export function renderMarkdown(src) {
  const lines = String(src || '').split('\n')
  const out = []
  let list = null

  const inline = (t) =>
    t
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')

  for (const line of lines) {
    const li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)$/)
    if (li) {
      const t = /^\s*\d/.test(line) ? 'ol' : 'ul'
      if (list && list !== t) { out.push('</' + list + '>'); list = null }
      if (!list) { list = t; out.push('<' + t + '>') }
      out.push('<li>' + inline(li[1]) + '</li>')
      continue
    }
    if (list) { out.push('</' + list + '>'); list = null }
    if (line.trim()) out.push('<p>' + inline(line) + '</p>')
  }
  if (list) out.push('</' + list + '>')
  return out.join('')
}
