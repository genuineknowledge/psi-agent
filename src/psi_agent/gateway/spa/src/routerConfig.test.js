import { describe, expect, it } from 'vitest'

import { buildRouterPayload, routerAiRole, validateRouterForm } from './routerConfig.js'

const ais = [{ id: 'route' }, { id: 'simple' }, { id: 'complex' }]

function form() {
  return {
    name: ' Smart Router ',
    mode: 'aggregation',
    router_ai_id: 'route',
    upstreams: [
      { ai_id: 'simple', description: ' simple tasks ' },
      { ai_id: 'complex', description: 'complex tasks' },
    ],
    router_timeout: '30',
    target_timeout: '8',
    max_context_chars: '12000',
  }
}

describe('router configuration', () => {
  it('validates references and candidate descriptions', () => {
    expect(validateRouterForm(form(), ais)).toBeNull()
    const duplicate = form()
    duplicate.upstreams[1].ai_id = 'simple'
    expect(validateRouterForm(duplicate, ais)).toContain('重复')
  })

  it('builds the current gateway payload without legacy fields', () => {
    expect(buildRouterPayload(form())).toEqual({
      name: 'Smart Router',
      mode: 'aggregation',
      router_ai_id: 'route',
      upstreams: [
        { ai_id: 'simple', description: 'simple tasks' },
        { ai_id: 'complex', description: 'complex tasks' },
      ],
      router_timeout: 30,
      target_timeout: 8,
      max_context_chars: 12000,
    })
  })

  it('rejects aggregator reuse but permits selector reuse', () => {
    const aggregation = form()
    aggregation.upstreams[0].ai_id = aggregation.router_ai_id
    expect(validateRouterForm(aggregation, ais)).toContain('聚合')
    aggregation.mode = 'routing'
    expect(validateRouterForm(aggregation, ais)).toBeNull()
  })

  it.each(['router_timeout', 'target_timeout'])('validates %s independently', field => {
    const invalid = form()
    invalid[field] = 0
    expect(validateRouterForm(invalid, ais)).toContain('正数')
    invalid[field] = ''
    expect(validateRouterForm(invalid, ais)).toBeNull()
  })

  it('requires a positive integer context budget and an explicit mode', () => {
    const invalid = form()
    invalid.max_context_chars = 1.5
    expect(validateRouterForm(invalid, ais)).toContain('正整数')
    invalid.max_context_chars = 12000
    invalid.mode = ''
    expect(validateRouterForm(invalid, ais)).toContain('模式')
  })

  it('uses mode-specific Router AI labels', () => {
    expect(routerAiRole('routing')).toBe('Selector')
    expect(routerAiRole('aggregation')).toBe('Aggregator')
  })
})
