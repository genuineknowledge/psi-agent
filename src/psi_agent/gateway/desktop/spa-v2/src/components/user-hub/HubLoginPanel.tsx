import { useCallback, useEffect, useMemo, useState } from 'react'
import { Loader2, Mail, Monitor, Smartphone, Trash2 } from 'lucide-react'

import { notifyAuthChanged } from '../../services/useAuthAccount'

import {
  authLogout,
  bindAuthIdentity,
  completeAuth,
  getAuthMe,
  getAuthStatus,
  listAuthDevices,
  revokeAuthDevice,
  sendAuthCode,
  unbindAuthIdentity,
  verifyAuthCode,
  type AuthDevice,
  type AuthIdentity,
  type AuthStatus,
  type AuthUser,
} from '../../services/api'
import {
  attemptsExhausted,
  codeTtlMinutes,
  cooldownFrom,
  errorCodeOf,
  failScreenFor,
  groupPhone,
  humanizeAuthError as humanize,
  isCodeWrong,
  isDailyCap,
  isOtpComplete,
  isTempTokenExpired,
  maskAccount,
  needsComplete,
  normalizePhoneDigits,
  remainingOf,
  retryAfterOf,
  validateAccount,
  type Channel,
} from '../../services/authFlow'
import HubDialog from './HubDialog'
import HubOtpInput from './HubOtpInput'
import { useI18n } from '../../i18n'

type Props = {
  show: boolean
  onClose: () => void
  /** 登录成功后提示一句。原型 D4：不插「登录成功」屏，用 toast 交代结果。 */
  onToast?: (message: string) => void
  /**
   * 硬门禁：登录是使用本产品的前置条件（C 端默认模型走云端转发，未登录拿不到
   * 可用的 key）。为真时不给「暂不登录」出口、不给 ✕、点遮罩与 Esc 都不关窗,
   * 唯一出口是登录成功。用户自己从侧栏点开时为假（那时他已登录或想看账户）。
   */
  mandatory?: boolean
  /**
   * 登录态在本面板内变过（登出成功 / 本设备被移除）。
   *
   * 硬门禁是父层按 `/auth/status` 定的, 而**登出发生在本面板内部** —— 不往上说
   * 一声, 父层的 `authGate` 会一直停在启动那次探到的 `passed`, 于是登出后登录窗
   * 就有了 ✕、点遮罩也能关掉, 门等于只在冷启动那一下存在。
   */
  onLoginStateChanged?: () => void
}

/**
 * input=A1/B1 输入账号；code=A2/B2/D1 验证码；complete=A3 建号；
 * finishing=D4 建号收尾（不可中断）；done=C1/C2 已登录。
 */
type Stage = 'input' | 'code' | 'complete' | 'finishing' | 'done'

/**
 * 每条链路各自的输入与倒计时。
 *
 * 原型 B1 明确要求「Tab 切换不清空另一侧已填内容」「两条链路各自独立维护倒计时」。
 * 用单个 account + 单个 cooldown 做不到：切到邮箱会继承手机号那条的剩余秒数，
 * 界面显示「重新获取（43s）」却从没给这个邮箱发过码。
 */
type ChannelState = { account: string; cooldown: number }

const EMPTY_CHANNEL: ChannelState = { account: '', cooldown: 0 }

// public/ 资源由 Vite 从站点根提供，不走打包器
// 必须走 BASE_URL: 这个 SPA 挂在 `/spa-v2/` 下, 写死 `/haitun-dolphin.png`
// 会打到站点根目录, 404 出一个碎图标。
const DOLPHIN = `${import.meta.env.BASE_URL}haitun-dolphin.png`

export default function HubLoginPanel({
  show,
  onClose,
  onToast,
  mandatory = false,
  onLoginStateChanged,
}: Props) {
  const { t } = useI18n();
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [channel, setChannel] = useState<Channel>('phone')
  // 两条链路各自留一份，切 Tab 不互相清（原型 B1）
  const [phoneCh, setPhoneCh] = useState<ChannelState>(EMPTY_CHANNEL)
  const [emailCh, setEmailCh] = useState<ChannelState>(EMPTY_CHANNEL)
  const [code, setCode] = useState('')
  const [stage, setStage] = useState<Stage>('input')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('') // D1 就地错误文案
  const [fail, setFail] = useState<'' | 'D2' | 'D3'>('') // D2 限频 / D3 断网
  const [dailyCap, setDailyCap] = useState(false)
  const [displayName, setDisplayName] = useState('')
  const [me, setMe] = useState<{ user: AuthUser; identities: AuthIdentity[] } | null>(null)
  const [devices, setDevices] = useState<AuthDevice[]>([])
  const [manageDevices, setManageDevices] = useState(false)
  // 绑定第二身份(R2)：非空时走绑定子流程，复用输入/验证码两屏，校验改调 bind
  const [bindMode, setBindMode] = useState<Channel | null>(null)

  // 当前通道的输入与倒计时。写入走 patchChannel，读出走这两个派生值。
  const account = channel === 'phone' ? phoneCh.account : emailCh.account
  const cooldown = channel === 'phone' ? phoneCh.cooldown : emailCh.cooldown

  const patchChannel = useCallback(
    (which: Channel, patch: Partial<ChannelState>) => {
      const set = which === 'phone' ? setPhoneCh : setEmailCh
      set((s) => ({ ...s, ...patch }))
    },
    [],
  )

  const setAccount = useCallback(
    (v: string) => patchChannel(channel, { account: v }),
    [channel, patchChannel],
  )

  /* 倒计时：两条链路同时递减，各自到 0 即停。秒数取服务端 retryAfter，前端不拍数字。
   * 用单个 interval 而非两个 timeout，避免切 Tab 时定时器被卸载导致另一条停摆。 */
  useEffect(() => {
    if (phoneCh.cooldown <= 0 && emailCh.cooldown <= 0) return
    const t = window.setInterval(() => {
      setPhoneCh((s) => (s.cooldown > 0 ? { ...s, cooldown: s.cooldown - 1 } : s))
      setEmailCh((s) => (s.cooldown > 0 ? { ...s, cooldown: s.cooldown - 1 } : s))
    }, 1000)
    return () => window.clearInterval(t)
  }, [phoneCh.cooldown, emailCh.cooldown])

  // 限频黄条随倒计时归零自动撤掉，不留一条已过期的警告
  useEffect(() => {
    if (cooldown === 0 && fail === 'D2') setFail('')
  }, [cooldown, fail])

  /**
   * 返回「现在是否登录态」，调用方据此决定是留在账户面板还是关窗回工作台。
   *
   * `enterAccount` 为假时只探登录态，**不切到账户面板、也不拉 me 与设备列表**。
   * 马上要关窗的路径必须传假：默认那条会先 `setStage('done')` 把 C1 渲染出来，
   * 再等两个请求回来才关窗 —— 用户看到的是一个只有窗框和标题、内容空着的账户面板
   * 闪一秒多，正是原型 D4 禁止的「插一屏」。少掉的这两个请求下次开窗会补上。
   */
  const refresh = useCallback(async (enterAccount = true): Promise<boolean> => {
    try {
      const st = await getAuthStatus()
      setStatus(st)
      if (st.available && st.loggedIn) {
        // 广播要在最前面: 关窗路径靠它让侧栏账户区就地变成已登录, 而这条路径
        // 下面的 me/devices 都不取。
        notifyAuthChanged()
        if (!enterAccount) return true
        setStage('done')
        const [info, devs] = await Promise.all([
          getAuthMe().catch(() => null),
          listAuthDevices().catch(() => ({ devices: [] as AuthDevice[] })),
        ])
        setMe(info)
        setDevices(Array.isArray(devs?.devices) ? devs.devices : [])
        return true
      }
      setStage('input')
      setMe(null)
      setDevices([])
      notifyAuthChanged()
      return false
    } catch (e) {
      // 这里刻意**不**动 status：它可能存着上一次成功探到的值，清掉会让界面
      // 在一次网络抖动后丢失已知信息。body 选择靠 D3 抢在 status === null
      // 之前来保证能渲染出错误屏（见下方注释）。
      setFail('D3')
      setError(authErrorText(e))
      return false
    }
  }, [])

  // 每次打开都重新探：可能已在别处登出，或云端撤销了本设备
  useEffect(() => {
    if (show) void refresh()
  }, [show, refresh])

  const backToInput = () => {
    setCode('')
    setError('')
    setFail('')
    setStage('input')
  }

  /**
   * 登录/建号成功的收尾：关窗回工作台 + 一句 toast。
   *
   * 原型 D4 明确「成功后对话框直接关闭并回到工作台，侧栏账户区就地更新为已登录
   * —— 不再插一屏『登录成功』」。停在账户面板会让用户以为还有一步要做。
   * 已登录用户从侧栏主动点进来看账户不走这里，那条路径本来就该停在 C1。
   */
  const finishAndClose = (message: string) => {
    setCode('')
    setDisplayName('')
    onToast?.(message)
    onClose()
  }
  /**
   * 切 Tab。两侧输入与倒计时各自保留（原型 B1 第一条）——
   * 只重置验证码与错误态，因为码是跟着某一条链路发的，换条链路后它无意义。
   */
  const switchChannel = (c: Channel) => {
    if (c === channel) return
    setChannel(c)
    backToInput()
  }

  const onSend = async () => {
    setError('')
    setFail('')
    const value = account.trim()
    const invalid = validateAccount(channel, value)
    if (invalid) {
      setError(validationText(invalid))
      return
    }
    setBusy(true)
    const sentOn = channel // 请求期间用户可能切 Tab，倒计时必须记回发码那条链路
    try {
      const res = await sendAuthCode({ [sentOn]: value })
      setStage('code')
      setCode('')
      patchChannel(sentOn, { cooldown: cooldownFrom(res.retryAfter) })
    } catch (e) {
      const c = errorCodeOf(e)
      const screen = failScreenFor(c)
      if (screen === 'D2') {
        setFail('D2')
        setDailyCap(isDailyCap(c))
        // 限频时服务端也给 retryAfter；缺失才回落 60s
        patchChannel(sentOn, { cooldown: cooldownFrom(retryAfterOf(e)) })
      } else if (screen === 'D3') {
        setFail('D3')
      } else {
        setError(authErrorText(e))
      }
    } finally {
      setBusy(false)
    }
  }

  const runVerify = useCallback(
    async (theCode: string) => {
      setError('')
      setBusy(true)
      try {
        const value = account.trim()
        if (bindMode) {
          // 绑定第二身份：已登录态，校验走 bind，不签新会话；成功后回账户面板
          await bindAuthIdentity({ code: theCode, [bindMode]: value })
          setBindMode(null)
          setAccount('')
          await refresh()
          return
        }
        const res = await verifyAuthCode({ code: theCode, [channel]: value })
        if (needsComplete(res)) {
          // 新用户进 A3 建号（本期只收昵称，可留空由服务端给默认）。
          // 注册凭证由 api 层内部持有，组件不碰。
          setStage('complete')
          setBusy(false)
          return
        }
        // 老用户登录成功：关窗回工作台，不停在账户面板（原型 D4）
        // 传 false：不进账户面板, 否则关窗前会闪一下空的 C1
        if (await refresh(false)) finishAndClose(t('auth.toastLoggedIn'))
      } catch (e) {
        const c = errorCodeOf(e)
        const screen = failScreenFor(c)
        if (screen === 'D3') {
          setFail('D3')
        } else if (screen === 'D2') {
          /* 校验侧也有限频（R7：同号 5 次 / 300s）。这一支必须单列 ——
           * 归到下面的 D1 会把「校验太频繁」显示成「验证码不正确」，用户会以为
           * 是自己抄错了码，继续猛试，撞得更死。 */
          setError(authErrorText(e))
          setCode('')
        } else {
          /* D1 就地报错。但只有"确实是码错了"才说码错并递减次数 ——
           * D1 是兜底屏，404/409/500 之类都会落到这里，一律显示「验证码不正确」
           * 会把后端故障说成用户抄错码（绑定端点缺失时就这么坑过一次：码是对的，
           * 界面却说不正确）。其余错误按码给出真实文案。 */
          if (isCodeWrong(c)) {
            const remaining = remainingOf(e)
            setError(attemptsTextT(remaining))
            // 次数耗尽：清空已填，配合按钮禁用逼用户重新获取
            if (attemptsExhausted(remaining)) setCode('')
          } else {
            setError(authErrorText(e))
          }
        }
      } finally {
        setBusy(false)
      }
    },
    [account, channel, bindMode, refresh, setAccount],
  )

  /* 填满 6 位自动提交（原型 A2）。交给 HubOtpInput 的 onComplete 触发，
   * 它内部记住已提交过的码，同一个码不会因重渲染被打两次。放在 effect 里
   * 监听 code 会：错误后码仍是 6 位 → busy 变化触发重跑 → 重复请求。 */
  const onCodeChange = (next: string) => {
    if (error) setError('')
    setCode(next)
  }

  /** `skipName` 为真时不提交昵称（「稍后设置」），由服务端给默认值。 */
  const onComplete = async (skipName = false) => {
    setError('')
    // 进 D4：关闭按钮与遮罩点击一并失效，避免建号中途被打断留下悬空状态
    setStage('finishing')
    // 8s 无响应转 D3 并保留重试入口，不无限转圈（原型 D4 第二条）
    const slow = window.setTimeout(() => {
      setFail('D3')
      setError(t('auth.error.completeTimeout'))
    }, 8000)
    try {
      // 注册凭证由 api 层内部持有，此处只提交昵称，组件不碰凭证
      const wanted = skipName ? '' : displayName.trim()
      await completeAuth(wanted ? { displayName: wanted } : {})
      // 建号成功同样关窗回工作台，侧栏就地更新（原型 D4）
      // 同上传 false：D4 的转圈屏直接接关窗, 中间不插一屏空账户面板
      if (await refresh(false)) finishAndClose(t('auth.toastAccountCreated'))
    } catch (e) {
      if (isTempTokenExpired(errorCodeOf(e))) {
        // tempToken 过期（10 分钟）：退回 A1，说清要重新获取验证码
        setStage('input')
        setCode('')
        setError(t('auth.error.tempTokenExpired'))
      } else if (failScreenFor(errorCodeOf(e)) === 'D3') {
        setFail('D3')
        setError(authErrorText(e))
      } else {
        // 其余错误退回 A3，昵称还在，用户改一下即可重试
        setStage('complete')
        setError(authErrorText(e))
      }
    } finally {
      window.clearTimeout(slow)
    }
  }

  const onLogout = async () => {
    setBusy(true)
    setError('')
    try {
      await authLogout()
      setPhoneCh(EMPTY_CHANNEL)
      setEmailCh(EMPTY_CHANNEL)
      setManageDevices(false)
      setBindMode(null)
      setDisplayName('')
      backToInput()
      await refresh()
      /* 必须通知父层重新判门禁: 登出后就该重新被拦在登录窗里。放在 refresh() 之后
         是为了让本面板先回到输入屏 —— 父层随后把 mandatory 置真, 用户看到的是一个
         关不掉的输入屏, 而不是先闪一下账户面板。 */
      onLoginStateChanged?.()
    } catch (e) {
      setError(authErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  // 从账户面板发起「绑定第二身份」：进输入屏，channel 固定为待绑定的那种
  const startBind = (which: Channel) => {
    setBindMode(which)
    setChannel(which)
    setAccount('')
    setCode('')
    setError('')
    setFail('')
    setStage('input')
  }

  const onRevoke = async (id: string) => {
    setBusy(true)
    setError('')
    try {
      await revokeAuthDevice(id)
      await refresh()
    } catch (e) {
      setError(authErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  const onUnbind = async (provider: 'phone' | 'email') => {
    setBusy(true)
    setError('')
    try {
      await unbindAuthIdentity(provider)
      await refresh()
    } catch (e) {
      // 云端拦「解绑最后一个身份」会回 409，humanize 给出可读文案
      setError(authErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  const phoneValid = useMemo(() => validateAccount('phone', account.trim()) === '', [account])
  const emailValid = useMemo(() => validateAccount('email', account.trim()) === '', [account])
  const canSend = channel === 'phone' ? phoneValid : emailValid

  const authErrorText = (err: unknown): string => {
    const code = errorCodeOf(err)
    const key = `auth.error.${code}`
    const localized = t(key)
    if (localized !== key) return localized
    return humanize(err)
  }
  const validationText = (invalid: string): string => {
    if (invalid === '请输入 11 位大陆手机号') return t('auth.error.invalidPhone')
    if (invalid === '请输入有效的邮箱地址') return t('auth.error.invalidEmail')
    return invalid
  }
  const attemptsTextT = (remaining: unknown): string => {
    if (typeof remaining !== 'number' || !Number.isFinite(remaining)) return t('auth.error.codeWrong')
    if (remaining <= 0) return t('auth.error.attemptsExhausted')
    return t('auth.error.attemptsLeft', { count: remaining })
  }

  // ---- 品牌头（A1/B1；D3 灰度）----
  const brand = (offline = false) => (
    <div className={`hub-login-brand${offline ? ' offline' : ''}`}>
      <div className="mark">{offline ? null : <img src={DOLPHIN} alt="HaiTun" />}</div>
      <h3>{offline ? t('auth.offlineTitle') : t('auth.welcome')}</h3>
      <p>
        {/* 硬门禁下不能再说「本机功能不受影响」—— 登录成了使用前置条件, 断网时
            人是真进不去。原先那句是软门禁时代留下的, 会让用户以为可以绕过。 */}
        {offline
          ? mandatory
            ? t('auth.offlineMandatory')
            : t('auth.offlineLocal')
          : channel === 'phone'
            ? t('auth.verifyPhone')
            : t('auth.verifyEmail')}
      </p>
    </div>
  )

  const tabs = (
    <div className="hub-login-tabs" role="tablist">
      <button role="tab" aria-selected={channel === 'phone'} onClick={() => switchChannel('phone')}>
        {t('auth.phoneTab')}
      </button>
      <button role="tab" aria-selected={channel === 'email'} onClick={() => switchChannel('email')}>
        {t('auth.emailTab')}
      </button>
    </div>
  )

  /* 登录屏不放协议文字。先是必勾复选框, 后改成一行被动告知, 现按团队决定整句去掉。
   * `public/terms.html` 与 `public/privacy.html` 仍在包里, 但**界面上已无入口** ——
   * 要再挂回去的话, 挂在设置或关于页比堵在登录路径上合适。 */

  // ---- 屏 A1/B1：输入账号 ----
  const renderInput = () => (
    <>
      {bindMode ? (
        <div className="hub-tip info">
          <span className="ico">ⓘ</span>
          <span>{bindMode === 'phone' ? t('auth.bindPhoneTip') : t('auth.bindEmailTip')}</span>
        </div>
      ) : (
        <>
          {brand()}
          {tabs}
        </>
      )}
      {fail === 'D2' ? (
        <div className="hub-tip warn">
          <span className="ico">⚠</span>
          <span>
            {dailyCap
              ? t('auth.dailyCap')
              : t('auth.cooldown', { seconds: cooldown })}
          </span>
        </div>
      ) : null}
      {channel === 'phone' ? (
        <div className="hub-field">
          <div className="hub-login-control">
            <span className="cc">+86</span>
            <input
              value={groupPhone(account)}
              onChange={(e) => setAccount(normalizePhoneDigits(e.target.value))}
              placeholder="138 0013 8000"
              inputMode="numeric"
              /* tel-national：提示浏览器填国内号、别再塞 +86（左侧已有区号）。 */
              autoComplete="tel-national"
              disabled={busy}
              aria-label={t('auth.phoneAria')}
            />
            {account ? (
              <button type="button" className="clear" onClick={() => setAccount('')} aria-label={t('auth.clear')}>
                ✕
              </button>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="hub-field">
          <div className="hub-login-control">
            <input
              value={account}
              onChange={(e) => setAccount(e.target.value)}
              placeholder="you@example.com"
              inputMode="email"
              autoComplete="email"
              disabled={busy}
              aria-label={t('auth.emailAria')}
            />
            {account ? (
              <button type="button" className="clear" onClick={() => setAccount('')} aria-label={t('auth.clear')}>
                ✕
              </button>
            ) : null}
          </div>
        </div>
      )}
      <button
        type="button"
        className="hub-btn primary block"
        onClick={() => void onSend()}
        disabled={busy || !canSend || cooldown > 0}
      >
        {busy ? <Loader2 size={15} className="hub-spin" /> : null}
        {cooldown > 0 ? t('auth.resendCooldown', { seconds: cooldown }) : busy ? t('auth.sending') : t('auth.getCode')}
      </button>
      {error ? <p className="hub-login-err"><span>⊘</span><span>{error}</span></p> : null}
      {/* 硬门禁下没有「暂不登录」出口 —— 登录是使用前置条件。 */}
    </>
  )
  // ---- 屏 A2/B2/D1：输入验证码 ----
  const renderCode = () => (
    <>
      <p className="hub-login-sent-to">
        {t('auth.codeSentTo')} <b>{maskAccount(channel, account)}</b>
        <br />{t('auth.codeTtl', { minutes: codeTtlMinutes(channel) })}
      </p>
      <HubOtpInput
        value={code}
        onChange={onCodeChange}
        onComplete={(c) => void runVerify(c)}
        invalid={Boolean(error)}
        disabled={busy}
        autoFocus
      />
      {error ? (
        <p className="hub-login-err"><span>⊘</span><span>{error}</span></p>
      ) : null}
      {cooldown > 0 ? (
        <p className="hub-login-resend">{t('auth.resendCooldownShort', { seconds: cooldown })}</p>
      ) : (
        <button type="button" className="hub-login-resend" onClick={() => void onSend()} disabled={busy}>
          {t('auth.resendCode')}
        </button>
      )}
      <div style={{ marginTop: 16 }}>
        <button
          type="button"
          className="hub-btn primary block"
          onClick={() => void runVerify(code)}
          disabled={busy || !isOtpComplete(code)}
        >
          {busy ? <Loader2 size={15} className="hub-spin" /> : null} {t('auth.login')}
        </button>
      </div>
      <div className="hub-login-center">
        {channel === 'phone' ? (
          // 手机收不到码要给真出口：切到邮箱链路
          <button type="button" className="hub-link" onClick={() => switchChannel('email')}>
            {t('auth.noCodeUseEmail')}
          </button>
        ) : (
          /* 邮箱侧是纯文案不可点（原型 B2 第三条）：邮件送达率本期无监控，
           * 这句是唯一的自助手段，做成按钮会让人以为点了能重发。 */
          <p className="hub-login-resend">{t('auth.checkSpam')}</p>
        )}
      </div>
    </>
  )

  // ---- 屏 A3：新用户建号 ----
  const renderComplete = () => (
    <>
      <div className="hub-tip ok">
        <span className="ico">✓</span>
        <span>{channel === 'phone' ? t('auth.verifiedPhoneCreate') : t('auth.verifiedEmailCreate')}</span>
      </div>
      <div className="hub-field">
        <span>{t('auth.nickname')}</span>
        <input
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder={t('auth.nicknamePlaceholder')}
          disabled={busy}
          aria-label={t('auth.nickname')}
        />
      </div>
      <div className="hub-tip info">
        <span className="ico">ⓘ</span>
        <span>{t('auth.localDataNote')}</span>
      </div>
    </>
  )
  // ---- 屏 C1：已登录账户面板 ----
  const renderAccount = () => {
    if (!me) return null
    const name = me.user?.displayName || me.user?.id || t('auth.userFallback')
    const ids = me.identities ?? []
    const phone = ids.find((i) => i.provider === 'phone')
    const email = ids.find((i) => i.provider === 'email')
    // 至少保留一个登录方式：只有绑了 2 种时才允许解绑（否则解绑必被云端拦）
    const canUnbind = ids.length > 1
    return (
      <>
        <div className="hub-me">
          <div className="avatar">{name.slice(0, 1)}</div>
          <div>
            <h4>
              {name} <span className="hub-badge">{t('auth.loggedInBadge')}</span>
            </h4>
            <p>{phone ? maskAccount('phone', phone.identifier) : email?.identifier}</p>
          </div>
        </div>
        <div className="hub-sec-title">{t('auth.loginMethods')}</div>
        <div className="hub-rows">
          <div className="hub-row">
            <span className="ico2"><Smartphone size={15} /></span>
            <span className="txt">
              <b>{t('auth.phoneLabel')}</b>
              <span>{phone ? maskAccount('phone', phone.identifier) : t('auth.notBound')}</span>
            </span>
            {phone ? (
              canUnbind ? (
                <button type="button" className="hub-btn danger" onClick={() => void onUnbind('phone')} disabled={busy}>{t('auth.unbind')}</button>
              ) : (
                <span className="hub-badge">{t('auth.verified')}</span>
              )
            ) : (
              <button type="button" className="hub-btn primary soft" onClick={() => startBind('phone')}>{t('auth.bind')}</button>
            )}
          </div>
          <div className="hub-row">
            <span className="ico2"><Mail size={15} /></span>
            <span className="txt">
              <b>{t('auth.emailLabel')}</b>
              <span>{email ? email.identifier : t('auth.notBound')}</span>
            </span>
            {email ? (
              canUnbind ? (
                <button type="button" className="hub-btn danger" onClick={() => void onUnbind('email')} disabled={busy}>{t('auth.unbind')}</button>
              ) : (
                <span className="hub-badge">{t('auth.verified')}</span>
              )
            ) : (
              <button type="button" className="hub-btn primary soft" onClick={() => startBind('email')}>{t('auth.bind')}</button>
            )}
          </div>
        </div>
        <div className="hub-tip info" style={{ marginTop: 14 }}>
          <span className="ico">ⓘ</span>
          <span>{t('auth.localFilesNote')}</span>
        </div>
        {!status?.credentialEncrypted ? (
          <div className="hub-tip warn">
            <span className="ico">⚠</span>
            <span>{t('auth.keyringWarning')}</span>
          </div>
        ) : null}
        {error ? <p className="hub-login-err"><span>⊘</span><span>{error}</span></p> : null}
      </>
    )
  }

  // ---- 屏 C2：登录设备管理 ----
  const renderDevices = () => (
    <>
      <div className="hub-tip info">
        <span className="ico">ⓘ</span>
        <span>{t('auth.removeDeviceNote')}</span>
      </div>
      <div className="hub-rows">
        {devices.map((d) => (
          <div className={`hub-row${d.current ? ' cur' : ''}`} key={d.id}>
            <span className="ico2">
              {/* 桌面端三平台都是电脑，不按平台换图标；平台名已在下一行文字里 */}
              <Monitor size={15} />
            </span>
            <span className="txt">
              <b>
                {d.name || d.platform}
                {d.current ? <span className="hub-badge">{t('auth.thisDevice')}</span> : null}
              </b>
              <span>
                {t('auth.lastActive', { platform: d.platform, time: d.lastSeenAt || d.createdAt })}
              </span>
            </span>
            {d.current ? null : (
              <button
                type="button"
                className="hub-btn danger"
                onClick={() => void onRevoke(d.id)}
                disabled={busy}
                aria-label={t('auth.removeDeviceAria')}
              >
                <Trash2 size={14} /> {t('auth.remove')}
              </button>
            )}
          </div>
        ))}
      </div>
      {error ? <p className="hub-login-err"><span>⊘</span><span>{error}</span></p> : null}
    </>
  )

  // ---- 屏 D3：断网 / 登录态失效 ----
  const renderOffline = () => (
    <>
      {brand(true)}
      <div className="hub-tip bad">
        <span className="ico">⊘</span>
        <span>
          {mandatory
            ? t('auth.offlineMandatoryBody')
            : t('auth.offlineBody')}
        </span>
      </div>
      <button type="button" className="hub-btn primary block" onClick={() => void refresh()} disabled={busy}>
        {busy ? <Loader2 size={15} className="hub-spin" /> : null} {t('auth.retry')}
      </button>
      {/* 硬门禁下断网也不放行: 默认模型的 key 由云端按登录态下发, 放进去只会在
          第一次对话时报一个与产品无关的上游错误(见 gateway/desktop/_free_model.py)。 */}
      {mandatory ? null : (
        <div className="hub-login-center">
          <button type="button" className="hub-btn ghost" style={{ border: 0, background: 'none' }} onClick={onClose}>
            {t('auth.skipForNow')}
          </button>
        </div>
      )}
    </>
  )
  // ---- 标题随屏切换 ----
  let title = t('auth.titleLogin')
  if (bindMode) title = bindMode === 'phone' ? t('auth.titleBindPhone') : t('auth.titleBindEmail')
  else if (stage === 'complete') title = t('auth.titleComplete')
  else if (stage === 'code') title = t('auth.titleCode')
  else if (stage === 'finishing') title = t('auth.titleLogin')
  else if (stage === 'done') title = manageDevices ? t('auth.titleDevices') : t('auth.titleAccount')

  // ---- 页脚动作随屏切换 ----
  let actions: React.ReactNode = null
  if (stage === 'complete') {
    actions = (
      <>
        {/* 「稍后设置」= 不提交昵称，由服务端给默认值；「开始使用」= 提交所填。
            两者若都提交输入框内容，前者就名不副实（用户填了字还点它，字会被存下）。 */}
        <button type="button" className="hub-btn ghost" onClick={() => void onComplete(true)} disabled={busy}>
          {t('auth.setupLater')}
        </button>
        <button type="button" className="hub-btn primary" onClick={() => void onComplete()} disabled={busy}>
          {t('auth.startUsing')}
        </button>
      </>
    )
  } else if (stage === 'done' && !manageDevices && me) {
    actions = (
      <>
        <button type="button" className="hub-btn ghost" style={{ border: 0, background: 'none' }} onClick={() => setManageDevices(true)}>
          {t('auth.manageDevices', { count: devices.length })}
        </button>
        <span style={{ flex: 1 }} />
        <button type="button" className="hub-btn danger" onClick={() => void onLogout()} disabled={busy}>
          {t('auth.logout')}
        </button>
      </>
    )
  } else if (stage === 'done' && manageDevices) {
    actions = (
      <button type="button" className="hub-btn ghost" onClick={() => setManageDevices(false)}>
        {t('auth.back')}
      </button>
    )
  }

  // ---- body 选择 ----
  //
  // **D3 必须排在 status === null 之前。** refresh() 失败时只 setFail('D3')、
  // 不设 status，所以 status 仍是 null；若先判 null 就永远显示「正在检查登录
  // 状态…」——转圈转到底，而 renderOffline() 里的「重试」和「暂不登录，继续
  // 使用」两个出口永远到不了，用户既看不到原因也退不出去。
  let body: React.ReactNode
  if (fail === 'D3') {
    body = renderOffline()
  } else if (status === null) {
    body = (
      <div className="hub-login-loading">
        <div className="ring" />
        <p>{t('auth.checkingStatus')}</p>
      </div>
    )
  } else if (!status.available) {
    // /auth/status 不通：认证地址被显式关掉, 或网关版本旧于登录功能。
    // 给本地模式说明, 不给一个点不动的登录表单。
    body = (
      <>
        <p className="hub-login-body">
          {t('auth.localModePrefix')}<strong>{t('auth.localMode')}</strong>{t('auth.localModeSuffix')}
        </p>
        <p className="hub-login-hint">
          {t('auth.localModeHint')}
        </p>
      </>
    )
  } else if (stage === 'done') {
    body = manageDevices ? renderDevices() : renderAccount()
  } else if (stage === 'finishing') {
    // 屏 D4：建号收尾，不可中断
    body = (
      <div className="hub-login-loading">
        <div className="ring" />
        <p>{t('auth.preparingAccount')}</p>
      </div>
    )
  } else if (stage === 'complete') {
    body = renderComplete()
  } else if (stage === 'code') {
    body = renderCode()
  } else {
    body = renderInput()
  }

  // 绑定模式下返回 = 取消绑定回账户面板；否则验证码屏返回输入屏、设备屏返回账户
  const cancelBind = () => {
    setBindMode(null)
    setAccount('')
    setCode('')
    setError('')
    setStage('done')
  }
  /* 返回箭头放在弹窗标题栏（原型 A2 / C2 都画在标题左侧），不放在 body 里。
   * D4 期间不给返回 —— 与关闭按钮同理，建号中途退不得。 */
  const onBack =
    stage === 'finishing'
      ? undefined
      : bindMode
        ? cancelBind
        : stage === 'code'
          ? backToInput
          : manageDevices
            ? () => setManageDevices(false)
            : undefined

  return (
    <HubDialog
      show={show}
      title={title}
      width={400}
      onClose={onClose}
      actions={actions}
      onBack={onBack}
      /* 硬门禁与 D4 建号收尾都不可中断: 藏起 ✕、遮罩点击失效。 */
      blocking={stage === 'finishing' || mandatory}
    >
      {body}
    </HubDialog>
  )
}


