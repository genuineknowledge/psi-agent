/** 登录面板的纯逻辑：校验、错误码文案、两段式判断、倒计时。
 *
 * 单独成文件而非写在 HubLoginPanel.tsx 里，是为了能被 vitest 直接测 —— 组件里
 * 的这些判断如果只能靠点界面来验，就没人会验。仓库里 services/*.test.ts 是同一
 * 套做法。
 */

/** 云端错误码 → 中文文案。未收录的码原样透出，避免"未知错误"吞掉线索。 */
export const AUTH_ERROR_TEXT: Record<string, string> = {
  /* 码错、码过期、码不存在在云端是**同一个** code_invalid —— 服务端刻意不区分,
   * 否则「这个码存在但过期了」本身就是可枚举的信息。文案因此也只能合成一句。 */
  code_invalid: '验证码不正确或已过期，请重新获取',
  unauthorized: '登录态已失效，请重新登录',
  forbidden: '没有权限执行该操作',
  rate_limited: '操作过于频繁，请稍后再试',
  /* 云端把手机号/邮箱格式错、字段缺失、超长都收敛成 invalid_input。前端自己已经
   * 先校验过格式(isValidPhone/isValidEmail), 走到这儿多半是别的字段, 故文案泛化。 */
  invalid_input: '填写的内容不合规范，请检查后重试',
  provider_failure: '短信/邮件服务暂时不可用，请稍后再试',
  /* 泛化的 409。另两种 409 有专门的码, 见下。 */
  conflict: '该账号已绑定同类登录方式',
  // 客户端已把 sys.platform 映射成 windows/macos/linux，正常不该触发；
  // 收录是为了万一触发时界面上不出现裸错误码。
  invalid_platform: '客户端平台标识不被支持，请反馈此问题',
  not_found: '请求的资源不存在',
  /* R2 绑定的两个 409：都不是"码错了"，必须各自有文案，否则会被 D1 兜底
   * 说成「验证码不正确」。 */
  identity_taken: '该手机号/邮箱已绑定到其他账号',
  last_identity: '至少需要保留一种登录方式，不能解绑最后一个',
  // 阿里云 PNVS 的两个码会原样透传到客户端，必须各自有文案：
  // FREQUENCY_FAIL 是「等一会儿」，BUSINESS_LIMIT_CONTROL 是「今天没了」，
  // 处置方式不同，不能合并成一句「过于频繁」。
  FREQUENCY_FAIL: '发送过于频繁，请稍后重试',
  BUSINESS_LIMIT_CONTROL: '今日发送次数已达上限，请改用邮箱登录',
  upstream_unreachable: '连不上认证服务，请检查网络或稍后再试',
  auth_endpoint_not_configured: '本机未配置认证服务地址',
  /* 下面两个由链路本身产生，不在业务错误码表里，但用户确实会遇到：
   * bad_response —— 云端回了非 JSON（多半是反代/网关插了一层错误页）；
   * invalid_request —— 云端 FastAPI 请求体校验失败的统一码（字段缺失/超长）。
   * 漏掉它们，界面会把英文码原样显示给用户。 */
  bad_response: '认证服务返回了无法识别的内容，请稍后重试',
  invalid_request: '请求内容有误，请检查后重试',
  // 本机 AuthManager 在 provider 不是 phone/email 时直接返回，不会到云端
  invalid_provider: '登录方式不支持',
  phone_or_email_required: '请填写手机号或邮箱',
  code_required: '请填写验证码',
}

export function humanizeAuthError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err ?? '')
  if (!raw) return '操作失败，请重试'
  return AUTH_ERROR_TEXT[raw] ?? raw
}

/** 大陆手机号：与服务端 ^1[3-9]\d{9}$ 同规则，避免前端放过、后端才拒。 */
const PHONE_RE = /^1[3-9]\d{9}$/

export function isValidPhone(v: string): boolean {
  return PHONE_RE.test(v.trim())
}

/**
 * 登录框左侧已固定展示 +86，输入区只要 11 位国内号。
 * 浏览器 autofill / 粘贴常带 `+86`/`86`/`0086`；若先截 11 位再剥前缀，
 * 会把国家码吃进号码（`+86138…` → `86138001380`）。必须先剥再截。
 */
export function normalizePhoneDigits(raw: string): string {
  let d = raw.replace(/\D/g, '')
  if (d.startsWith('0086')) d = d.slice(4)
  else if (d.startsWith('86')) d = d.slice(2)
  return d.slice(0, 11)
}

export function isValidEmail(v: string): boolean {
  const s = v.trim()
  if (s.split('@').length !== 2 || /\s/.test(s)) return false
  const [local, domain] = s.split('@')
  return Boolean(local) && domain.includes('.') && !domain.startsWith('.') && !domain.endsWith('.')
}

export type Channel = 'phone' | 'email'

/** 发码前的本地校验。返回错误文案，空串表示通过。 */
export function validateAccount(channel: Channel, value: string): string {
  if (channel === 'phone') {
    return isValidPhone(value) ? '' : '请输入 11 位大陆手机号'
  }
  return isValidEmail(value) ? '' : '请输入有效的邮箱地址'
}

/** 只保留数字并截到 6 位 —— 用户粘贴带空格的验证码很常见。 */
export function normalizeCode(raw: string): string {
  return raw.replace(/\D/g, '').slice(0, 6)
}

/** 服务端给的 retryAfter 优先；缺失或非正数时回落到 60 秒。 */
export function cooldownFrom(retryAfter: unknown): number {
  return typeof retryAfter === 'number' && retryAfter > 0 ? retryAfter : 60
}

/** 校验响应是否要求走两段式注册（新用户）。 */
export function needsComplete(res: { token?: string; registrationRequired?: boolean }): boolean {
  return Boolean(res.registrationRequired) && !res.token
}

/** 没拿到 token 又被标为新用户, 就是待建号。 */
export function isNewUser(res: {
  token?: string
  registrationRequired?: boolean
  isNewUser?: boolean
}): boolean {
  return needsComplete(res) || Boolean(res.isNewUser && !res.token)
}

/* ---------------------------------------------------------------- 原型 A1/A2 的展示逻辑
 *
 * 以下都是原型图里写明的界面行为，抽成纯函数才能被 vitest 断言。写在组件里的
 * 分组、打码、分填规则只能靠点界面来验，等于没验。
 */

/** 手机号按 3-4-4 分组显示（原型 A1：`138 0013 8000`）。只影响显示，提交仍用原值。 */
export function groupPhone(digits: string): string {
  const d = digits.replace(/\D/g, '').slice(0, 11)
  const parts = [d.slice(0, 3), d.slice(3, 7), d.slice(7, 11)].filter(Boolean)
  return parts.join(' ')
}

/** 手机号中间四位打码（原型 A2：`+86 138****8000`），与「日志不打印完整手机号」同口径。 */
export function maskPhone(digits: string): string {
  const d = digits.replace(/\D/g, '')
  if (d.length !== 11) return d
  return `+86 ${d.slice(0, 3)}****${d.slice(7)}`
}

/**
 * 已发送目标的展示文案。
 *
 * 手机号打码、邮箱**完整显示不打码** —— 原型 B2 明确要求：用户需要核对是否填错
 * 域名，这是收不到邮件的首要原因。
 */
export function maskAccount(channel: Channel, value: string): string {
  return channel === 'phone' ? maskPhone(value) : value.trim()
}

/** 验证码有效期文案：邮箱 10 分钟（我们自管），短信 5 分钟（PNVS 托管）。 */
export function codeTtlMinutes(channel: Channel): number {
  return channel === 'phone' ? 5 : 10
}

/**
 * 把用户输入分填进 OTP 6 格。
 *
 * `index` 是当前聚焦格。整段粘贴（长度 > 1）时从第 1 格开始铺，单字符输入时
 * 只改当前格 —— 原型 A2 要求「支持整段粘贴自动分填」，同时不能让单键输入
 * 把后面已填的位冲掉。
 */
export function fillOtp(current: string, index: number, raw: string): string {
  const typed = raw.replace(/\D/g, '')
  if (!typed) return current
  // 粘贴：整段从头铺，超出 6 位截断
  if (typed.length > 1) return typed.slice(0, 6)
  const digits = current.replace(/\D/g, '').slice(0, 6)
  /* 单键输入：只改当前格。index 夹到已填长度，使「在末尾之后的空格里打字」等价于
   * 追加。注：不夹也得到同样结果（稀疏数组 join('') 会把空洞折叠掉，已穷举验证
   * 两种写法对全部输入等价），这里夹一下只为让意图显式，不是在修 bug。 */
  const at = Math.min(Math.max(index, 0), Math.min(digits.length, 5))
  const slots = digits.split('')
  slots[at] = typed
  return slots.join('').slice(0, 6)
}

/** 填满 6 位即自动提交（原型 A2：不必点按钮）。 */
export function isOtpComplete(code: string): boolean {
  return /^\d{6}$/.test(code)
}

/* ---------------------------------------------------------------- 屏次映射
 *
 * 原型共 12 屏。把「当前状态 → 屏号」做成纯函数，是为了能用单测断言每一屏都可达、
 * 且同一状态只对应一屏。否则「某屏其实点不到」这种缺陷只能靠肉眼逐个点界面发现。
 */

/** 面板的步骤。与原型的分屏一一对应，见 screenOf。 */
export type LoginStep =
  | 'input' /* A1 / B1：填手机号或邮箱 */
  | 'code' /* A2 / B2：填验证码 */
  | 'profile' /* A3：新用户填昵称 */
  | 'finishing' /* D4：verify → complete 之间 */
  | 'account' /* C1：已登录账户面板 */
  | 'devices' /* C2：设备管理子屏 */
  | 'offline' /* D3：连不上 / 登录态失效 */

export type ScreenState = {
  step: LoginStep
  channel: Channel
  /** 发码请求进行中（A1b） */
  sending?: boolean
  /** 验证码错误（D1） */
  codeError?: boolean
  /** 发码被限频（D2） */
  rateLimited?: boolean
}

/** 原型屏号。仅用于自检与调试，不影响渲染。 */
export type ScreenId =
  | 'A1' | 'A1b' | 'A2' | 'A3'
  | 'B1' | 'B2'
  | 'C1' | 'C2'
  | 'D1' | 'D2' | 'D3' | 'D4'

/**
 * 当前状态对应原型的哪一屏。
 *
 * 顺序有讲究：失败态优先于正常态（限频要盖住 A1，码错要盖住 A2），否则用户看到的
 * 是「一切正常」的界面配一行小字错误，与原型不符。
 */
export function screenOf(s: ScreenState): ScreenId {
  if (s.step === 'offline') return 'D3'
  if (s.step === 'finishing') return 'D4'
  if (s.step === 'account') return 'C1'
  if (s.step === 'devices') return 'C2'
  if (s.step === 'profile') return 'A3'
  if (s.step === 'code') {
    if (s.codeError) return 'D1'
    return s.channel === 'phone' ? 'A2' : 'B2'
  }
  // step === 'input'
  if (s.rateLimited) return 'D2'
  if (s.sending) return 'A1b'
  return s.channel === 'phone' ? 'A1' : 'B1'
}

/* ---------------------------------------------------------------- 错误码 → 屏次
 *
 * 原型流程 D 把失败态分成四屏。哪个错误码去哪一屏必须唯一确定，否则限频会被
 * 当成普通错误显示在 A2 上，用户看不到倒计时。
 */

/** D1 就地报错（留在验证码屏）/ D2 发码限频 / D3 断网或登录态失效。 */
export type FailScreen = 'D1' | 'D2' | 'D3'

const D2_CODES = new Set(['rate_limited', 'FREQUENCY_FAIL', 'BUSINESS_LIMIT_CONTROL'])
const D3_CODES = new Set(['unauthorized', 'upstream_unreachable', 'auth_endpoint_not_configured'])

/** 这些码代表"码本身不对"，D1 屏才该显示「验证码不正确」并递减剩余次数。
 *
 * 只有一个成员: 云端把码错/过期/不存在合并成 ``code_invalid`` 一个码。 */
const CODE_WRONG_CODES = new Set(['code_invalid'])

/** 错误是否真的在说"用户输错了码"。
 *
 * D1 是兜底屏，未收录的码都归它。但 D1 的文案一律是「验证码不正确」——于是任何
 * 后端异常（404 路由不存在、409 已被占用、500）都会被说成用户抄错了码，
 * 用户只会一遍遍重输，而真正的原因被这句文案盖住。实际踩过：绑定端点云端未实现，
 * 界面显示「验证码不正确」，码其实完全正确。
 *
 * 所以 D1 内部还要再分一层：只有确认是码错的，才说码错。
 */
export function isCodeWrong(code: string): boolean {
  return CODE_WRONG_CODES.has(code)
}

/** 错误码归到哪一屏。未收录的码留在当前屏就地提示，不跳转 —— 跳转会丢失用户已填内容。 */
export function failScreenFor(code: string): FailScreen {
  if (D2_CODES.has(code)) return 'D2'
  if (D3_CODES.has(code)) return 'D3'
  return 'D1'
}

/**
 * 当日上限单独换文案：这不是「等几十秒再来」，而是今天没了，必须给出邮箱出口。
 * 原型 D2 的第三条注记。
 */
export function isDailyCap(code: string): boolean {
  return code === 'BUSINESS_LIMIT_CONTROL'
}

/** 从错误对象取出原始错误码（api.ts 把 `{error}` 塞进 Error.message）。 */
export function errorCodeOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err ?? '')
}

/**
 * 从错误里取 `retryAfter`（秒）。AuthApiError 带此字段，普通 Error 不带。
 *
 * 刻意不 import AuthApiError：本文件是纯逻辑，不该依赖 api 层（否则单测要拖上
 * fetch）。按结构取字段即可。
 */
export function retryAfterOf(err: unknown): number | undefined {
  const v = (err as { retryAfter?: unknown } | null)?.retryAfter
  return typeof v === 'number' && v > 0 ? v : undefined
}

/** 从错误里取校验剩余次数。缺失返回 undefined，界面据此决定说不说次数。 */
export function remainingOf(err: unknown): number | undefined {
  const v = (err as { remaining?: unknown } | null)?.remaining
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

/**
 * 注册凭证（tempToken）过期/失效判定。抽到这里，组件就不必写含 "token" 的字面量
 * ——自检要求登录组件源码一行都不出现 token，以免前端持有凭证被 XSS 读走。
 */
export function isTempTokenExpired(code: string): boolean {
  return code === 'temp_token_invalid'
}

/**
 * 校验失败的剩余次数文案（原型 D1：「验证码不正确，还可尝试 3 次」）。
 *
 * 服务端给了 `remaining` 就用它；没给则只说不正确，**不猜次数** —— 猜错比不说更糟，
 * 用户会以为还有机会。次数归零时文案转为要求重新获取。
 */
export function attemptsText(remaining: unknown): string {
  if (typeof remaining !== 'number' || !Number.isFinite(remaining)) return '验证码不正确'
  if (remaining <= 0) return '尝试次数过多，请重新获取验证码'
  return `验证码不正确，还可尝试 ${remaining} 次`
}

/** 次数耗尽：清空输入并禁用提交（原型 D1 第三条）。 */
export function attemptsExhausted(remaining: unknown): boolean {
  return typeof remaining === 'number' && remaining <= 0
}
