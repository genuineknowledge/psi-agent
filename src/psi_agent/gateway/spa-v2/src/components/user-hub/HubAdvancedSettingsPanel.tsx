import { ChevronRight, Package } from 'lucide-react'
import HubDialog from './HubDialog'

type Props = {
  show: boolean
  onClose: () => void
  onBackToSettings?: () => void
  agent?: string
  onChangeAgent?: () => void
}

function pathLabel(path: string): string {
  const p = path.replace(/\\/g, '/').replace(/\/+$/, '')
  const parts = p.split('/').filter(Boolean)
  return parts[parts.length - 1] || p || '未选择'
}

/** Advanced settings page of the settings dialog: Agent package path switch. */
export default function HubAdvancedSettingsPanel({
  show,
  onClose,
  onBackToSettings,
  agent,
  onChangeAgent,
}: Props) {
  const backToSettings = () => {
    if (onBackToSettings) {
      onBackToSettings()
      return
    }
    onClose()
  }

  return (
    <HubDialog
      show={show}
      title={(
        <div className="hub-models-title">
          <span>高级设置</span>
          <button type="button" className="hub-link" onClick={backToSettings}>
            返回设置
          </button>
        </div>
      )}
      width={480}
      onClose={onClose}
      actions={(
        <>
          <button type="button" className="hub-btn ghost" onClick={backToSettings}>返回设置</button>
          <button type="button" className="hub-btn primary" onClick={onClose}>关闭</button>
        </>
      )}
    >
      <section className="hub-settings-section">
        {onChangeAgent ? (
          <button
            type="button"
            className="hub-settings-row hub-settings-workspace"
            onClick={() => {
              onClose()
              onChangeAgent()
            }}
          >
            <span className="hub-settings-workspace-icon" aria-hidden="true">
              <Package size={18} />
            </span>
            <span>
              <strong>切换 Agent 包</strong>
              <em title={agent || undefined}>
                {agent ? pathLabel(agent) : '选择能力包目录'}
              </em>
            </span>
            <ChevronRight size={16} className="hub-settings-row-chevron" />
          </button>
        ) : null}
        {agent && onChangeAgent ? (
          <p className="hub-settings-workspace-path" title={agent}>{agent}</p>
        ) : null}
        <p className="hub-settings-foot">
          Agent 包包含 tools / schedules / systems，用于定义 Agent 的工具能力、定时任务与工作规则；您也可以在此切换为自己构建的 Agent。
        </p>
      </section>
    </HubDialog>
  )
}
