import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { ClipboardList, ExternalLink, GripVertical } from 'lucide-react'
import './survey-popup.css'

/** 问卷反馈表单（飞书共享问卷）。 */
const SURVEY_URL =
  'https://genuineknowledge.feishu.cn/share/base/form/shrcn7pp47SeGec2M4Srnbt75Rg?from=navigation'

// 进入 HaiTun 页面 5 分钟后弹出。
const SURVEY_DELAY_MS = 300_000
const SURVEY_DONE_KEY = 'haitun-survey-done'

function surveyCompleted(): boolean {
  try {
    return window.localStorage.getItem(SURVEY_DONE_KEY) === '1'
  } catch {
    return false
  }
}

function markSurveyCompleted() {
  try {
    window.localStorage.setItem(SURVEY_DONE_KEY, '1')
  } catch {
    /* ignore */
  }
}

/** 进入 HaiTun 页面 5 分钟后弹出的悬浮框：可拖动，点击按钮在新标签页打开问卷。
 *  关闭后写入 localStorage 标记，重启电脑后也不再弹，直到用户清除浏览器数据。 */
export default function SurveyPopup() {
  const [open, setOpen] = useState(false)
  const [canClose, setCanClose] = useState(false)
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)
  const frameRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (surveyCompleted()) return
    const timer = window.setTimeout(() => setOpen(true), SURVEY_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [])

  if (!open) return null

  const startDrag = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    const frame = frameRef.current
    if (!frame) return
    e.preventDefault()
    const rect = frame.getBoundingClientRect()
    const startLeft = position?.left ?? rect.left
    const startTop = position?.top ?? rect.top
    const startX = e.clientX
    const startY = e.clientY
    const onMove = (ev: PointerEvent) => {
      const left = Math.min(
        Math.max(startLeft + ev.clientX - startX, 8),
        window.innerWidth - rect.width - 8,
      )
      const top = Math.min(
        Math.max(startTop + ev.clientY - startY, 8),
        window.innerHeight - rect.height - 8,
      )
      setPosition({ left, top })
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  return (
    <div className="survey-popup-layer">
      <div
        ref={frameRef}
        className="survey-popup-frame"
        style={position ? { left: position.left, top: position.top, right: 'auto' } : undefined}
      >
        <header className="survey-popup-header" onPointerDown={startDrag}>
          <GripVertical size={16} className="survey-popup-grip" aria-hidden="true" />
        </header>
        <p className="survey-popup-intro">
          您好，本次问卷用于收集您的真实使用感受与反馈，帮助我们持续优化产品能力和使用体验，所有填写内容仅用于产品迭代，填写时长约 3-5 分钟
        </p>
        <div className="survey-popup-body">
          <a
            className="survey-popup-link"
            href={SURVEY_URL}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => setCanClose(true)}
          >
            <ClipboardList size={18} aria-hidden="true" />
            <span>请填写体验反馈问卷</span>
            <ExternalLink size={15} aria-hidden="true" />
          </a>
        </div>
        {canClose && (
          <footer className="survey-popup-footer">
            <button
              type="button"
              className="survey-popup-close"
              onClick={() => {
                markSurveyCompleted()
                setOpen(false)
              }}
            >
              关闭
            </button>
          </footer>
        )}
      </div>
    </div>
  )
}
