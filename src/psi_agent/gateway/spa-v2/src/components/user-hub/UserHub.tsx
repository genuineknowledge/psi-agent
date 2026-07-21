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
  notificationsEnabled: boolean
  hapticsEnabled: boolean
  onToggleNotifications: () => void
  onToggleHaptics: () => void
  onToast?: (message: string) => void
  onAisChanged?: (ais: AiInfo[]) => void
  /** Open models panel on first mount (e.g. empty AI pool). */
  openModelsOnMount?: boolean
  /** Fired once after auto-opening models so the parent can clear the one-shot flag. */
  onModelsAutoOpened?: () => void
}

/**
 * spa v1 UserHub 等价物：点头像弹出菜单 → 资料 / 大模型 / 登录 / 设置。
 * 挂在侧栏左下角账户区（不再使用三点菜单）。
 */
export default function UserHub({
  selectedAiId,
  onSelectAi,
  notificationsEnabled,
  hapticsEnabled,
  onToggleNotifications,
  onToggleHaptics,
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
      <button
        type="button"
        className="user-hub-trigger"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        title={`${displayName} — 用户菜单`}
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

      {menuOpen && (
        <div className="user-hub-menu" role="menu">
          <button type="button" role="menuitem" onClick={() => openPanel('profile')}>
            <UserRound size={15} /> 我的资料
          </button>
          <button type="button" role="menuitem" onClick={() => openPanel('models')}>
            <Bot size={15} /> 大模型
            {aiCount > 0 ? <em className="user-hub-menu-badge">{aiCount}</em> : null}
          </button>
          <button type="button" role="menuitem" onClick={() => openPanel('login')}>
            <LogIn size={15} /> 登录 <span className="muted">本地</span>
          </button>
          <button type="button" role="menuitem" onClick={() => openPanel('settings')}>
            <Settings2 size={15} /> 设置
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
        notificationsEnabled={notificationsEnabled}
        hapticsEnabled={hapticsEnabled}
        onToggleNotifications={onToggleNotifications}
        onToggleHaptics={onToggleHaptics}
        onAction={onToast}
      />
      <HubAdvancedPanel
        show={panel === 'advanced'}
        onClose={() => setPanel(null)}
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
