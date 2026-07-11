import { marked } from 'marked'
import katex from 'katex'
import hljs from 'highlight.js/lib/common'

// GFM tables + single-newline -> <br>. With breaks on, in-paragraph line
// breaks become semantic <br>, so the chat bubble no longer needs
// `white-space: pre-wrap` (which used to render marked's inter-tag
// newlines as stray blank lines).
marked.setOptions({ gfm: true, breaks: true })

// 代码块语法高亮：用 highlight.js 覆盖 marked 的 code renderer，输出带
// `hljs language-xxx` class 的 <pre><code>，配合 styles/highlight.css 主题着色。
// 内联代码（inline code / 反引号）不走高亮，只由 CSS 加背景片区分，保持轻量。
// 语言标注无效或高亮抛错时回退到纯转义文本，绝不因高亮失败而丢内容。
function highlightCode(code, lang) {
  const language = hljs.getLanguage(lang) ? lang : null
  try {
    const out = language
      ? hljs.highlight(code, { language, ignoreIllegals: true })
      : hljs.highlightAuto(code)
    return { html: out.value, language: out.language || language || '' }
  } catch (e) {
    return { html: htmlEscape(code), language: '' }
  }
}

const markedRenderer = new marked.Renderer()
markedRenderer.code = function ({ text, lang }) {
  const { html, language } = highlightCode(text, (lang || '').trim())
  const cls = language ? ` class="hljs language-${language}"` : ' class="hljs"'
  return `<pre><code${cls}>${html}</code></pre>`
}
marked.use({ renderer: markedRenderer })

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
const LS_WORKSPACES = 'gw-workspaces'
const LS_SELECTED_WS = 'gw-selected-workspace'
const LS_COLLAPSED_WS = 'gw-collapsed-workspaces'

export function saveActiveState(aiId, sessId, workspacePath) {
  const prev = loadActiveState()
  localStorage.setItem(LS_ACTIVE, JSON.stringify({
    aiId,
    sessId,
    workspacePath: workspacePath !== undefined ? workspacePath : prev.workspacePath ?? null,
  }))
}

export function loadActiveState() {
  try {
    const data = JSON.parse(localStorage.getItem(LS_ACTIVE)) || {}
    return {
      aiId: data.aiId ?? null,
      sessId: data.sessId ?? null,
      workspacePath: data.workspacePath ?? null,
    }
  } catch (_) {
    return { aiId: null, sessId: null, workspacePath: null }
  }
}

export function loadRegisteredWorkspaces() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_WORKSPACES) || '[]')
    return Array.isArray(raw) ? raw.filter(p => typeof p === 'string') : []
  } catch (_) {
    return []
  }
}

export function saveRegisteredWorkspaces(paths) {
  localStorage.setItem(LS_WORKSPACES, JSON.stringify(paths))
}

export function loadSelectedWorkspace() {
  try {
    return localStorage.getItem(LS_SELECTED_WS) || ''
  } catch (_) {
    return ''
  }
}

export function saveSelectedWorkspace(path) {
  if (path) localStorage.setItem(LS_SELECTED_WS, path)
  else localStorage.removeItem(LS_SELECTED_WS)
}

export function loadCollapsedWorkspaces() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_COLLAPSED_WS) || '[]')
    return Array.isArray(raw) ? raw.filter(p => typeof p === 'string') : []
  } catch (_) {
    return []
  }
}

export function saveCollapsedWorkspaces(paths) {
  localStorage.setItem(LS_COLLAPSED_WS, JSON.stringify(paths))
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
