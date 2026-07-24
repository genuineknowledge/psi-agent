import { describe, expect, it } from 'vitest'

import { buildRouterPayload, validateRouterForm } from './routerConfig.js'

const ais = [{ id: 'route' }, { id: 'simple' }, { id: 'complex' }]

function form() {
  return {
    name: ' Smart Router ',
    router_ai_id: 'route',
    upstreams: [
      { ai_id: 'simple', description: ' simple tasks ' },
      { ai_id: 'complex', description: 'complex tasks' },
    ],
    default_ai_id: 'simple',
    router_timeout: '30',
    router_context_chars: '12000',
  }
}

describe('router configuration', () => {
  it('validates references and candidate descriptions', () => {
    expect(validateRouterForm(form(), ais)).toBeNull()
    const duplicate = form()
    duplicate.upstreams[1].ai_id = 'simple'
    expect(validateRouterForm(duplicate, ais)).toContain('重复')
  })

  it('builds the gateway payload without runtime fields', () => {
    expect(buildRouterPayload(form())).toEqual({
      name: 'Smart Router',
      router_ai_id: 'route',
      upstreams: [
        { ai_id: 'simple', description: 'simple tasks' },
        { ai_id: 'complex', description: 'complex tasks' },
      ],
      default_ai_id: 'simple',
      router_timeout: 30,
      router_context_chars: 12000,
    })
  })
})
