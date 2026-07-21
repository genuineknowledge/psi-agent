import type { ReactNode } from 'react'
import { X } from 'lucide-react'

type Props = {
  show: boolean
  title: ReactNode
  width?: number
  onClose: () => void
  children: ReactNode
  actions?: ReactNode
}

/** Simple modal shell (spa v1 BaseDialog equivalent). */
export default function HubDialog({
  show,
  title,
  width = 480,
  onClose,
  children,
  actions,
}: Props) {
  if (!show) return null
  return (
    <div className="hub-dialog-layer" role="dialog" aria-modal="true">
      <button type="button" className="hub-dialog-backdrop" aria-label="关闭" onClick={onClose} />
      <div className="hub-dialog" style={{ width: `min(${width}px, 94vw)` }}>
        <header className="hub-dialog-header">
          <div className="hub-dialog-title">{title}</div>
          <button type="button" className="hub-dialog-close" onClick={onClose} aria-label="关闭">
            <X size={18} />
          </button>
        </header>
        <div className="hub-dialog-body">{children}</div>
        {actions ? <footer className="hub-dialog-actions">{actions}</footer> : null}
      </div>
    </div>
  )
}
