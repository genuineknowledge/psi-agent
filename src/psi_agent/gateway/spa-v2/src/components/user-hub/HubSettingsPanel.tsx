import { ChevronRight, FolderOpen } from 'lucide-react'
import HubDialog from './HubDialog'

type Props = {
  show: boolean
  onClose: () => void
  workspace?: string
  onChangeWorkspace?: () => void
  notificationsEnabled: boolean
  hapticsEnabled: boolean
  onToggleNotifications: () => void
  onToggleHaptics: () => void
  onAction?: (label: string) => void
}

function workspaceLabel(path: string): string {
  const p = path.replace(/\\/g, '/').replace(/\/+$/, '')
  const parts = p.split('/').filter(Boolean)
  return parts[parts.length - 1] || p || '未选择'
}

export default function HubSettingsPanel({
  show,
  onClose,
  workspace,
  onChangeWorkspace,
  notificationsEnabled,
  hapticsEnabled,
  onToggleNotifications,
  onToggleHaptics,
  onAction,
}: Props) {
  return (
    <HubDialog
      show={show}
      title="设置"
      width={440}
      onClose={onClose}
      actions={<button type="button" className="hub-btn primary" onClick={onClose}>关闭</button>}
    >
      {onChangeWorkspace && (
        <section className="hub-settings-section">
          <h4>工作区</h4>
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
          {workspace ? (
            <p className="hub-settings-workspace-path" title={workspace}>{workspace}</p>
          ) : null}
        </section>
      )}

      <section className="hub-settings-section">
        <h4>通知与反馈</h4>
        <button type="button" className="hub-settings-toggle" onClick={onToggleNotifications}>
          <span>
            <strong>通知与提醒</strong>
            <em>任务动态进入收件箱</em>
          </span>
          <i className={notificationsEnabled ? 'on' : ''} />
        </button>
        <button type="button" className="hub-settings-toggle" onClick={onToggleHaptics}>
          <span>
            <strong>动效与触觉反馈</strong>
            <em>金币动画与手机轻震</em>
          </span>
          <i className={hapticsEnabled ? 'on' : ''} />
        </button>
      </section>

      <section className="hub-settings-section">
        <h4>其它</h4>
        <button type="button" className="hub-settings-row" onClick={() => onAction?.('默认交付位置：成果库')}>
          <span><strong>默认交付位置</strong><em>成果库</em></span>
        </button>
        <button type="button" className="hub-settings-row" onClick={() => onAction?.('语言与称呼：简体中文 · 您')}>
          <span><strong>语言与称呼</strong><em>简体中文 · 您</em></span>
        </button>
        <button type="button" className="hub-settings-row" onClick={() => onAction?.('快捷键：⌘/Ctrl K 搜索 · ⌘/Ctrl N 新建')}>
          <span><strong>键盘快捷键</strong><em>⌘/Ctrl K 搜索 · ⌘/Ctrl N 新建</em></span>
        </button>
      </section>
      <p className="hub-settings-foot">HaiTun Agent · 本地 Gateway</p>
    </HubDialog>
  )
}
