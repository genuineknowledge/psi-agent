import { describe, expect, it } from 'vitest'

import { describeRouterStatus, normalizeRouterStatusEvent } from './routerStatus.js'

const TRACE_ID = '0198f47e-7345-7c2d-b5bb-8b64d0f9b235'

function event(overrides = {}) {
  return {
    type: 'router_status',
    version: 1,
    trace_id: TRACE_ID,
    mode: 'routing',
    phase: 'selecting',
    depth: 0,
    ...overrides,
  }
}

describe('normalizeRouterStatusEvent', () => {
  it('normalizes each Router mode and its mode-specific progress fields', () => {
    expect(normalizeRouterStatusEvent(event())).toEqual(event())

    expect(
      normalizeRouterStatusEvent(
        event({
          mode: 'aggregation',
          phase: 'synthesizing',
          depth: 1,
          completed: 2,
          total: 3,
          degraded: true,
        }),
      ),
    ).toEqual({
      type: 'router_status',
      version: 1,
      trace_id: TRACE_ID,
      mode: 'aggregation',
      phase: 'synthesizing',
      depth: 1,
      completed: 2,
      total: 3,
      degraded: true,
    })

    expect(
      normalizeRouterStatusEvent(
        event({
          mode: 'fallback',
          phase: 'attempting',
          attempt: 2,
          total: 3,
        }),
      ),
    ).toEqual({
      type: 'router_status',
      version: 1,
      trace_id: TRACE_ID,
      mode: 'fallback',
      phase: 'attempting',
      depth: 0,
      attempt: 2,
      total: 3,
    })
  })

  it('canonicalizes the UUID and strips unknown or sensitive fields', () => {
    const normalized = normalizeRouterStatusEvent(
      event({
        trace_id: `  ${TRACE_ID.toUpperCase()}  `,
        candidate: 'private-candidate',
        model: 'private-model',
        socket: '/private/router.sock',
        error: 'upstream secret',
        future_additive_field: { nested: true },
      }),
    )

    expect(normalized).toEqual(event())
    expect(Object.keys(normalized)).toEqual([
      'type',
      'version',
      'trace_id',
      'mode',
      'phase',
      'depth',
    ])
  })

  it.each([
    null,
    [],
    {},
    event({ type: 'text' }),
    event({ version: 2 }),
    event({ version: true }),
    event({ trace_id: 'not-a-uuid' }),
    event({ mode: 'unknown' }),
    event({ mode: 'toString' }),
    event({ mode: 'routing', phase: 'attempting' }),
    event({ mode: 'aggregation', phase: 'selecting', completed: 0, total: 2 }),
    event({ mode: 'fallback', phase: 'collecting', attempt: 1, total: 2 }),
    event({ depth: -1 }),
    event({ depth: 0.5 }),
  ])('rejects an invalid base event: %j', value => {
    expect(normalizeRouterStatusEvent(value)).toBeNull()
  })

  it.each([
    event({ mode: 'aggregation', phase: 'collecting', total: 2 }),
    event({ mode: 'aggregation', phase: 'collecting', completed: 0 }),
    event({ mode: 'aggregation', phase: 'collecting', completed: -1, total: 2 }),
    event({ mode: 'aggregation', phase: 'collecting', completed: 3, total: 2 }),
    event({ mode: 'aggregation', phase: 'collecting', completed: 0, total: 0 }),
    event({ mode: 'aggregation', phase: 'collecting', completed: 0.5, total: 2 }),
    event({
      mode: 'aggregation',
      phase: 'collecting',
      completed: 0,
      total: 2,
      degraded: 'yes',
    }),
    event({
      mode: 'aggregation',
      phase: 'collecting',
      completed: 0,
      total: 2,
      attempt: 1,
    }),
  ])('rejects contradictory aggregation progress: %j', value => {
    expect(normalizeRouterStatusEvent(value)).toBeNull()
  })

  it.each([
    event({ mode: 'fallback', phase: 'attempting', total: 2 }),
    event({ mode: 'fallback', phase: 'attempting', attempt: 1 }),
    event({ mode: 'fallback', phase: 'attempting', attempt: 0, total: 2 }),
    event({ mode: 'fallback', phase: 'attempting', attempt: 3, total: 2 }),
    event({ mode: 'fallback', phase: 'attempting', attempt: 1.5, total: 2 }),
    event({ mode: 'fallback', phase: 'attempting', attempt: 1, total: 0 }),
    event({
      mode: 'fallback',
      phase: 'attempting',
      attempt: 1,
      total: 2,
      completed: 1,
    }),
    event({
      mode: 'fallback',
      phase: 'attempting',
      attempt: 1,
      total: 2,
      degraded: false,
    }),
    event({ total: 2 }),
  ])('rejects contradictory fallback or routing progress: %j', value => {
    expect(normalizeRouterStatusEvent(value)).toBeNull()
  })
})

describe('describeRouterStatus', () => {
  it.each([
    [
      event({ phase: 'selecting' }),
      {
        label: '智能路由',
        message: '正在选择最合适的模型',
        icon: 'alt_route',
        badge: '',
        nested: false,
      },
    ],
    [
      event({ phase: 'generating', depth: 2 }),
      {
        label: '智能路由',
        message: '已选定模型，正在生成回复',
        icon: 'alt_route',
        badge: '',
        nested: true,
      },
    ],
    [
      event({ mode: 'aggregation', phase: 'collecting', completed: 0, total: 3 }),
      {
        label: '多模型汇总',
        message: '正在并行获取 3 个模型的回答',
        icon: 'hub',
        badge: '3 路并行',
        nested: false,
      },
    ],
    [
      event({ mode: 'aggregation', phase: 'synthesizing', completed: 3, total: 3 }),
      {
        label: '多模型汇总',
        message: '正在综合多个模型的回答',
        icon: 'hub',
        badge: '3 路并行',
        nested: false,
      },
    ],
    [
      event({
        mode: 'aggregation',
        phase: 'synthesizing',
        completed: 3,
        total: 3,
        degraded: true,
      }),
      {
        label: '多模型汇总',
        message: '部分模型未完成，正在综合可用回答',
        icon: 'hub',
        badge: '降级汇总',
        nested: false,
      },
    ],
    [
      event({ mode: 'fallback', phase: 'attempting', attempt: 2, total: 3 }),
      {
        label: '自动切换',
        message: '正在尝试第 2 个模型',
        icon: 'swap_horiz',
        badge: '2/3',
        nested: false,
      },
    ],
    [
      event({ mode: 'fallback', phase: 'switching', attempt: 2, total: 3 }),
      {
        label: '自动切换',
        message: '当前模型未完成，正在切换',
        icon: 'swap_horiz',
        badge: '2/3',
        nested: false,
      },
    ],
    [
      event({ mode: 'fallback', phase: 'replaying', attempt: 2, total: 3, depth: 1 }),
      {
        label: '自动切换',
        message: '已找到可用模型，正在返回结果',
        icon: 'swap_horiz',
        badge: '2/3',
        nested: true,
      },
    ],
  ])('describes every user-visible phase', (status, expected) => {
    expect(describeRouterStatus(status)).toEqual(expected)
  })
})
