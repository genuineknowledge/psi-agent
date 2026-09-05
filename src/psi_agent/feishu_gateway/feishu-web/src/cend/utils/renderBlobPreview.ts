/**
 * In-page blob preview (宝箱 / chat drawer), aligned with spa v1 FilePreview.
 * Heavy libs (docx / pdf / pptx / papaparse / codemirror) load via dynamic import().
 */

import { renderMd, mimeType } from '../services/renderMd'

const FALLBACK = '此格式暂不支持页内预览，请下载后查看'
const PARTIAL = '文件较大或页数较多，仅显示部分预览'
const MAX_BYTES = 50 * 1024 * 1024
const PDF_PAGE_LIMIT = 10
const TEXT_CHAR_LIMIT = 200_000
const TEXT_LINE_LIMIT = 4_000
const CSV_ROW_LIMIT = 500
const TABLE_ROW_LIMIT = 200
const TABLE_COL_LIMIT = 40
const SHEET_LIMIT = 5

const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp'])
const AUDIO_EXTS = new Set(['mp3', 'wav', 'ogg', 'm4a', 'flac'])
const VIDEO_EXTS = new Set(['mp4', 'webm', 'mov', 'm4v'])
const MARKDOWN_EXTS = new Set(['md', 'markdown'])
const HTML_EXTS = new Set(['html', 'htm'])
const TEXT_EXTS = new Set([
  'txt', 'log', 'sql', 'py', 'js', 'mjs', 'cjs', 'ts', 'tsx',
  'jsx', 'vue', 'css', 'xml', 'yaml', 'yml', 'toml', 'ini', 'sh',
  'bash', 'zsh', 'fish', 'java', 'c', 'h', 'cpp', 'hpp', 'cs', 'go', 'rs', 'rb',
  'php', 'swift', 'kt', 'kts', 'scala', 'r', 'lua', 'dockerfile', 'gitignore',
])

export type BlobPreviewFile = { name: string; data: string }

export type BlobPreviewHandle = {
  cleanup: () => void
  notice?: string
  error?: string
}

export function extensionOf(name: string): string {
  const base = (name || '').toLowerCase().split(/[?#]/)[0]
  const parts = base.split('.')
  if (parts.length < 2) return ''
  return parts.pop() || ''
}

/** Formats we attempt to render in-page (宝箱 + chat). */
export function isBlobPreviewable(name: string): boolean {
  const ext = extensionOf(name)
  if (!ext) return false
  return (
    IMAGE_EXTS.has(ext)
    || ext === 'svg'
    || AUDIO_EXTS.has(ext)
    || VIDEO_EXTS.has(ext)
    || MARKDOWN_EXTS.has(ext)
    || HTML_EXTS.has(ext)
    || TEXT_EXTS.has(ext)
    || ext === 'json'
    || ext === 'jsonl'
    || ext === 'csv'
    || ext === 'pdf'
    || ext === 'docx'
    || ext === 'xls'
    || ext === 'xlsx'
    || ext === 'pptx'
  )
}

function base64ToBytes(data: string): Uint8Array {
  if (!data) throw new Error('empty file')
  const raw = data.includes(',') ? data.split(',')[1]! : data
  return Uint8Array.from(atob(raw.replace(/\s/g, '')), (c) => c.charCodeAt(0))
}

function estimatedDecodedBytes(data: string): number {
  if (!data) return 0
  const normalized = (data.includes(',') ? data.split(',')[1]! : data).replace(/\s/g, '')
  const padding = normalized.endsWith('==') ? 2 : normalized.endsWith('=') ? 1 : 0
  return Math.floor((normalized.length * 3) / 4) - padding
}

function bytesToArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
}

function decodeText(bytes: Uint8Array): string {
  return new TextDecoder('utf-8', { fatal: false }).decode(bytes)
}

function boundedText(text: string): { text: string; partial: boolean } {
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

function formatJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

function formatJsonl(text: string): string {
  const lines = text.split(/\r?\n/)
  let formatted = false
  const output = lines.map((line) => {
    if (!line.trim()) return line
    try {
      formatted = true
      return JSON.stringify(JSON.parse(line), null, 2)
    } catch {
      return line
    }
  })
  return formatted ? output.join('\n') : text
}

function normalizeRow(row: unknown): unknown[] {
  return Array.isArray(row) ? row : [row]
}

function createTable(rows: unknown[][]): HTMLElement {
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

/**
 * Render ``file`` into ``host``. Caller must invoke returned ``cleanup``.
 */
export async function renderBlobPreview(
  host: HTMLElement,
  file: BlobPreviewFile,
): Promise<BlobPreviewHandle> {
  let objectUrl = ''
  let editorView: { destroy: () => void } | null = null
  let pptxPreviewer: { preview: (b: ArrayBuffer) => Promise<void>; destroy?: () => void } | null = null
  let notice: string | undefined

  const cleanup = () => {
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
    host.replaceChildren()
  }

  const fail = (msg = FALLBACK): BlobPreviewHandle => {
    cleanup()
    return { cleanup, error: msg }
  }

  try {
    const ext = extensionOf(file.name)
    if (estimatedDecodedBytes(file.data) > MAX_BYTES) return fail()

    const bytes = base64ToBytes(file.data)

    if (ext === 'svg') {
      const text = decodeText(bytes)
      const frame = document.createElement('div')
      frame.className = 'preview-svg-frame'
      frame.innerHTML = text
      const svg = frame.querySelector('svg')
      if (svg) {
        svg.classList.add('preview-svg')
        host.append(frame)
      } else {
        const object = document.createElement('object')
        objectUrl = URL.createObjectURL(new Blob([bytesToArrayBuffer(bytes)], { type: 'image/svg+xml' }))
        object.type = 'image/svg+xml'
        object.data = objectUrl
        object.className = 'preview-svg-object'
        host.append(object)
      }
      return { cleanup, notice }
    }

    if (IMAGE_EXTS.has(ext)) {
      const frame = document.createElement('div')
      frame.className = 'preview-image-frame'
      const img = document.createElement('img')
      objectUrl = URL.createObjectURL(new Blob([bytesToArrayBuffer(bytes)], { type: mimeType(file.name) }))
      img.src = objectUrl
      img.alt = file.name
      img.className = 'preview-image artifact-preview-image'
      frame.append(img)
      host.append(frame)
      return { cleanup, notice }
    }

    if (AUDIO_EXTS.has(ext) || VIDEO_EXTS.has(ext)) {
      const kind = AUDIO_EXTS.has(ext) ? 'audio' : 'video'
      const el = document.createElement(kind)
      objectUrl = URL.createObjectURL(new Blob([bytesToArrayBuffer(bytes)], { type: mimeType(file.name) }))
      el.src = objectUrl
      el.controls = true
      el.className = kind === 'audio' ? 'preview-audio' : 'preview-video'
      host.append(el)
      return { cleanup, notice }
    }

    if (MARKDOWN_EXTS.has(ext)) {
      const bounded = boundedText(decodeText(bytes))
      if (bounded.partial) notice = PARTIAL
      const article = document.createElement('article')
      article.className = 'file-preview-md'
      article.innerHTML = renderMd(bounded.text)
      host.append(article)
      return { cleanup, notice }
    }

    if (HTML_EXTS.has(ext)) {
      const bounded = boundedText(decodeText(bytes))
      if (bounded.partial) notice = PARTIAL
      const iframe = document.createElement('iframe')
      iframe.className = 'file-preview-html'
      iframe.title = file.name || 'HTML preview'
      iframe.setAttribute('sandbox', '')
      iframe.setAttribute('referrerpolicy', 'no-referrer')
      objectUrl = URL.createObjectURL(new Blob([bounded.text], { type: 'text/html;charset=utf-8' }))
      iframe.src = objectUrl
      host.append(iframe)
      return { cleanup, notice }
    }

    if (ext === 'json' || ext === 'jsonl' || TEXT_EXTS.has(ext)) {
      let text = decodeText(bytes)
      if (ext === 'json') text = formatJson(text)
      if (ext === 'jsonl') text = formatJsonl(text)
      const bounded = boundedText(text)
      if (bounded.partial) notice = PARTIAL
      try {
        const { EditorView, basicSetup } = await import('codemirror')
        editorView = new EditorView({
          doc: bounded.text,
          extensions: [
            basicSetup,
            EditorView.editable.of(false),
            EditorView.lineWrapping,
            EditorView.theme({
              '&': {
                height: '100%',
                minHeight: '240px',
                backgroundColor: '#f7f9fb',
                color: '#172432',
                fontSize: '13px',
              },
              '.cm-scroller': {
                overflow: 'auto',
                fontFamily: '"JetBrains Mono", "SFMono-Regular", Consolas, monospace',
              },
              '.cm-gutters': {
                backgroundColor: '#eef2f6',
                color: '#617079',
                borderRightColor: '#d5dde6',
              },
            }),
          ],
          parent: host,
        })
      } catch {
        const pre = document.createElement('pre')
        pre.className = 'artifact-preview-text'
        pre.textContent = bounded.text
        host.append(pre)
      }
      return { cleanup, notice }
    }

    if (ext === 'csv') {
      const module = await import('papaparse')
      const Papa = (module as { default?: typeof import('papaparse') }).default ?? module
      const result = Papa.parse(decodeText(bytes), { skipEmptyLines: false })
      const rows = Array.isArray(result.data) ? result.data : []
      if (
        rows.length > CSV_ROW_LIMIT
        || rows.some((row) => Array.isArray(row) && row.length > TABLE_COL_LIMIT)
      ) {
        notice = PARTIAL
      }
      host.append(
        createTable(
          rows
            .slice(0, CSV_ROW_LIMIT)
            .map((row) => normalizeRow(row).slice(0, TABLE_COL_LIMIT)),
        ),
      )
      return { cleanup, notice }
    }

    if (ext === 'xls' || ext === 'xlsx') {
      const XLSX = await import('xlsx')
      const workbook = XLSX.read(bytesToArrayBuffer(bytes), { type: 'array' })
      const sheetNames = workbook.SheetNames.slice(0, SHEET_LIMIT)
      if (workbook.SheetNames.length > SHEET_LIMIT) notice = PARTIAL
      if (!sheetNames.length) return fail()
      for (const name of sheetNames) {
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
        }) as unknown[][]
        if (
          rows.length > TABLE_ROW_LIMIT
          || rows.some((row) => normalizeRow(row).length > TABLE_COL_LIMIT)
        ) {
          notice = PARTIAL
        }
        section.append(
          createTable(
            rows
              .slice(0, TABLE_ROW_LIMIT)
              .map((row) => normalizeRow(row).slice(0, TABLE_COL_LIMIT)),
          ),
        )
        host.append(section)
      }
      return { cleanup, notice }
    }

    if (ext === 'pdf') {
      const pdfjsLib = await import('pdfjs-dist/legacy/build/pdf.mjs')
      pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
        'pdfjs-dist/legacy/build/pdf.worker.mjs',
        import.meta.url,
      ).toString()
      const pdf = await pdfjsLib.getDocument({ data: bytesToArrayBuffer(bytes) }).promise
      const pageCount = Math.min(pdf.numPages, PDF_PAGE_LIMIT)
      if (pdf.numPages > PDF_PAGE_LIMIT) notice = PARTIAL
      const wrap = document.createElement('div')
      wrap.className = 'pdf-pages'
      host.append(wrap)
      for (let i = 1; i <= pageCount; i += 1) {
        const page = await pdf.getPage(i)
        const baseViewport = page.getViewport({ scale: 1 })
        const availableWidth = Math.max(320, (host.clientWidth || 720) - 32)
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
        await page.render({ canvas, viewport }).promise
      }
      return { cleanup, notice }
    }

    if (ext === 'docx') {
      const { renderAsync } = await import('docx-preview')
      const shell = document.createElement('div')
      shell.className = 'office-preview-scroll docx-preview-scroll'
      const stage = document.createElement('div')
      stage.className = 'office-preview-stage docx-preview-host'
      shell.append(stage)
      host.append(shell)
      // ignoreWidth: drop Word page width (~794px) so the drawer can own layout.
      // Page *margins* are still absolute lengths — CSS overrides them so the
      // body fills the panel instead of a skinny centered column.
      await renderAsync(bytesToArrayBuffer(bytes), stage, undefined, {
        inWrapper: true,
        ignoreWidth: true,
        ignoreHeight: true,
        breakPages: true,
      })
      return { cleanup, notice }
    }

    if (ext === 'pptx') {
      const module = await import('pptx-preview')
      const init = (module as { init?: (el: HTMLElement, opts: object) => { preview: (b: ArrayBuffer) => Promise<void>; destroy?: () => void } }).init
      if (typeof init !== 'function') return fail()
      const shell = document.createElement('div')
      shell.className = 'office-preview-scroll pptx-preview-scroll'
      const stage = document.createElement('div')
      stage.className = 'office-preview-stage pptx-preview-host pptx-preview-stage'
      shell.append(stage)
      host.append(shell)
      pptxPreviewer = init(stage, { width: 960, height: 540 })
      await pptxPreviewer.preview(bytesToArrayBuffer(bytes))
      return { cleanup, notice }
    }

    return fail()
  } catch {
    return fail()
  }
}
