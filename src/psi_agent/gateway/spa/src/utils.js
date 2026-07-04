import { marked } from 'marked'
import katex from 'katex'

// GFM tables + single-newline -> <br>. With breaks on, in-paragraph line
// breaks become semantic <br>, so the chat bubble no longer needs
// `white-space: pre-wrap` (which used to render marked's inter-tag
// newlines as stray blank lines).
marked.setOptions({ gfm: true, breaks: true })

export function renderMd(text) {
  if (!marked || !marked.parse) return htmlEscape(text)
  const macros = []
  const s = text
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, m) => { const i = macros.length; macros.push({ block: true, tex: m.trim() }); return `\x00MATH${i}\x00` })
    .replace(/\$([^$]+?)\$/g, (_, m) => { const i = macros.length; macros.push({ block: false, tex: m.trim() }); return `\x00MATH${i}\x00` })
  let html = marked.parse(s)
  macros.forEach((m, i) => {
    try {
      const rendered = katex.renderToString(m.tex, { displayMode: m.block, throwOnError: false })
      html = html.replace(`\x00MATH${i}\x00`, rendered)
    } catch (e) {
      html = html.replace(`\x00MATH${i}\x00`, `<code>${m.tex}</code>`)
    }
  })
  return html
}

export function htmlEscape(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

export function mimeType(name) {
  const ext = (name || '').split('.').pop().toLowerCase()
  const map = {
    png: 'image/png',
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    gif: 'image/gif',
    webp: 'image/webp',
    svg: 'image/svg+xml',
    pdf: 'application/pdf',
    txt: 'text/plain',
    json: 'application/json',
    html: 'text/html',
    css: 'text/css',
    js: 'text/javascript',
    py: 'text/x-python',
    zip: 'application/zip',
    gz: 'application/gzip',
    tar: 'application/x-tar',
    mp3: 'audio/mpeg',
    wav: 'audio/wav',
    ogg: 'audio/ogg',
    m4a: 'audio/mp4',
    flac: 'audio/flac',
    mp4: 'video/mp4',
    webm: 'video/webm',
    mov: 'video/quicktime',
    m4v: 'video/x-m4v',
  }
  return map[ext] || 'application/octet-stream'
}

const LS_ACTIVE = 'gw-active-ids'

export function saveActiveState(aiId, sessId) {
  localStorage.setItem(LS_ACTIVE, JSON.stringify({ aiId, sessId }))
}

export function loadActiveState() {
  try { return JSON.parse(localStorage.getItem(LS_ACTIVE)) || { aiId: null, sessId: null } }
  catch (_) { return { aiId: null, sessId: null } }
}

export function saveHistory(id, msgs) {
  localStorage.setItem('gw-hist-' + id, JSON.stringify(msgs.map(m => ({
    role: m.role,
    text: m.text,
    files: (m.files || []).map(f => ({ name: f.name, data: f.data })),
    stopped: m.stopped || false,
  }))))
}

export function loadHistory(id) {
  try { return JSON.parse(localStorage.getItem('gw-hist-' + id)) || [] }
  catch (_) { return [] }
}

export function clearHistory(id) {
  localStorage.removeItem('gw-hist-' + id)
}
