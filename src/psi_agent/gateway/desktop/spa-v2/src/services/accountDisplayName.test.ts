import { describe, expect, it } from 'vitest'
import {
  isIdentityLikeDisplayName,
  resolveAccountDisplayName,
} from './accountDisplayName'

describe('isIdentityLikeDisplayName', () => {
  it('treats empty as unset', () => {
    expect(isIdentityLikeDisplayName('')).toBe(true)
    expect(isIdentityLikeDisplayName('  ')).toBe(true)
  })

  it('matches bound phone digits with or without +86', () => {
    const ids = [{ provider: 'phone', identifier: '+8613800138000' }]
    expect(isIdentityLikeDisplayName('13800138000', ids)).toBe(true)
    expect(isIdentityLikeDisplayName('+86 13800138000', ids)).toBe(true)
    expect(isIdentityLikeDisplayName('张三', ids)).toBe(false)
  })

  it('matches bound email case-insensitively', () => {
    const ids = [{ provider: 'email', identifier: 'User@Example.com' }]
    expect(isIdentityLikeDisplayName('user@example.com', ids)).toBe(true)
    expect(isIdentityLikeDisplayName('海豚', ids)).toBe(false)
  })

  it('flags bare CN mobile even without identities', () => {
    expect(isIdentityLikeDisplayName('13800138000')).toBe(true)
    expect(isIdentityLikeDisplayName('海豚用户')).toBe(false)
  })
})

describe('resolveAccountDisplayName', () => {
  const ids = [{ provider: 'phone', identifier: '13800138000' }]

  it('prefers real cloud nickname when logged in', () => {
    expect(
      resolveAccountDisplayName({
        loggedIn: true,
        cloudName: '小海豚',
        identities: ids,
        localName: '本地名',
        fallback: '用户',
      }),
    ).toBe('小海豚')
  })

  it('falls back to local when cloud name is the phone', () => {
    expect(
      resolveAccountDisplayName({
        loggedIn: true,
        cloudName: '13800138000',
        identities: ids,
        localName: '我写的昵称',
        fallback: '用户',
      }),
    ).toBe('我写的昵称')
  })

  it('does not echo phone when local is empty', () => {
    expect(
      resolveAccountDisplayName({
        loggedIn: true,
        cloudName: '13800138000',
        identities: ids,
        localName: '',
        fallback: '用户',
      }),
    ).toBe('用户')
  })

  it('uses local when logged out', () => {
    expect(
      resolveAccountDisplayName({
        loggedIn: false,
        cloudName: '云端',
        localName: '本地',
        fallback: '用户',
      }),
    ).toBe('本地')
  })
})
