import { describe, expect, it } from 'vitest'

import {
  ROUTER_MODE_OPTIONS,
  routerModeHint,
  routerModePresentation,
} from './routerMode.js'

describe('Router mode presentation', () => {
  it('uses one canonical label and icon set across configuration and runtime UI', () => {
    expect(ROUTER_MODE_OPTIONS).toEqual([
      { value: 'routing', label: '智能分流' },
      { value: 'aggregation', label: '并行聚合' },
      { value: 'fallback', label: '顺序回退' },
    ])
    expect(routerModePresentation('routing')).toEqual({
      label: '智能分流',
      icon: 'alt_route',
    })
    expect(routerModePresentation('aggregation')).toEqual({
      label: '并行聚合',
      icon: 'hub',
    })
    expect(routerModePresentation('fallback')).toEqual({
      label: '顺序回退',
      icon: 'swap_horiz',
    })
  })

  it('explains the actual execution topology of every mode', () => {
    expect(routerModeHint('routing')).toContain('选择 1 个')
    expect(routerModeHint('aggregation')).toContain('并行调用全部')
    expect(routerModeHint('fallback')).toContain('按配置顺序')
  })

  it('fails closed for unknown and prototype-property modes', () => {
    expect(routerModePresentation('future')).toEqual({
      label: '未知模式',
      icon: 'help',
    })
    expect(routerModePresentation('toString')).toEqual({
      label: '未知模式',
      icon: 'help',
    })
    expect(routerModeHint('future')).toBe('Router 配置不可用')
  })
})
