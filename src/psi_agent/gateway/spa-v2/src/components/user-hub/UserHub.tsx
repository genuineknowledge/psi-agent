import { useEffect, useRef, useState } from 'react'
import { Bot, ClipboardList, ExternalLink, LogIn, Settings2, UserCog, UserRound } from 'lucide-react'
import type { AiInfo } from '../../services/api'
import { listAis } from '../../services/api'
import { readStoredAvatar, readStoredName } from '../../services/userProfile'
import { dedupeAisForDisplay, readStoredAiId } from '../../services/bootstrapAi'
import { useAuthAccount } from '../../services/useAuthAccount'
import HubAdvancedPanel from './HubAdvancedPanel'
import HubAdvancedSettingsPanel from './HubAdvancedSettingsPanel'
import HubLoginPanel from './HubLoginPanel'
import HubModelsPanel, { FREE_MODEL_NOTICE_BODY, FREE_MODEL_NOTICE_TITLE } from './HubModelsPanel'
import HubProfilePanel from './HubProfilePanel'
import HubSettingsPanel from './HubSettingsPanel'
import './user-hub.css'

/** 产品问卷反馈表单（飞书共享问卷，新窗口打开）。 */
const FEEDBACK_SURVEY_URL =
  'https://genuineknowledge.feishu.cn/share/base/form/shrcn7pp47SeGec2M4Srnbt75Rg?from=navigation'

export type HubPanel = 'profile' | 'models' | 'login' | 'settings' | 'settingsAdvanced' | 'advanced' | null

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
  /**
   * 登录软门禁结束（登录成功或用户选择「暂不登录」）。父层据此放行首屏引导。
   * 只在门禁触发的那次开窗后回调；用户平时自己点开登录面板不影响首屏流程。
   */
  onLoginGateDone?: () => void
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
  onLoginGateDone,
}: Props) {
  // 头像改成弹菜单(资料 / 登录)后需要这两个: rootRef 判点击是否落在菜单外。
  const rootRef = useRef<HTMLDivElement | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [panel, setPanel] = useState<HubPanel>(null)
  const [userName, setUserName] = useState(readStoredName)
  const [userAvatar, setUserAvatar] = useState(readStoredAvatar)
  const [aiCount, setAiCount] = useState(0)
  const [freeModelNoticeOpen, setFreeModelNoticeOpen] = useState(false)
  const auth = useAuthAccount()

  useEffect(() => {
    if (!openModelsOnMount) return
    setPanel('models')
    onModelsAutoOpened?.()
  }, [openModelsOnMount, onModelsAutoOpened])

  /* 这次开的登录窗是不是门禁开的。父层的 onLoginGateDone 只该在门禁那次开窗
   * 关闭时回调一次 —— 用户平时自己点开登录面板再关掉，不该重放首屏引导判定。 */
  const loginFromGateRef = useRef(false)
  useEffect(() => {
    if (!openPanelRequest) return
    if (openPanelRequest.panel === 'login') loginFromGateRef.current = true
    setPanel(openPanelRequest.panel)
  }, [openPanelRequest])

  /** 关闭登录面板：若是门禁开的，通知父层放行。 */
  const closeLoginPanel = () => {
    setPanel(null)
    if (loginFromGateRef.current) {
      loginFromGateRef.current = false
      onLoginGateDone?.()
    }
  }

  useEffect(() => {
    void listAis()
      .then((list) => {
        const shown = dedupeAisForDisplay(list, selectedAiId ?? readStoredAiId())
        setAiCount(shown.length)
      })
      .catch(() => {})
  }, [selectedAiId])

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
      if (menuOpen) setMenuOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [freeModelNoticeOpen, panel, menuOpen])

  /* 云端账号优先于本地昵称: 登录后侧栏必须显示账号身份, 否则用户看不出自己
   * 已登录(原型 D4「侧栏账户区就地更新为已登录」)。未登录时回落本地昵称。 */
  const loggedIn = Boolean(auth.status?.available && auth.status?.loggedIn)
  const cloudName = auth.user?.displayName?.trim() ?? ''
  const shownName = (loggedIn && cloudName) || userName.trim()
  const initial = shownName.charAt(0).toUpperCase()
  const displayName = shownName || '用户'

  const openPanel = (next: HubPanel) => {
    setPanel(next)
    setMenuOpen(false)
  }

  return (
    <div className="user-hub" ref={rootRef}>
      <a
        className="user-hub-feedback"
        href={FEEDBACK_SURVEY_URL}
        target="_blank"
        rel="noopener noreferrer"
      >
        <ClipboardList size={15} aria-hidden="true" />
        <span>反馈问卷</span>
        <ExternalLink size={13} aria-hidden="true" />
      </a>
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

      {menuOpen && (
        <div className="user-hub-menu" role="menu">
          <button type="button" role="menuitem" onClick={() => openPanel('profile')}>
            <UserRound size={15} /> 我的资料
          </button>
          {/* 已登录后这一项要变成「账户」: 仍写「登录账号」会让用户以为没登上,
              点进去却是账户面板 —— 入口与落点对不上。 */}
          <button type="button" role="menuitem" onClick={() => openPanel('login')}>
            {loggedIn ? <UserCog size={15} /> : <LogIn size={15} />}
            {loggedIn ? ' 账户与设备' : ' 登录账号'}
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
        onFreeModelNotice={() => setFreeModelNoticeOpen(true)}
        onAisChanged={(ais) => {
          setAiCount(dedupeAisForDisplay(ais, selectedAiId).length)
          onAisChanged?.(ais)
        }}
      />
      <HubLoginPanel
        show={panel === 'login'}
        onClose={closeLoginPanel}
        onToast={onToast}
        /* 门禁开的那次要有明确的「暂不登录，继续使用」出口 —— 只给一个 ✕
           会让用户以为必须登录 */
        showSkip={loginFromGateRef.current}
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
