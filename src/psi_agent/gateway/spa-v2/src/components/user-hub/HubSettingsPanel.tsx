import { ChevronRight, FolderOpen } from 'lucide-react'
import HubDialog from './HubDialog'

type Props = {
  show: boolean
  onClose: () => void
  workspace?: string
  onChangeWorkspace?: () => void
}

function workspaceLabel(path: string): string {
  const p = path.replace(/\\/g, '/').replace(/\/+$/, '')
  const parts = p.split('/').filter(Boolean)
  return parts[parts.length - 1] || p || '未选择'
}

/** Settings dialog — only real actions for now (workspace switch). */
export default function HubSettingsPanel({
  show,
  onClose,
  workspace,
  onChangeWorkspace,
}: Props) {
  return (
    <HubDialog
      show={show}
      title="设置"
      width={420}
      onClose={onClose}
      actions={<button type="button" className="hub-btn primary" onClick={onClose}>关闭</button>}
    >
      <section className="hub-settings-section">
        <h4>工作区</h4>
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
              <strong>切换工作区</strong>
              <em title={workspace || undefined}>
                {workspace ? workspaceLabel(workspace) : '选择本机目录'}
              </em>
            </span>
            <ChevronRight size={16} className="hub-settings-row-chevron" />
          </button>
        ) : (
          <p className="hub-settings-workspace-path">{workspace || '未选择工作区'}</p>
        )}
        {workspace && onChangeWorkspace ? (
          <p className="hub-settings-workspace-path" title={workspace}>{workspace}</p>
        ) : null}
      </section>
    </HubDialog>
  )
}
