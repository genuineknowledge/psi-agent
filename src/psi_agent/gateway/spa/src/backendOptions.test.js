import { describe, expect, it } from 'vitest'

import { backendExists, backendValues, getBackendLabel } from './backendOptions.js'

const ais = [{ id: 'a1', model: 'Qwen' }]
const routers = [{ id: 'r1', name: '智能路由' }]

describe('backend options', () => {
  it('finds and labels AI and Router backends', () => {
    expect(backendExists('ai', 'a1', ais, routers)).toBe(true)
    expect(backendExists('router', 'r1', ais, routers)).toBe(true)
    expect(getBackendLabel('ai', 'a1', ais, routers)).toBe('Qwen')
    expect(getBackendLabel('router', 'r1', ais, routers)).toBe('智能路由')
  })

  it('keeps unknown backend types fail-closed', () => {
    expect(backendValues('ai', ais, routers)).toBe(ais)
    expect(backendValues('router', ais, routers)).toBe(routers)
    expect(backendValues('session', ais, routers)).toEqual([])
    expect(backendExists('session', 'a1', ais, routers)).toBe(false)
    expect(getBackendLabel('session', 'a1', ais, routers)).toBe('选择服务')
  })
})
