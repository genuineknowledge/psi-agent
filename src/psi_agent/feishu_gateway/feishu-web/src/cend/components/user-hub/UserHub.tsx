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
   * 登录门禁结束（只可能是登录成功 —— 硬门禁没有「暂不登录」出口）。父层据此
   * 放行首屏引导。只在门禁那次开窗后回调；用户平时自己点开登录面板不影响首屏。
   */
  onLoginGateDone?: () => void
  /**
   * 硬门禁进行中：登录面板锁死在最上层, 关不掉也切不走, 直到登录成功。
   * 父层探到「认证可用且未登录」时置真。
   */
  loginRequired?: boolean
  /**
   * 登录态在登录面板内变过（目前只有登出）。父层据此重新判门禁 ——
   * 登出后必须重新拦住，否则登录窗上会冒出 ✕、点遮罩也能关掉。
   */
  onLoginStateChanged?: () => void
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
  loginRequired = false,
  onLoginStateChanged,
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

  /**
   * 关闭登录面板：若是门禁开的，通知父层放行。
   *
   * 硬门禁下这个函数只会被「登录成功」那条路径调到（✕ 与遮罩已被 blocking 摘掉），
   * 所以不需要在这里再判一次 loginRequired —— 父层收到回调后会自行复查登录态。
   */
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
      // 硬门禁：Esc 不是出口，否则一按就把登录窗关了，门形同虚设。
      if (loginRequired) return
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
  }, [freeModelNoticeOpen, panel, menuOpen, loginRequired])

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
        /* 硬门禁期间强制显示, 不受 panel 影响: 否则用户点侧栏别的入口
           (模型池/设置)就把登录窗顶掉了, 门只拦得住第一下。 */
        show={loginRequired || panel === 'login'}
        onClose={closeLoginPanel}
        onToast={onToast}
        mandatory={loginRequired}
        /* 登出发生在面板内部, 父层不知道 —— 不透上去的话门禁只在冷启动那一下存在。 */
        onLoginStateChanged={onLoginStateChanged}
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
