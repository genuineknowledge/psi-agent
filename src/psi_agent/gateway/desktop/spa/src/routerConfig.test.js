import { describe, expect, it } from 'vitest'

import { buildRouterPayload, routerAiRole, routerSummary, validateRouterForm } from './routerConfig.js'

const ais = [{ id: 'route' }, { id: 'simple' }, { id: 'complex' }]
const routers = [{ id: 'nested', name: 'Nested Router' }, { id: 'route', name: 'Same-name Router' }]

function form() {
  return {
    name: ' Smart Router ',
    mode: 'aggregation',
    router_ai_id: 'route',
    upstreams: [
      { backend_type: 'ai', backend_id: 'simple', description: ' simple tasks ' },
      { backend_type: 'ai', backend_id: 'complex', description: 'complex tasks' },
    ],
    router_timeout: '30',
    target_timeout: '8',
    max_context_chars: '12000',
  }
}

describe('router configuration', () => {
  it('validates typed references and composite duplicate keys', () => {
    expect(validateRouterForm(form(), ais, routers)).toBeNull()

    const duplicate = form()
    duplicate.upstreams[1].backend_id = 'simple'
    expect(validateRouterForm(duplicate, ais, routers)).toContain('重复')

    const sameIdInDifferentNamespaces = form()
    sameIdInDifferentNamespaces.upstreams = [
      { backend_type: 'ai', backend_id: 'route', description: 'AI' },
      { backend_type: 'router', backend_id: 'route', description: 'Router' },
    ]
    sameIdInDifferentNamespaces.mode = 'routing'
    expect(validateRouterForm(sameIdInDifferentNamespaces, ais, routers)).toBeNull()
  })

  it('rejects unknown backend types and references', () => {
    const unknownType = form()
    unknownType.upstreams[0].backend_type = 'session'
    expect(validateRouterForm(unknownType, ais, routers)).toContain('类型无效')

    const missing = form()
    missing.upstreams[0].backend_id = 'missing'
    expect(validateRouterForm(missing, ais, routers)).toContain('不存在')
  })

  it('builds the canonical typed Gateway payload', () => {
    expect(buildRouterPayload(form())).toEqual({
      name: 'Smart Router',
      mode: 'aggregation',
      router_ai_id: 'route',
      upstreams: [
        { backend_type: 'ai', backend_id: 'simple', description: 'simple tasks' },
        { backend_type: 'ai', backend_id: 'complex', description: 'complex tasks' },
      ],
      router_timeout: 30,
      target_timeout: 8,
      max_context_chars: 12000,
    })
  })

  it('builds a mixed Fallback payload without a controller', () => {
    const fallback = form()
    fallback.mode = 'fallback'
    fallback.router_ai_id = ''
    fallback.router_timeout = '99'
    fallback.upstreams = [
      { backend_type: 'ai', backend_id: 'simple', description: 'primary' },
      { backend_type: 'router', backend_id: 'nested', description: 'secondary' },
    ]

    expect(validateRouterForm(fallback, ais, routers)).toBeNull()
    expect(buildRouterPayload(fallback)).toEqual({
      name: 'Smart Router',
      mode: 'fallback',
      router_ai_id: null,
      upstreams: [
        { backend_type: 'ai', backend_id: 'simple', description: 'primary' },
        { backend_type: 'router', backend_id: 'nested', description: 'secondary' },
      ],
      router_timeout: null,
      target_timeout: 8,
      max_context_chars: 12000,
    })
  })

  it('enforces mode-specific controller rules', () => {
    const fallback = form()
    fallback.mode = 'fallback'
    expect(validateRouterForm(fallback, ais, routers)).toContain('不能配置')
    fallback.router_ai_id = ''
    expect(validateRouterForm(fallback, ais, routers)).toBeNull()

    const routing = form()
    routing.mode = 'routing'
    routing.router_ai_id = ''
    expect(validateRouterForm(routing, ais, routers)).toContain('Selector')
  })

  it('rejects Aggregator reuse only in the AI namespace', () => {
    const aggregation = form()
    aggregation.upstreams[0].backend_id = aggregation.router_ai_id
    expect(validateRouterForm(aggregation, ais, routers)).toContain('Aggregator')

    aggregation.upstreams[0].backend_type = 'router'
    expect(validateRouterForm(aggregation, ais, routers)).toBeNull()
  })

  it.each(['router_timeout', 'target_timeout'])('validates %s independently', field => {
    const invalid = form()
    invalid[field] = 0
    expect(validateRouterForm(invalid, ais, routers)).toContain('正数')
    invalid[field] = ''
    expect(validateRouterForm(invalid, ais, routers)).toBeNull()
  })

  it('ignores router timeout for Fallback but validates target timeout', () => {
    const fallback = form()
    fallback.mode = 'fallback'
    fallback.router_ai_id = ''
    fallback.router_timeout = -1
    expect(validateRouterForm(fallback, ais, routers)).toBeNull()
    fallback.target_timeout = -1
    expect(validateRouterForm(fallback, ais, routers)).toContain('正数')
  })

  it('requires a positive integer context budget and an explicit mode', () => {
    const invalid = form()
    invalid.max_context_chars = 1.5
    expect(validateRouterForm(invalid, ais, routers)).toContain('正整数')
    invalid.max_context_chars = 12000
    invalid.mode = ''
    expect(validateRouterForm(invalid, ais, routers)).toContain('模式')
  })

  it('uses mode-specific Router AI labels', () => {
    expect(routerAiRole('routing')).toBe('Selector')
    expect(routerAiRole('aggregation')).toBe('Aggregator')
    expect(routerAiRole('fallback')).toBe('')
  })

  it('summarizes mode, typed upstreams, controller, and timeouts', () => {
    const aggregation = buildRouterPayload(form())
    expect(routerSummary(aggregation, ais, routers)).toBe(
      'Aggregation · Aggregator route · AI simple → AI complex · Aggregator 30s · 候选 8s',
    )

    const fallback = form()
    fallback.mode = 'fallback'
    fallback.router_ai_id = ''
    fallback.target_timeout = ''
    fallback.upstreams = [
      { backend_type: 'router', backend_id: 'nested', description: 'secondary' },
    ]
    expect(routerSummary(buildRouterPayload(fallback), ais, routers)).toBe(
      'Fallback · Router Nested Router · 候选不限时',
    )
  })
})
