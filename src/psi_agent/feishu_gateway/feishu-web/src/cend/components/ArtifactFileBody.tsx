import { useEffect, useRef, useState, type MouseEvent } from 'react'
import { downloadMatrixXlsx, matrixToTsv, tableToMatrix } from '../services/mdTable'
import type { ChatFile } from '../haitun-agent/model'
import { renderBlobPreview } from '../utils/renderBlobPreview'

/**
 * Render a chat/deliverable blob into a host element.
 * Formats align with spa v1 FilePreview (MD/HTML/image/office/pdf/… via lazy import).
 * Shared by chat FilePreview drawer and ArtifactDrawer (宝箱).
 */
export function ArtifactFileBody({ file }: { file: ChatFile }) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let cancelled = false
    let cleanup = () => {}

    setLoading(true)
    setError('')
    setNotice('')
    host.replaceChildren()

    void (async () => {
      const handle = await renderBlobPreview(host, { name: file.name, data: file.data })
      if (cancelled) {
        handle.cleanup()
        return
      }
      cleanup = handle.cleanup
      if (handle.error) setError(handle.error)
      if (handle.notice) setNotice(handle.notice)
      setLoading(false)
    })()

    return () => {
      cancelled = true
      cleanup()
    }
  }, [file.data, file.name])

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

  return (
    <div className="artifact-preview-scroll" onClick={(e) => void onPreviewClick(e)}>
      {loading ? <div className="artifact-preview-state">正在生成预览…</div> : null}
      {notice && !error ? <div className="artifact-preview-notice">{notice}</div> : null}
      {error ? <div className="artifact-preview-state">{error}</div> : null}
      <div ref={hostRef} className="artifact-preview-host" />
    </div>
  )
}
