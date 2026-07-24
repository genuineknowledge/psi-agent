import { useEffect, useRef, useState } from 'react'
import { Bot, LogIn, Settings2, UserRound } from 'lucide-react'
import type { AiInfo } from '../../services/api'
import { listAis } from '../../services/api'
import { readStoredAvatar, readStoredName } from '../../services/userProfile'
import HubAdvancedPanel from './HubAdvancedPanel'
import HubLoginPanel from './HubLoginPanel'
import HubModelsPanel from './HubModelsPanel'
import HubProfilePanel from './HubProfilePanel'
import HubSettingsPanel from './HubSettingsPanel'
import './user-hub.css'

export type HubPanel = 'profile' | 'models' | 'login' | 'settings' | 'advanced' | null

type Props = {
  selectedAiId: string | null
  onSelectAi: (id: string | null) => void
  workspace?: string
  onChangeWorkspace?: () => void
  onToast?: (message: string) => void
  onAisChanged?: (ais: AiInfo[]) => void
  /** Open models panel on first mount (e.g. empty AI pool). */
  openModelsOnMount?: boolean
  /** Fired once after auto-opening models so the parent can clear the one-shot flag. */
  onModelsAutoOpened?: () => void
}

/**
 * 侧栏账户区：头像菜单（资料 / 登录）与模型池、设置分入口。
 */
export default function UserHub({
  selectedAiId,
  onSelectAi,
  workspace,
  onChangeWorkspace,
  onToast,
  onAisChanged,
  openModelsOnMount = false,
  onModelsAutoOpened,
}: Props) {
  const rootRef = useRef<HTMLDivElement | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [panel, setPanel] = useState<HubPanel>(null)
  const [userName, setUserName] = useState(readStoredName)
  const [userAvatar, setUserAvatar] = useState(readStoredAvatar)
  const [aiCount, setAiCount] = useState(0)

  useEffect(() => {
    if (!openModelsOnMount) return
    setPanel('models')
    onModelsAutoOpened?.()
  }, [openModelsOnMount, onModelsAutoOpened])

  useEffect(() => {
    void listAis()
      .then((ais) => setAiCount(ais.length))
      .catch(() => {})
  }, [])

  useEffect(() => {
    const onDoc = (event: MouseEvent) => {
      if (!menuOpen) return
      const el = rootRef.current
      if (el && !el.contains(event.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [menuOpen])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (panel === 'advanced') {
        setPanel('models')
        return
      }
      if (panel) {
        setPanel(null)
        return
      }
      if (menuOpen) setMenuOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [panel, menuOpen])

  const initial = userName.trim().charAt(0).toUpperCase()
  const displayName = userName.trim() || '用户'

  const openPanel = (next: HubPanel) => {
    setPanel(next)
    setMenuOpen(false)
  }

  return (
    <div className="user-hub" ref={rootRef}>
      <div className="user-hub-row">
        <button
          type="button"
          className="user-hub-trigger"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          title={`${displayName} — 账户`}
          onClick={() => setMenuOpen((v) => !v)}
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
            {aiCount > 0 ? <em className="user-hub-shortcut-badge">{aiCount}</em> : null}
          </button>
          <button
            type="button"
            className={`user-hub-shortcut${panel === 'settings' ? ' active' : ''}`}
            title="设置"
            aria-label="设置"
            onClick={() => openPanel('settings')}
          >
            <Settings2 size={16} />
          </button>
        </div>
      </div>

      {menuOpen && (
        <div className="user-hub-menu" role="menu">
          <button type="button" role="menuitem" onClick={() => openPanel('profile')}>
            <UserRound size={15} /> 我的资料
          </button>
          <button type="button" role="menuitem" onClick={() => openPanel('login')}>
            <LogIn size={15} /> 登录 <span className="muted">本地</span>
          </button>
        </div>
      )}

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
        onAisChanged={(ais) => {
          setAiCount(ais.length)
          onAisChanged?.(ais)
        }}
      />
      <HubLoginPanel show={panel === 'login'} onClose={() => setPanel(null)} />
      <HubSettingsPanel
        show={panel === 'settings'}
        onClose={() => setPanel(null)}
        workspace={workspace}
        onChangeWorkspace={onChangeWorkspace}
      />
      <HubAdvancedPanel
        show={panel === 'advanced'}
        onClose={() => setPanel(null)}
        onBackToModels={() => setPanel('models')}
        onSelectAi={onSelectAi}
        onToast={onToast}
        onAisChanged={(ais) => {
          setAiCount(ais.length)
          onAisChanged?.(ais)
        }}
      />
    </div>
  )
}
