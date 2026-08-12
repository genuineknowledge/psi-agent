import { useEffect, useState } from 'react'
import { Bot, Settings2 } from 'lucide-react'
import type { AiInfo } from '../../services/api'
import { listAis } from '../../services/api'
import { readStoredAvatar, readStoredName } from '../../services/userProfile'
import { dedupeAisForDisplay, readStoredAiId } from '../../services/bootstrapAi'
import HubAdvancedPanel from './HubAdvancedPanel'
import HubAdvancedSettingsPanel from './HubAdvancedSettingsPanel'
import HubModelsPanel, { FREE_MODEL_NOTICE_BODY, FREE_MODEL_NOTICE_TITLE } from './HubModelsPanel'
import HubProfilePanel from './HubProfilePanel'
import HubSettingsPanel from './HubSettingsPanel'
import './user-hub.css'

export type HubPanel = 'profile' | 'models' | 'settings' | 'settingsAdvanced' | 'advanced' | null

type Props = {
  selectedAiId: string | null
  onSelectAi: (id: string | null) => void
  workspace?: string
  onChangeWorkspace?: () => void
  agent?: string
  onChangeAgent?: () => void
  onToast?: (message: string) => void
  onAisChanged?: (ais: AiInfo[]) => void
  /** Open models panel on first mount (e.g. empty AI pool). */
  openModelsOnMount?: boolean
  /** Fired once after auto-opening models so the parent can clear the one-shot flag. */
  onModelsAutoOpened?: () => void
  /** External open-panel request (e.g. first-run guide jumps into model pool). */
  openPanelRequest?: { nonce: number; panel: HubPanel } | null
}

/**
 * 侧栏账户区：头像直达我的资料，模型池与设置分入口。
 */
export default function UserHub({
  selectedAiId,
  onSelectAi,
  workspace,
  onChangeWorkspace,
  agent,
  onChangeAgent,
  onToast,
  onAisChanged,
  openModelsOnMount = false,
  onModelsAutoOpened,
  openPanelRequest,
}: Props) {
  const [panel, setPanel] = useState<HubPanel>(null)
  const [userName, setUserName] = useState(readStoredName)
  const [userAvatar, setUserAvatar] = useState(readStoredAvatar)
  const [aiCount, setAiCount] = useState(0)
  const [freeModelNoticeOpen, setFreeModelNoticeOpen] = useState(false)

  useEffect(() => {
    if (!openModelsOnMount) return
    setPanel('models')
    onModelsAutoOpened?.()
  }, [openModelsOnMount, onModelsAutoOpened])

  useEffect(() => {
    if (!openPanelRequest) return
    setPanel(openPanelRequest.panel)
  }, [openPanelRequest])

  useEffect(() => {
    void listAis()
      .then((list) => {
        const shown = dedupeAisForDisplay(list, selectedAiId ?? readStoredAiId())
        setAiCount(shown.length)
      })
      .catch(() => {})
  }, [selectedAiId])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (freeModelNoticeOpen) return
      if (panel === 'settingsAdvanced') {
        setPanel('settings')
        return
      }
      if (panel === 'advanced') {
        setPanel('models')
        return
      }
      if (panel) {
        setPanel(null)
        return
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [freeModelNoticeOpen, panel])

  const initial = userName.trim().charAt(0).toUpperCase()
  const displayName = userName.trim() || '用户'

  const openPanel = (next: HubPanel) => {
    setPanel(next)
  }

  return (
    <div className="user-hub">
      <div className="user-hub-row">
        <button
          type="button"
          className="user-hub-trigger"
          title={`${displayName} — 账户`}
          onClick={() => openPanel('profile')}
        >
          <span className="account-avatar user-hub-avatar">
            {userAvatar ? <img src={userAvatar} alt="" /> : initial || 'U'}
          </span>
          <span className="user-hub-meta">
            <strong>{displayName}</strong>
            <span><i /> Agent 在线</span>
          </span>
        </button>

        <div className="user-hub-shortcuts" role="toolbar" aria-label="模型与设置">
          <button
            type="button"
            className={`user-hub-shortcut${panel === 'models' || panel === 'advanced' ? ' active' : ''}`}
            title="模型池"
            aria-label={`模型池${aiCount > 0 ? `，${aiCount} 个` : ''}`}
            onClick={() => openPanel('models')}
          >
            <Bot size={16} />
          </button>
          <button
            type="button"
            className={`user-hub-shortcut${panel === 'settings' || panel === 'settingsAdvanced' ? ' active' : ''}`}
            title="设置"
            aria-label="设置"
            onClick={() => openPanel('settings')}
          >
            <Settings2 size={16} />
          </button>
        </div>
      </div>

      <HubProfilePanel
        show={panel === 'profile'}
        onClose={() => setPanel(null)}
        onToast={onToast}
        onSaved={(name, avatar) => {
          setUserName(name)
          setUserAvatar(avatar)
        }}
      />
      <HubModelsPanel
        show={panel === 'models'}
        onClose={() => setPanel(null)}
        selectedAiId={selectedAiId}
        onSelectAi={onSelectAi}
        onOpenAdvanced={() => setPanel('advanced')}
        onToast={onToast}
        onFreeModelNotice={() => setFreeModelNoticeOpen(true)}
        onAisChanged={(ais) => {
          setAiCount(dedupeAisForDisplay(ais, selectedAiId).length)
          onAisChanged?.(ais)
        }}
      />
      <HubSettingsPanel
        show={panel === 'settings'}
        onClose={() => setPanel(null)}
        workspace={workspace}
        onChangeWorkspace={onChangeWorkspace}
        onOpenAdvancedSettings={() => setPanel('settingsAdvanced')}
      />
      <HubAdvancedSettingsPanel
        show={panel === 'settingsAdvanced'}
        onClose={() => setPanel(null)}
        onBackToSettings={() => setPanel('settings')}
        agent={agent}
        onChangeAgent={() => {
          setPanel(null)
          onChangeAgent?.()
        }}
      />
      <HubAdvancedPanel
        show={panel === 'advanced'}
        onClose={() => setPanel(null)}
        onBackToModels={() => setPanel('models')}
        onSelectAi={onSelectAi}
        onToast={onToast}
        onAisChanged={(ais) => {
          setAiCount(dedupeAisForDisplay(ais, selectedAiId).length)
          onAisChanged?.(ais)
        }}
      />

      {freeModelNoticeOpen && (
        <div className="hub-dialog-layer" role="dialog" aria-modal="true" aria-label="免费模型提示">
          <div className="hub-dialog-backdrop hub-free-notice-backdrop" aria-hidden="true" />
          <div className="hub-dialog hub-free-notice-dialog">
            <div className="hub-dialog-body">
              <p className="hub-free-notice-title">{FREE_MODEL_NOTICE_TITLE}</p>
              <p className="hub-free-notice-text">{FREE_MODEL_NOTICE_BODY}</p>
            </div>
            <footer className="hub-dialog-actions">
              <button
                type="button"
                className="hub-btn primary"
                onClick={() => setFreeModelNoticeOpen(false)}
              >
                知道了
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  )
}
