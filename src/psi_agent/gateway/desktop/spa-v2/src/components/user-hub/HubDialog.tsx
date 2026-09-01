import type { ReactNode } from 'react'
import { ChevronLeft, X } from 'lucide-react'

type Props = {
  show: boolean
  title: ReactNode
  width?: number
  onClose: () => void
  children: ReactNode
  actions?: ReactNode
  /** 给出则在标题左侧显示返回箭头（原型 A2 / C2 的子屏）。 */
  onBack?: () => void
  /**
   * 隐藏关闭按钮并禁用点击遮罩关闭。用于原型 D4：建号收尾期间不可中断，
   * 中途关掉会留下一个已验证但未建号的悬空状态。
   */
  blocking?: boolean
}

/** Simple modal shell (spa v1 BaseDialog equivalent). */
export default function HubDialog({
  show,
  title,
  width = 480,
  onClose,
  children,
  actions,
  onBack,
  blocking = false,
}: Props) {
  if (!show) return null
  return (
    <div className="hub-dialog-layer" role="dialog" aria-modal="true">
      {/* blocking 时遮罩不再是「关闭」控件：留着 aria-label 会让读屏软件报出一个
          点了没反应的关闭动作。改为 aria-hidden 的纯装饰层。 */}
      {blocking ? (
        <div className="hub-dialog-backdrop" aria-hidden="true" />
      ) : (
        <button type="button" className="hub-dialog-backdrop" aria-label="关闭" onClick={onClose} />
      )}
      <div className="hub-dialog" style={{ width: `min(${width}px, 94vw)` }}>
        <header className="hub-dialog-header">
          {onBack ? (
            <button type="button" className="hub-dialog-close" onClick={onBack} aria-label="返回">
              <ChevronLeft size={18} />
            </button>
          ) : null}
          <div className="hub-dialog-title" style={onBack ? { flex: 1 } : undefined}>
            {title}
          </div>
          {blocking ? null : (
            <button type="button" className="hub-dialog-close" onClick={onClose} aria-label="关闭">
              <X size={18} />
            </button>
          )}
        </header>
        <div className="hub-dialog-body">{children}</div>
        {actions ? <footer className="hub-dialog-actions">{actions}</footer> : null}
      </div>
    </div>
  )
}
