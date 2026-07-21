import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Download, FileText, X } from 'lucide-react'
import { mimeType, renderMd } from '../services/renderMd'
import { downloadMatrixXlsx, matrixToTsv, tableToMatrix } from '../services/mdTable'
import type { ChatFile } from '../haitun-agent/model'

function decodeBase64Text(data: string): string {
  const raw = data.includes(',') ? data.split(',')[1] : data
  const bin = atob(raw)
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0))
  return new TextDecoder('utf-8', { fatal: false }).decode(bytes)
}

function extOf(name: string) {
  return (name.split('.').pop() || '').toLowerCase()
}

/**
 * In-app preview drawer for chat blobs — MD (rendered) + HTML (sandboxed).
 * Same role as spa v1 ``FilePreview.vue`` for those two formats.
 */
export default function FilePreview({
  file,
  onClose,
}: {
  file: ChatFile
  onClose: () => void
}) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [error, setError] = useState('')
  const ext = extOf(file.name)
  const isMd = ext === 'md' || ext === 'markdown'
  const isHtml = ext === 'html' || ext === 'htm'

  const decoded = useMemo(() => {
    try {
      return decodeBase64Text(file.data)
    } catch {
      return ''
    }
  }, [file.data])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    host.replaceChildren()
    setError('')
    if (!decoded) {
      setError('无法解码文件内容')
      return
    }
    if (isMd) {
      const article = document.createElement('article')
      article.className = 'file-preview-md'
      article.innerHTML = renderMd(decoded)
      host.appendChild(article)
      return
    }
    if (isHtml) {
      const iframe = document.createElement('iframe')
      iframe.className = 'file-preview-html'
      iframe.title = file.name
      iframe.sandbox = ''
      const blob = new Blob([decoded], { type: 'text/html' })
      iframe.src = URL.createObjectURL(blob)
      host.appendChild(iframe)
      return () => URL.revokeObjectURL(iframe.src)
    }
    setError('当前仅支持预览 Markdown / HTML 文件')
  }, [decoded, file.name, isHtml, isMd])

  const onPreviewClick = async (e: MouseEvent) => {
    const btn = (e.target as HTMLElement).closest?.('[data-table-action]') as HTMLElement | null
    if (!btn) return
    e.preventDefault()
    const card = btn.closest('[data-md-table]')
    const table = card?.querySelector('table') as HTMLTableElement | null
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
        await downloadMatrixXlsx(matrix, `table-${stamp}.xlsx`)
      } finally {
        btn.classList.remove('is-busy')
      }
    }
  }

  const downloadFile = () => {
    const mime = mimeType(file.name)
    const raw = file.data.includes(',') ? file.data : `data:${mime};base64,${file.data}`
    const a = document.createElement('a')
    a.href = raw.startsWith('data:') ? raw : `data:${mime};base64,${file.data}`
    a.download = file.name
    a.click()
  }

  return createPortal(
    <div className="preview-drawer-shell">
      <button type="button" className="preview-scrim" aria-label="关闭预览" onClick={onClose} />
      <aside className="file-preview preview-drawer" role="dialog" aria-modal="true" aria-label="文件预览">
        <header className="preview-drawer-header">
          <div className="preview-title-wrap">
            <FileText size={18} />
            <div className="preview-title" title={file.name}>{file.name}</div>
          </div>
          <div className="preview-actions">
            <button type="button" className="preview-icon-btn" title="下载" onClick={downloadFile}>
              <Download size={16} />
            </button>
            <button type="button" className="preview-icon-btn" title="关闭" onClick={onClose}>
              <X size={16} />
            </button>
          </div>
        </header>
        <div className="preview-drawer-body" onClick={(e) => void onPreviewClick(e)}>
          {error ? <div className="preview-state">{error}</div> : null}
          <div ref={hostRef} className="preview-host" />
        </div>
      </aside>
    </div>,
    document.body,
  )
}
