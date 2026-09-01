import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Download, FileText, FolderOpen, X } from 'lucide-react'
import type { ChatFile } from '../haitun-agent/model'
import { downloadChatFile, revealDeliverableInFolder } from '../utils/filePreviewUtils'
import { ArtifactFileBody } from './ArtifactFileBody'

/**
 * In-app preview drawer for chat blobs — same render path as 宝箱 ArtifactFileBody.
 */
export default function FilePreview({
  file,
  workspaceRoot = '',
  onClose,
}: {
  file: ChatFile
  workspaceRoot?: string
  onClose: () => void
}) {
  const [revealBusy, setRevealBusy] = useState(false)
  const [revealError, setRevealError] = useState('')
  const canReveal = Boolean(file.path?.trim())

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const handleReveal = () => {
    const path = file.path?.trim()
    if (!path || revealBusy) return
    setRevealBusy(true)
    setRevealError('')
    void revealDeliverableInFolder(path, workspaceRoot)
      .catch((e) => {
        setRevealError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => setRevealBusy(false))
  }

  return createPortal(
    <div className="preview-drawer-shell">
      <button type="button" className="preview-scrim" aria-label="关闭预览" onClick={onClose} />
      <aside className="file-preview preview-drawer" role="dialog" aria-modal="true" aria-label="文件预览">
        <header className="preview-drawer-header">
          <div className="preview-title-wrap">
            <FileText size={18} />
            <div className="preview-title" title={file.path || file.name}>{file.name}</div>
          </div>
          <div className="preview-actions">
            <button
              type="button"
              className="preview-icon-btn"
              title={canReveal ? (revealBusy ? '正在打开…' : '在文件夹中显示') : '无磁盘路径，无法定位'}
              disabled={!canReveal || revealBusy}
              onClick={handleReveal}
              aria-label="在文件夹中显示"
            >
              <FolderOpen size={16} />
            </button>
            <button type="button" className="preview-icon-btn" title="下载" onClick={() => downloadChatFile(file)}>
              <Download size={16} />
            </button>
            <button type="button" className="preview-icon-btn" title="关闭" onClick={onClose}>
              <X size={16} />
            </button>
          </div>
        </header>
        {revealError ? <div className="preview-reveal-error" role="alert">{revealError}</div> : null}
        <div className="preview-drawer-body">
          <ArtifactFileBody key={`${file.name}:${file.data.slice(0, 48)}`} file={file} />
        </div>
      </aside>
    </div>,
    document.body,
  )
}
