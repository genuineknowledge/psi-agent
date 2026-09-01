<template>
  <Teleport to="body">
    <div class="preview-drawer-shell">
      <button class="preview-scrim" type="button" aria-label="关闭预览" @click="emit('close')"></button>
      <aside class="file-preview preview-drawer" role="dialog" aria-modal="true" aria-label="文件预览">
        <header class="preview-drawer-header">
          <div class="preview-title-wrap">
            <span class="material-symbols-outlined preview-title-icon">draft</span>
            <div class="preview-title" :title="file.name">{{ file.name }}</div>
          </div>
          <div class="preview-actions">
            <a
              class="preview-icon-btn"
              href="#"
              title="下载"
              @click.prevent="downloadFile"
            >
              <span class="material-symbols-outlined">download</span>
            </a>
            <button class="preview-icon-btn" type="button" title="关闭" @click="emit('close')">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>
        </header>
        <div class="preview-drawer-body">
          <div v-if="loading" class="preview-state">正在生成预览...</div>
          <div v-else-if="message" class="preview-state">{{ message }}</div>
          <div v-show="notice" class="preview-notice">{{ notice }}</div>
          <div ref="hostRef" class="preview-host" @click="onPreviewClick"></div>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { mimeType, renderMd } from '../utils.js'
import { downloadMatrixXlsx, matrixToTsv, tableToMatrix } from '../mdTable.js'

const props = defineProps({
  file: {
    type: Object,
    required: true,
    validator: (f) => f && typeof f.name === 'string' && typeof f.data === 'string',
  },
})

const emit = defineEmits(['close'])

const FALLBACK_MESSAGE = '文件过大或无法预览，请直接下载。'
const PARTIAL_NOTICE = '仅显示部分内容，请下载查看完整文件。'

const TEXT_CHAR_LIMIT = 1000000
const TEXT_LINE_LIMIT = 20000
const CSV_ROW_LIMIT = 1000
const TABLE_ROW_LIMIT = 1000
const TABLE_COL_LIMIT = 80
const SHEET_LIMIT = 5
const PDF_PAGE_LIMIT = 10
const MAX_PREVIEW_DECODED_BYTES = 50 * 1024 * 1024

const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp'])
const AUDIO_EXTS = new Set(['mp3', 'wav', 'ogg', 'm4a', 'flac'])
const VIDEO_EXTS = new Set(['mp4', 'webm', 'mov', 'm4v'])
const MARKDOWN_EXTS = new Set(['md', 'markdown'])
/** Document preview (rendered page), not CodeMirror source. */
const HTML_EXTS = new Set(['html', 'htm'])
const TEXT_EXTS = new Set([
  'txt', 'log', 'sql', 'py', 'js', 'mjs', 'cjs', 'ts', 'tsx',
  'jsx', 'vue', 'css', 'xml', 'yaml', 'yml', 'toml', 'ini', 'sh',
  'bash', 'zsh', 'fish', 'java', 'c', 'h', 'cpp', 'hpp', 'cs', 'go', 'rs', 'rb',
  'php', 'swift', 'kt', 'kts', 'scala', 'r', 'lua', 'dockerfile', 'gitignore',
])

const hostRef = ref(null)
const loading = ref(false)
const message = ref('')
const notice = ref('')

let objectUrl = ''
let editorView = null
let pptxPreviewer = null
let renderRun = 0

watch(
  () => props.file,
  () => renderPreview(),
  { immediate: true }
)

onBeforeUnmount(() => {
  renderRun += 1
  cleanup()
})

async function renderPreview() {
  const run = ++renderRun
  loading.value = true
  message.value = ''
  notice.value = ''
  await nextTick()
  cleanup()
  if (!hostRef.value) {
    loading.value = false
    return
  }

  try {
    const ext = extension(props.file.name)
    if (estimatedDecodedBytes(props.file.data) > MAX_PREVIEW_DECODED_BYTES) {
      showFallback()
      return
    }
    const bytes = base64ToBytes(props.file.data)

    if (ext === 'svg') renderSvg(decodeText(bytes), bytes)
    else if (IMAGE_EXTS.has(ext)) renderImage(bytes, ext)
    else if (AUDIO_EXTS.has(ext)) renderMedia(bytes, ext, 'audio')
    else if (VIDEO_EXTS.has(ext)) renderMedia(bytes, ext, 'video')
    else if (ext === 'json') await renderCode(formatJson(decodeText(bytes)), false)
    else if (ext === 'jsonl') await renderCode(formatJsonl(decodeText(bytes)), false)
    else if (MARKDOWN_EXTS.has(ext)) renderMarkdown(decodeText(bytes))
    else if (HTML_EXTS.has(ext)) renderHtmlDocument(decodeText(bytes))
    else if (TEXT_EXTS.has(ext)) await renderCode(decodeText(bytes), false)
    else if (ext === 'csv') await renderCsv(decodeText(bytes))
    else if (ext === 'pdf') await renderPdf(bytesToArrayBuffer(bytes), run)
    else if (ext === 'docx') await renderDocx(bytesToArrayBuffer(bytes))
    else if (ext === 'xls' || ext === 'xlsx') await renderWorkbook(bytesToArrayBuffer(bytes))
    else if (ext === 'pptx') await renderPptx(bytesToArrayBuffer(bytes))
    else showFallback()
  } catch (e) {
    showFallback()
  } finally {
    if (run === renderRun) loading.value = false
  }
}

function cleanup() {
  if (editorView) {
    editorView.destroy()
    editorView = null
  }
  if (pptxPreviewer && typeof pptxPreviewer.destroy === 'function') {
    pptxPreviewer.destroy()
    pptxPreviewer = null
  }
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl)
    objectUrl = ''
  }
  if (hostRef.value) hostRef.value.innerHTML = ''
}

function extension(name) {
  const base = (name || '').toLowerCase().split(/[?#]/)[0]
  const parts = base.split('.')
  if (parts.length < 2) return ''
  return parts.pop()
}

function base64ToBytes(data) {
  if (!data) throw new Error('empty file')
  return Uint8Array.from(atob(data), (c) => c.charCodeAt(0))
}

function estimatedDecodedBytes(data) {
  if (!data) return 0
  const normalized = data.replace(/\s/g, '')
  const padding = normalized.endsWith('==') ? 2 : normalized.endsWith('=') ? 1 : 0
  return Math.floor((normalized.length * 3) / 4) - padding
}

function bytesToArrayBuffer(bytes) {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
}

function base64ToBlob(bytes, ext) {
  return new Blob([bytes], { type: mimeType(ext) })
}

function downloadFile() {
  let url = ''
  try {
    const bytes = base64ToBytes(props.file.data)
    url = URL.createObjectURL(new Blob([bytes], { type: mimeType(props.file.name) }))
    const link = document.createElement('a')
    link.href = url
    link.download = props.file.name
    link.click()
  } finally {
    if (url) URL.revokeObjectURL(url)
  }
}

function decodeText(bytes) {
  return new TextDecoder('utf-8', { fatal: false }).decode(bytes)
}

function renderImage(bytes, ext) {
  const frame = document.createElement('div')
  frame.className = 'preview-image-frame'
  const img = document.createElement('img')
  objectUrl = URL.createObjectURL(base64ToBlob(bytes, ext))
  img.src = objectUrl
  img.alt = props.file.name
  img.className = 'preview-image'
  frame.append(img)
  hostRef.value.append(frame)
}

function renderSvg(text, bytes) {
  const frame = document.createElement('div')
  frame.className = 'preview-svg-frame'
  frame.innerHTML = text
  const svg = frame.querySelector('svg')
  if (svg) {
    svg.classList.add('preview-svg')
    hostRef.value.append(frame)
    return
  }

  const object = document.createElement('object')
  objectUrl = URL.createObjectURL(base64ToBlob(bytes, 'svg'))
  object.type = 'image/svg+xml'
  object.data = objectUrl
  object.className = 'preview-svg-object'
  hostRef.value.append(object)
}

function renderMedia(bytes, ext, kind) {
  const el = document.createElement(kind)
  objectUrl = URL.createObjectURL(base64ToBlob(bytes, ext))
  el.src = objectUrl
  el.controls = true
  el.className = kind === 'audio' ? 'preview-audio' : 'preview-video'
  hostRef.value.append(el)
}

function formatJson(text) {
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch (_) {
    return text
  }
}

function formatJsonl(text) {
  const lines = text.split(/\r?\n/)
  let formatted = false
  const output = lines.map((line) => {
    if (!line.trim()) return line
    try {
      formatted = true
      return JSON.stringify(JSON.parse(line), null, 2)
    } catch (_) {
      return line
    }
  })
  return formatted ? output.join('\n') : text
}

function boundedText(text) {
  let partial = false
  let next = text
  if (next.length > TEXT_CHAR_LIMIT) {
    next = next.slice(0, TEXT_CHAR_LIMIT)
    partial = true
  }
  const lines = next.split(/\r?\n/)
  if (lines.length > TEXT_LINE_LIMIT) {
    next = lines.slice(0, TEXT_LINE_LIMIT).join('\n')
    partial = true
  }
  return { text: next, partial }
}

async function renderCode(text, forcePartial) {
  const { EditorView, basicSetup } = await import('codemirror')
  const bounded = boundedText(text)
  if (bounded.partial || forcePartial) notice.value = PARTIAL_NOTICE
  editorView = new EditorView({
    doc: bounded.text,
    extensions: [
      basicSetup,
      EditorView.editable.of(false),
      EditorView.lineWrapping,
      EditorView.theme({
        '&': {
          height: '100%',
          backgroundColor: 'var(--md-bg)',
          color: 'var(--md-text-primary)',
          fontSize: '13px',
        },
        '.cm-scroller': {
          overflow: 'auto',
          fontFamily: '"JetBrains Mono", "SFMono-Regular", Consolas, monospace',
        },
        '.cm-gutters': {
          backgroundColor: 'var(--md-surface-container-high)',
          color: 'var(--md-text-secondary)',
          borderRightColor: 'var(--md-outline-variant)',
        },
      }),
    ],
    parent: hostRef.value,
  })
}

function renderMarkdown(text) {
  const bounded = boundedText(text)
  if (bounded.partial) notice.value = PARTIAL_NOTICE
  const article = document.createElement('article')
  article.className = 'preview-markdown'
  article.innerHTML = renderMd(bounded.text)
  hostRef.value.append(article)
}

/** Same table copy / Excel download as chat bubbles (md-table-card toolbar). */
async function onPreviewClick(e) {
  const btn = e.target.closest?.('[data-table-action]')
  if (!btn) return
  e.preventDefault()
  const card = btn.closest('[data-md-table]')
  const table = card?.querySelector('table')
  const matrix = tableToMatrix(table)
  if (!matrix.length) return
  const action = btn.getAttribute('data-table-action')
  if (action === 'copy') {
    const tsv = matrixToTsv(matrix)
    try {
      await navigator.clipboard.writeText(tsv)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = tsv
      ta.style.position = 'fixed'
      ta.style.left = '-9999px'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      ta.remove()
    }
    btn.classList.add('is-done')
    window.setTimeout(() => btn.classList.remove('is-done'), 1400)
    return
  }
  if (action === 'download') {
    btn.classList.add('is-busy')
    try {
      const stamp = new Date().toISOString().slice(0, 10)
      const base = String(props.file.name || 'table').replace(/\.[^.]+$/, '')
      await downloadMatrixXlsx(matrix, `${base}-${stamp}.xlsx`)
    } finally {
      btn.classList.remove('is-busy')
    }
  }
}

/**
 * Render HTML as a sandboxed document (blob URL iframe).
 * No allow-scripts / allow-same-origin — display only, no SPA escape.
 */
function renderHtmlDocument(text) {
  const bounded = boundedText(text)
  if (bounded.partial) notice.value = PARTIAL_NOTICE
  const frame = document.createElement('iframe')
  frame.className = 'preview-html-frame'
  frame.title = props.file.name || 'HTML preview'
  frame.setAttribute('sandbox', '')
  frame.setAttribute('referrerpolicy', 'no-referrer')
  objectUrl = URL.createObjectURL(new Blob([bounded.text], { type: 'text/html;charset=utf-8' }))
  frame.src = objectUrl
  hostRef.value.append(frame)
}

async function renderCsv(text) {
  const module = await import('papaparse')
  const Papa = module.default || module
  const result = Papa.parse(text, { skipEmptyLines: false })
  const rows = Array.isArray(result.data) ? result.data : []
  if (rows.length > CSV_ROW_LIMIT || rows.some((row) => Array.isArray(row) && row.length > TABLE_COL_LIMIT)) {
    notice.value = PARTIAL_NOTICE
  }
  renderTable(rows.slice(0, CSV_ROW_LIMIT).map((row) => normalizeRow(row).slice(0, TABLE_COL_LIMIT)))
}

async function renderWorkbook(arrayBuffer) {
  const XLSX = await import('xlsx')
  const workbook = XLSX.read(arrayBuffer, { type: 'array' })
  const sheetNames = workbook.SheetNames.slice(0, SHEET_LIMIT)
  if (workbook.SheetNames.length > SHEET_LIMIT) notice.value = PARTIAL_NOTICE
  if (!sheetNames.length) {
    showFallback()
    return
  }

  sheetNames.forEach((name) => {
    const section = document.createElement('section')
    section.className = 'sheet-preview'
    const title = document.createElement('div')
    title.className = 'sheet-title'
    title.textContent = name
    section.append(title)

    const rows = XLSX.utils.sheet_to_json(workbook.Sheets[name], {
      header: 1,
      blankrows: false,
      defval: '',
    })
    if (rows.length > TABLE_ROW_LIMIT || rows.some((row) => normalizeRow(row).length > TABLE_COL_LIMIT)) {
      notice.value = PARTIAL_NOTICE
    }
    section.append(createTable(rows.slice(0, TABLE_ROW_LIMIT).map((row) => normalizeRow(row).slice(0, TABLE_COL_LIMIT))))
    hostRef.value.append(section)
  })
}

function normalizeRow(row) {
  return Array.isArray(row) ? row : [row]
}

function renderTable(rows) {
  hostRef.value.append(createTable(rows))
}

function createTable(rows) {
  const wrap = document.createElement('div')
  wrap.className = 'preview-table-wrap'
  const table = document.createElement('table')
  table.className = 'preview-table'
  const tbody = document.createElement('tbody')
  rows.forEach((row, rowIndex) => {
    const tr = document.createElement('tr')
    row.forEach((cell) => {
      const td = document.createElement(rowIndex === 0 ? 'th' : 'td')
      td.textContent = cell == null ? '' : String(cell)
      tr.append(td)
    })
    tbody.append(tr)
  })
  table.append(tbody)
  wrap.append(table)
  return wrap
}

async function renderPdf(arrayBuffer, run) {
  const pdfjsLib = await import('pdfjs-dist/legacy/build/pdf.mjs')
  pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
    'pdfjs-dist/legacy/build/pdf.worker.mjs',
    import.meta.url
  ).toString()

  const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise
  const pageCount = Math.min(pdf.numPages, PDF_PAGE_LIMIT)
  if (pdf.numPages > PDF_PAGE_LIMIT) notice.value = PARTIAL_NOTICE

  const wrap = document.createElement('div')
  wrap.className = 'pdf-pages'
  hostRef.value.append(wrap)

  for (let i = 1; i <= pageCount; i += 1) {
    if (run !== renderRun) return
    const page = await pdf.getPage(i)
    const baseViewport = page.getViewport({ scale: 1 })
    const availableWidth = Math.max(320, (hostRef.value?.clientWidth || 720) - 32)
    const cssScale = Math.min(availableWidth / baseViewport.width, 1.7)
    const ratio = Math.min(window.devicePixelRatio || 1, 3)
    const viewport = page.getViewport({ scale: cssScale * ratio })
    const canvas = document.createElement('canvas')
    canvas.className = 'pdf-page'
    canvas.width = Math.ceil(viewport.width)
    canvas.height = Math.ceil(viewport.height)
    canvas.style.width = `${Math.ceil(baseViewport.width * cssScale)}px`
    canvas.style.height = `${Math.ceil(baseViewport.height * cssScale)}px`
    wrap.append(canvas)
    const context = canvas.getContext('2d')
    if (!context) throw new Error('Canvas unavailable')
    await page.render({ canvasContext: context, viewport }).promise
  }
}

async function renderDocx(arrayBuffer) {
  const { renderAsync } = await import('docx-preview')
  const shell = document.createElement('div')
  shell.className = 'office-preview-scroll docx-preview-scroll'
  const stage = document.createElement('div')
  stage.className = 'office-preview-stage docx-preview-host'
  shell.append(stage)
  hostRef.value.append(shell)
  await renderAsync(arrayBuffer, stage, undefined, {
    inWrapper: true,
    ignoreWidth: false,
    ignoreHeight: false,
    breakPages: true,
  })
}

async function renderPptx(arrayBuffer) {
  const module = await import('pptx-preview')
  const init = module.init
  if (typeof init !== 'function') {
    showFallback()
    return
  }
  const shell = document.createElement('div')
  shell.className = 'office-preview-scroll pptx-preview-scroll'
  const stage = document.createElement('div')
  stage.className = 'office-preview-stage pptx-preview-host pptx-preview-stage'
  shell.append(stage)
  hostRef.value.append(shell)
  pptxPreviewer = init(stage, { width: 960, height: 540 })
  await pptxPreviewer.preview(arrayBuffer)
}

function showFallback() {
  cleanup()
  message.value = FALLBACK_MESSAGE
}
</script>

<style scoped>
.preview-drawer-shell {
  position: fixed;
  inset: 0;
  z-index: 130;
  pointer-events: none;
}

.preview-scrim {
  position: absolute;
  inset: 0;
  border: none;
  background: rgba(0, 0, 0, 0.28);
  cursor: default;
  pointer-events: auto;
}

.file-preview {
  position: fixed;
  inset: 0 0 0 auto;
  width: min(84vw, 1080px);
  max-width: 100vw;
  border: 1px solid var(--md-outline-variant);
  border-right: none;
  border-radius: 28px 0 0 28px;
  background: var(--md-surface-container-low);
  box-shadow: var(--md-elevation-3);
  overflow: hidden;
  pointer-events: auto;
  display: flex;
  flex-direction: column;
}

.preview-drawer-header {
  flex: 0 0 auto;
  min-height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px 12px 20px;
  background: var(--md-surface-container-high);
  border-bottom: 1px solid var(--md-outline-variant);
}

.preview-title-wrap {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
}

.preview-title-icon {
  flex: 0 0 auto;
  color: var(--md-primary);
  font-size: 22px;
}

.preview-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--md-text-primary);
  font-size: 15px;
  font-weight: 500;
}

.preview-actions {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.preview-icon-btn {
  width: 40px;
  height: 40px;
  border: none;
  border-radius: var(--md-shape-full);
  background: transparent;
  color: var(--md-text-secondary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  text-decoration: none;
}

.preview-icon-btn:hover {
  background: rgba(128, 128, 128, var(--md-state-hover));
  color: var(--md-primary);
}

.preview-icon-btn .material-symbols-outlined {
  font-size: 20px;
}

.preview-drawer-body {
  min-height: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.preview-state,
.preview-notice {
  flex: 0 0 auto;
  padding: 10px 16px;
  font-size: 13px;
  color: var(--md-text-secondary);
}

.preview-notice {
  border-bottom: 1px solid var(--md-outline-variant);
  color: var(--md-primary);
}

.preview-host {
  min-height: 0;
  flex: 1;
  overflow: auto;
  background: var(--md-bg);
}

.preview-host:empty {
  display: none;
}

.preview-host :deep(.cm-editor) {
  height: 100%;
  min-height: 280px;
}

.preview-host :deep(.preview-image-frame) {
  min-height: 100%;
  box-sizing: border-box;
  display: grid;
  place-items: center;
  padding: 24px;
}

.preview-host :deep(.preview-image) {
  display: block;
  max-width: 100%;
  max-height: calc(100vh - 136px);
  width: auto;
  height: auto;
  object-fit: contain;
}

.preview-host :deep(.preview-svg-frame) {
  min-height: 100%;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    linear-gradient(45deg, color-mix(in srgb, var(--md-surface) 80%, transparent) 25%, transparent 25%),
    linear-gradient(-45deg, color-mix(in srgb, var(--md-surface) 80%, transparent) 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, color-mix(in srgb, var(--md-surface) 80%, transparent) 75%),
    linear-gradient(-45deg, transparent 75%, color-mix(in srgb, var(--md-surface) 80%, transparent) 75%);
  background-size: 24px 24px;
  background-position: 0 0, 0 12px, 12px -12px, -12px 0;
}

.preview-host :deep(.preview-svg) {
  display: block;
  max-width: 100%;
  max-height: calc(100vh - 136px);
  height: auto;
}

.preview-host :deep(.preview-svg-object) {
  display: block;
  width: 100%;
  min-height: calc(100vh - 96px);
  border: none;
  background: #fff;
}

.preview-host :deep(.preview-audio),
.preview-host :deep(.preview-video) {
  display: block;
  width: 100%;
  max-height: 440px;
  background: #000;
}

.preview-host :deep(.preview-audio) {
  margin: 12px;
  width: calc(100% - 24px);
  background: transparent;
}

.preview-host :deep(.preview-table-wrap) {
  width: calc(100% - 24px);
  max-width: 100%;
  overflow: auto;
  margin: 12px;
  border: 1px solid var(--md-outline-variant);
  border-radius: 12px;
  background: var(--md-surface-container);
  box-shadow: var(--md-elevation-1);
}

.preview-host :deep(.preview-table) {
  border-collapse: separate;
  border-spacing: 0;
  inline-size: max-content;
  min-width: 100%;
  font-size: 12px;
  color: var(--md-text-primary);
}

.preview-host :deep(.preview-table th),
.preview-host :deep(.preview-table td) {
  border-right: 1px solid var(--md-outline-variant);
  border-bottom: 1px solid var(--md-outline-variant);
  padding: 0 16px;
  height: 44px;
  max-width: 360px;
  min-width: 96px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: top;
  text-align: left;
}

.preview-host :deep(.preview-table th) {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--md-surface-container-high);
  color: var(--md-text-secondary);
  font-weight: 600;
}

.preview-host :deep(.preview-table tr:last-child td) {
  border-bottom: none;
}

.preview-host :deep(.preview-table th:last-child),
.preview-host :deep(.preview-table td:last-child) {
  border-right: none;
}

.preview-host :deep(.sheet-preview + .sheet-preview) {
  border-top: 1px solid var(--md-outline-variant);
}

.preview-host :deep(.sheet-title) {
  position: sticky;
  top: 0;
  z-index: 2;
  padding: 10px 16px;
  background: var(--md-surface-container-high);
  color: var(--md-text-primary);
  font-size: 13px;
  font-weight: 600;
  border-bottom: 1px solid var(--md-outline-variant);
}

.preview-host :deep(.pdf-pages) {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  align-items: center;
}

.preview-host :deep(.pdf-page) {
  max-width: 100%;
  height: auto;
  background: #fff;
  box-shadow: var(--md-elevation-1);
}

.preview-host :deep(.preview-html-frame) {
  display: block;
  width: 100%;
  height: calc(100vh - 96px);
  min-height: 360px;
  border: none;
  background: #fff;
}

.preview-host :deep(.preview-markdown) {
  max-width: 880px;
  margin: 0 auto;
  padding: 28px 32px 48px;
  color: var(--md-text-primary);
  font-size: 14px;
  line-height: 1.7;
}

.preview-host :deep(.preview-markdown h1),
.preview-host :deep(.preview-markdown h2),
.preview-host :deep(.preview-markdown h3) {
  margin: 1.2em 0 0.55em;
  line-height: 1.25;
}

.preview-host :deep(.preview-markdown h1:first-child),
.preview-host :deep(.preview-markdown h2:first-child),
.preview-host :deep(.preview-markdown h3:first-child) {
  margin-top: 0;
}

.preview-host :deep(.preview-markdown p),
.preview-host :deep(.preview-markdown ul),
.preview-host :deep(.preview-markdown ol),
.preview-host :deep(.preview-markdown blockquote),
.preview-host :deep(.preview-markdown pre) {
  margin: 0 0 1em;
}

.preview-host :deep(.preview-markdown ul),
.preview-host :deep(.preview-markdown ol) {
  padding-left: 1.5em;
}

.preview-host :deep(.preview-markdown pre) {
  overflow: auto;
  padding: 12px;
  border-radius: 8px;
  background: var(--md-surface-container-high);
}

.preview-host :deep(.preview-markdown code) {
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
}

/* Match chat-bubble GFM tables: one continuous card + toolbar strip */
.preview-host :deep(.preview-markdown .md-table-card) {
  margin: 0.9em 0 1.2em;
  border: 1px solid var(--md-outline-variant);
  border-radius: 12px;
  background: var(--md-surface-container, var(--md-surface-container-high));
  overflow: hidden;
}

.preview-host :deep(.preview-markdown .md-table-toolbar) {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  padding: 4px 6px;
  border-bottom: 1px solid var(--md-outline-variant);
  background: color-mix(in srgb, var(--md-surface) 70%, var(--md-surface-container-high));
}

.preview-host :deep(.preview-markdown .md-table-action) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--md-text-secondary);
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
  padding: 6px 8px;
  transition: background 0.15s, color 0.15s;
}

.preview-host :deep(.preview-markdown .md-table-action:hover) {
  background: rgba(128, 128, 128, var(--md-state-hover));
  color: var(--md-text-primary);
}

.preview-host :deep(.preview-markdown .md-table-action.is-done) {
  color: var(--md-primary);
}

.preview-host :deep(.preview-markdown .md-table-action.is-busy) {
  opacity: 0.55;
  pointer-events: none;
}

.preview-host :deep(.preview-markdown .md-table-action .material-symbols-outlined) {
  font-size: 16px;
}

.preview-host :deep(.preview-markdown .md-table-scroll) {
  width: 100%;
  overflow-x: auto;
}

.preview-host :deep(.preview-markdown .md-table-card table) {
  display: table;
  border-collapse: collapse;
  margin: 0;
  width: 100%;
  max-width: 100%;
  table-layout: fixed;
  font-size: 0.92em;
  letter-spacing: 0.005em;
  border: none;
}

.preview-host :deep(.preview-markdown .md-table-card th),
.preview-host :deep(.preview-markdown .md-table-card td) {
  border: 1px solid var(--md-outline-variant);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
  min-width: 0;
  line-height: 1.55;
}

.preview-host :deep(.preview-markdown .md-table-card th) {
  background: var(--md-surface-container-highest, var(--md-surface-container-high));
  font-weight: 650;
  color: var(--md-text-primary);
}

.preview-host :deep(.preview-markdown .md-table-card tr:nth-child(even) td) {
  background: color-mix(in srgb, var(--md-surface-container-high) 45%, transparent);
}

.preview-host :deep(.office-preview-scroll) {
  min-height: 100%;
  overflow: auto;
  padding: 24px;
  background: color-mix(in srgb, var(--md-surface) 72%, var(--md-bg));
}

.preview-host :deep(.office-preview-stage) {
  width: max-content;
  max-width: none;
  margin: 0 auto;
  background: #fff;
  color: #111;
}

.preview-host :deep(.docx-preview-scroll) {
  display: block;
}

.preview-host :deep(.docx-preview-host .docx-wrapper) {
  padding: 0;
  background: transparent;
}

.preview-host :deep(.docx-preview-host .docx) {
  margin: 0 auto 16px;
  box-shadow: var(--md-elevation-2);
}

.preview-host :deep(.docx-preview-host img) {
  max-width: none;
}

.preview-host :deep(.pptx-preview-scroll) {
  display: flex;
  align-items: flex-start;
}

.preview-host :deep(.pptx-preview-host) {
  min-width: 960px;
  min-height: 540px;
  overflow: visible;
}

.preview-host :deep(.pptx-preview-host img),
.preview-host :deep(.pptx-preview-host svg),
.preview-host :deep(.pptx-preview-host canvas) {
  max-width: none;
}

@media (max-width: 768px) {
  .file-preview {
    width: 100%;
    border-radius: 0;
  }

  .preview-drawer-header {
    min-height: 56px;
    padding: 8px 10px 8px 14px;
  }

  .preview-host :deep(.preview-markdown) {
    padding: 20px 16px 36px;
  }

  .preview-host :deep(.office-preview-scroll) {
    padding: 12px;
  }
}
</style>
