import { useEffect } from 'react'
import { BarChart3, Bot, FileCode, FileText, Plus, Settings2, Sparkles, X } from 'lucide-react'
import './first-run-guide.css'

type Props = {
  onClose: () => void
  onConfigureModels: () => void
  onStartTask: () => void
}

export default function FirstRunGuide({
  onClose,
  onConfigureModels,
  onStartTask,
}: Props) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="first-run-layer" role="dialog" aria-modal="true" aria-label="新手引导">
      <button className="first-run-backdrop" type="button" onClick={onClose} aria-label="关闭引导" />
      <div className="first-run-dialog">
        <button type="button" className="first-run-close" onClick={onClose} aria-label="关闭">
          <X size={16} />
        </button>
        <div className="first-run-head">
          <div>
            <span className="first-run-eyebrow">
              <Sparkles size={13} /> 新手引导
            </span>
            <h2>欢迎使用HaiTun，从这里开始</h2>
          </div>
        </div>
        <div className="first-run-sections">
          <section className="first-run-section">
            <div className="first-run-capability">
              <div className="first-run-capability-intro">
                <Sparkles size={15} />
                <p>当您发起任务后，Agent 会自己规划并执行，最后把成果整理成交付物。常见任务示例：</p>
              </div>
              <div className="first-run-capability-tags">
                <span><FileText size={14} /> 整理文档并生成摘要</span>
                <span><BarChart3 size={14} /> 分析数据并输出表格</span>
                <span><FileCode size={14} /> 编写代码并交付文件</span>
              </div>
            </div>
          </section>
        </div>
        <div className="first-run-foot">
          <div className="first-run-actions">
            <button type="button" className="first-run-btn secondary" onClick={onConfigureModels}>
              <Bot size={15} /> 配置模型
            </button>
            <button type="button" className="first-run-btn primary" onClick={onStartTask}>
              <Plus size={15} /> 新建任务/聊天
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
