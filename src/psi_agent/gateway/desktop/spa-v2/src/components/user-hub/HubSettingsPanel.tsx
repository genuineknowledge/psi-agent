import { ChevronRight, FolderOpen } from 'lucide-react'
import HubDialog from './HubDialog'

type Props = {
  show: boolean
  onClose: () => void
  workspace?: string
  onChangeWorkspace?: () => void
  onOpenAdvancedSettings?: () => void
}

function pathLabel(path: string): string {
  const p = path.replace(/\\/g, '/').replace(/\/+$/, '')
  const parts = p.split('/').filter(Boolean)
  return parts[parts.length - 1] || p || '未选择'
}

/** Settings dialog — workspace switch + advanced settings entry. */
export default function HubSettingsPanel({
  show,
  onClose,
  workspace,
  onChangeWorkspace,
  onOpenAdvancedSettings,
}: Props) {
  return (
    <HubDialog
      show={show}
      title={(
        <div className="hub-models-title">
          <span>设置</span>
          <button
            type="button"
            className="hub-link"
            onClick={() => onOpenAdvancedSettings?.()}
          >
            高级设置
          </button>
        </div>
      )}
      width={480}
      onClose={onClose}
      actions={<button type="button" className="hub-btn primary" onClick={onClose}>关闭</button>}
    >
      <section className="hub-settings-section">
        {onChangeWorkspace ? (
          <button
            type="button"
            className="hub-settings-row hub-settings-workspace"
            onClick={() => {
              onClose()
              onChangeWorkspace()
            }}
          >
            <span className="hub-settings-workspace-icon" aria-hidden="true">
              <FolderOpen size={18} />
            </span>
            <span>
              <strong>切换海豚工作室</strong>
              <em title={workspace || undefined}>
                {workspace ? pathLabel(workspace) : '选择本机目录'}
              </em>
            </span>
            <ChevronRight size={16} className="hub-settings-row-chevron" />
          </button>
        ) : (
          <p className="hub-settings-workspace-path">{workspace || '未选择海豚工作室'}</p>
        )}
        {workspace && onChangeWorkspace ? (
          <p className="hub-settings-workspace-path" title={workspace}>{workspace}</p>
        ) : null}
        <p className="hub-settings-foot">
          任务会绑定到这个文件夹，项目文件、历史记录和交付物都会保存在这里。
        </p>
      </section>
    </HubDialog>
  )
}
