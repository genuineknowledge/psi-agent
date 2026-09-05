import { useCallback, useEffect, useState } from 'react'
import type { AuthIdentity, AuthStatus, AuthUser } from './api'
import { getAuthMe, getAuthStatus } from './api'

/**
 * 共享的登录态。
 *
 * 侧栏账户区和登录面板必须看到同一份登录态：面板自己 `useState` 存、侧栏读
 * localStorage 里的本地昵称时，登录成功后侧栏不会变（还显示「用户」和「登录
 * 账号」），点进去又是账户面板 —— 两处各说各话。
 *
 * 刻意不用 module 级可变全局（根 AGENTS.md 15 条），也不引 context provider：
 * 订阅者只有两三个，各自持有一份 + 事件广播已经够，且避免动 App 的组件树。
 */

export type AuthAccount = {
  status: AuthStatus | null
  user: AuthUser | null
  identities: AuthIdentity[]
  loading: boolean
}

const EVENT = 'psi-auth-changed'

/** 登录/登出/绑定成功后广播，让所有订阅者重新探一次。 */
export function notifyAuthChanged(): void {
  window.dispatchEvent(new Event(EVENT))
}

export function useAuthAccount(): AuthAccount & { refresh: () => Promise<void> } {
  const [state, setState] = useState<AuthAccount>({
    status: null,
    user: null,
    identities: [],
    loading: true,
  })

  const refresh = useCallback(async () => {
    try {
      const status = await getAuthStatus()
      if (!status.available || !status.loggedIn) {
        setState({ status, user: null, identities: [], loading: false })
        return
      }
      const me = await getAuthMe().catch(() => null)
      setState({
        status,
        user: me?.user ?? null,
        identities: me?.identities ?? [],
        loading: false,
      })
    } catch {
      // 探测失败不清 status：网络抖一下不该让界面丢掉已知身份。
      setState((s) => ({ ...s, loading: false }))
    }
  }, [])

  useEffect(() => {
    void refresh()
    const onChanged = () => void refresh()
    window.addEventListener(EVENT, onChanged)
    return () => window.removeEventListener(EVENT, onChanged)
  }, [refresh])

  return { ...state, refresh }
}
