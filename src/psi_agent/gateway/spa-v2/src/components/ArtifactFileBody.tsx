import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import { downloadMatrixXlsx, matrixToTsv, tableToMatrix } from '../services/mdTable'
import { renderMd } from '../services/renderMd'
import type { ChatFile } from '../haitun-agent/model'
import {
  dataUrlForChatFile,
  decodeBase64Utf8,
} from '../utils/filePreviewUtils'

function extOf(name: string) {
  return (name.split('.').pop() || '').toLowerCase()
}

/**
 * Render a chat blob into a host element (MD / HTML / image / plain text).
 * Shared by chat FilePreview and ArtifactDrawer.
 */
export function ArtifactFileBody({ file }: { file: ChatFile }) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [error, setError] = useState('')
  const ext = extOf(file.name)
  const isMd = ext === 'md' || ext === 'markdown'
  const isHtml = ext === 'html' || ext === 'htm'
  const isImage = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)
  const isText = ['txt', 'csv', 'log', 'json', 'css', 'js', 'ts', 'tsx', 'py'].includes(ext)

  const decoded = useMemo(() => {
    if (isImage) return ''
    try {
      return decodeBase64Utf8(file.data)
    } catch {
      return ''
    }
  }, [file.data, isImage])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    host.replaceChildren()
    setError('')

    if (isImage) {
      try {
        const img = document.createElement('img')
        img.className = 'artifact-preview-image'
        img.alt = file.name
        img.src = dataUrlForChatFile(file)
        host.appendChild(img)
      } catch {
        setError('无法解码图片')
      }
      return
    }

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

    if (isText) {
      const pre = document.createElement('pre')
      pre.className = 'artifact-preview-text'
      pre.textContent = decoded
      host.appendChild(pre)
      return
    }

    setError('此格式暂不支持页内预览，请下载后查看')
  }, [decoded, file, isHtml, isImage, isMd, isText])

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
      {error ? <div className="artifact-preview-state">{error}</div> : null}
      <div ref={hostRef} className="artifact-preview-host" />
    </div>
  )
}
