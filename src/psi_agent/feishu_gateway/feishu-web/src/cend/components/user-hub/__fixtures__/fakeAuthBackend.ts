/** 登录界面冒烟测专用的假后端。**不在生产代码路径上**。
 *
 * 放在 __fixtures__ 下、且只被 *.test.tsx 用 vi.mock 注入，所以：
 *   - 打包器不会把它收进产物（无任何生产文件 import 它）
 *   - 里面的固定验证码不可能随安装包发出去
 *
 * 之前的写法是让 api.ts 静态 import 它、靠 `?authMock=1` 决定是否接管。那等于
 * 把「加个 URL 参数即进入假登录态」的旁路发给每个用户，且固定码确实被打进了
 * 产物（实测 dist 里能 grep 到）。现已改为测试替身。
 *
 * 真实登录链路不经过本文件：云端 9 个端点当前是 501 脚手架，provider 是
 * NotImplementedError —— 那是待实现的后端，不是这里的假数据能替代的。
 */

import type { AuthDevice, AuthIdentity, AuthStatus, AuthUser } from '../../../services/api'

/** 固定验证码。真发码不会用固定值，这里只为手工点界面。 */
export const MOCK_CODE = '123456'

/** 触发各失败态的约定输入。 */
export const SCENARIOS = {
  /** 手机号：发码即返回 429（D2 限频，retryAfter 48） */
  rateLimited: '13800000429',
  /** 手机号：发码即返回当日上限（D2 的另一套文案） */
  dailyCap: '13800000888',
  /** 手机号：发码即网络失败（D3） */
  offline: '13800000503',
  /** 手机号：已注册老用户，验证后直接登录（跳过 A3） */
  existing: '13800138000',
  /** 邮箱：已注册老用户 */
  existingEmail: 'old@example.com',
} as const



const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms))

type MockError = Error & { retryAfter?: number; remaining?: number }

function fail(code: string, extra: { retryAfter?: number; remaining?: number } = {}): never {
  const e = new Error(code) as MockError
  Object.assign(e, extra)
  throw e
}

/* ---------------------------------------------------------------- 会话状态 */

let loggedIn = false
let pendingTemp = ''
let pendingChannel: 'phone' | 'email' = 'phone'
let pendingId = ''
/** 校验剩余次数，用于演示 D1 的「还可尝试 N 次」并在耗尽后转文案。 */
let attemptsLeft = 5

const identities: AuthIdentity[] = []
let user: AuthUser = { id: '', displayName: null, avatarUrl: null, createdAt: '' }

const devices: AuthDevice[] = []

/** 设备列表的初始三台（原型 C2）。reset 时深拷回去，避免用例间互相污染。 */
function seedDevices(): AuthDevice[] {
  return [
    {
      id: 'dev-local',
      platform: 'windows',
      name: 'ZSD-WORKSTATION',
      createdAt: '2026-08-01T09:00:00Z',
      lastSeenAt: new Date().toISOString(),
      current: true,
    },
    {
      id: 'dev-mac',
      platform: 'macos',
      name: 'MacBook Pro',
      createdAt: '2026-07-20T09:00:00Z',
      lastSeenAt: new Date(Date.now() - 2 * 3600_000).toISOString(),
      current: false,
    },
    {
      id: 'dev-desk',
      platform: 'windows',
      name: 'DESKTOP-A19F',
      createdAt: '2026-07-01T09:00:00Z',
      lastSeenAt: new Date(Date.now() - 3 * 86400_000).toISOString(),
      current: false,
    },
  ]
}

/**
 * 恢复到「未登录、三台设备」的初始态。
 *
 * 状态挂在模块级，一次 import 全用例共享。不重置的话，前一个用例登录成功后
 * 下一个用例开局就是 C1，A1 根本渲染不出来 —— 表现为一串「找不到欢迎文案」，
 * 而真正的缺陷被这层噪音盖住。手工点界面时刷新页面即可，无需调用。
 */
export function reset(): void {
  loggedIn = false
  pendingTemp = ''
  pendingChannel = 'phone'
  pendingId = ''
  attemptsLeft = 5
  identities.length = 0
  user = { id: '', displayName: null, avatarUrl: null, createdAt: '' }
  devices.length = 0
  devices.push(...seedDevices())
}

/**
 * 直接置成「已登录」，不走发码校验。
 *
 * 给需要从 C1 起步的用例用（设备管理那几条）。**不能靠「在面板里登录一次」来到
 * C1** —— 登录成功那条路径按原型 D4 是关窗回工作台，压根不进 C1；侧栏点进来看
 * 账户才进。用登录流程铺垫等于把测试建在一个不存在的落点上。
 *
 * 字段与 `verify()` 的老用户支保持一致，两处要一起改。
 */
export function seedLoggedIn(): void {
  loggedIn = true
  user = {
    id: 'u-mock-1',
    displayName: '海豚用户 8000',
    avatarUrl: null,
    createdAt: '2026-07-01T09:00:00Z',
  }
  identities.length = 0
  identities.push({
    provider: 'phone',
    identifier: SCENARIOS.existing,
    verifiedAt: new Date().toISOString(),
  })
}

reset()

/* ---------------------------------------------------------------- 端点 */

export async function status(): Promise<AuthStatus> {
  await sleep(180)
  return {
    available: true,
    endpoint: 'mock://local',
    loggedIn,
    deviceKey: 'mock-device-key',
    platform: 'windows',
    // 故意为 true；想看「凭证未加密」告警把它改 false
    credentialEncrypted: true,
  }
}

export async function sendCode(body: { phone?: string; email?: string }): Promise<{ retryAfter: number }> {
  await sleep(600) // 够长，能看到 A1b 的按钮 spinner
  const id = body.phone ?? body.email ?? ''
  if (id === SCENARIOS.rateLimited) fail('rate_limited', { retryAfter: 48 })
  if (id === SCENARIOS.dailyCap) fail('BUSINESS_LIMIT_CONTROL', { retryAfter: 60 })
  if (id === SCENARIOS.offline) fail('upstream_unreachable')
  pendingChannel = body.phone ? 'phone' : 'email'
  pendingId = id
  attemptsLeft = 5
  return { retryAfter: 60 }
}

export async function verify(body: {
  code: string
  phone?: string
  email?: string
}): Promise<{
  token?: string
  registrationRequired?: boolean
  isNewUser?: boolean
  user?: AuthUser
}> {
  await sleep(500)
  if (body.code !== MOCK_CODE) {
    attemptsLeft -= 1
    if (attemptsLeft <= 0) fail('rate_limited') // 次数耗尽 → 校验侧限频（D2）
    fail('code_invalid', { remaining: attemptsLeft })
  }
  const id = body.phone ?? body.email ?? pendingId
  const known = id === SCENARIOS.existing || id === SCENARIOS.existingEmail
  if (known) {
    // 老用户：直接登录，不经 A3
    loggedIn = true
    user = {
      id: 'u-mock-1',
      displayName: '海豚用户 8000',
      avatarUrl: null,
      createdAt: '2026-07-01T09:00:00Z',
    }
    if (!identities.length) {
      identities.push({
        provider: pendingChannel,
        identifier: id,
        verifiedAt: new Date().toISOString(),
      })
    }
    return { token: 'mock-token', user }
  }
  // 凭证留在"网关"侧, 只回不含凭证的新用户标记 —— 与真实 /auth/verify 一致
  pendingTemp = 'mock-temp-token'
  return { registrationRequired: true, isNewUser: true }
}

export async function complete(body: { displayName?: string }): Promise<{ token: string; user: AuthUser }> {
  await sleep(700)
  if (!pendingTemp) fail('temp_token_invalid')
  pendingTemp = ''
  loggedIn = true
  user = {
    id: 'u-mock-new',
    displayName: body.displayName?.trim() || '海豚用户',
    avatarUrl: null,
    createdAt: new Date().toISOString(),
  }
  identities.length = 0
  identities.push({
    provider: pendingChannel,
    identifier: pendingId,
    verifiedAt: new Date().toISOString(),
  })
  return { token: 'mock-token', user }
}

export async function me(): Promise<{ user: AuthUser; identities: AuthIdentity[] }> {
  await sleep(150)
  if (!loggedIn) fail('unauthorized')
  return { user, identities: [...identities] }
}

export async function logout(): Promise<{ ok: boolean }> {
  await sleep(200)
  loggedIn = false
  identities.length = 0
  return { ok: true }
}

export async function listDevices(): Promise<{ devices: AuthDevice[] }> {
  await sleep(150)
  if (!loggedIn) fail('unauthorized')
  return { devices: [...devices] }
}

export async function revokeDevice(id: string): Promise<{ ok: boolean }> {
  await sleep(300)
  const i = devices.findIndex((d) => d.id === id)
  if (i < 0) fail('not_found')
  if (devices[i].current) fail('invalid_request') // 本机行本就不给移除按钮
  devices.splice(i, 1)
  return { ok: true }
}

export async function bind(body: { code: string; phone?: string; email?: string }): Promise<{ ok: boolean }> {
  await sleep(400)
  if (body.code !== MOCK_CODE) fail('code_invalid', { remaining: 4 })
  const provider = body.phone ? 'phone' : 'email'
  const identifier = body.phone ?? body.email ?? ''
  if (identities.some((x) => x.provider === provider)) fail('invalid_request')
  identities.push({ provider, identifier, verifiedAt: new Date().toISOString() })
  return { ok: true }
}

export async function unbind(provider: 'phone' | 'email'): Promise<{ ok: boolean }> {
  await sleep(300)
  // 与云端同规则：不能解绑最后一个身份，否则账号再也登不上
  if (identities.length <= 1) fail('conflict')
  const i = identities.findIndex((x) => x.provider === provider)
  if (i >= 0) identities.splice(i, 1)
  return { ok: true }
}
