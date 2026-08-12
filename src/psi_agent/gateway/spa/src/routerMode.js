const MODE_PRESENTATION = Object.freeze({
  routing: Object.freeze({ label: '智能分流', icon: 'alt_route' }),
  aggregation: Object.freeze({ label: '并行聚合', icon: 'hub' }),
  fallback: Object.freeze({ label: '顺序回退', icon: 'swap_horiz' }),
})

const MODE_HINTS = Object.freeze({
  routing: 'Selector 根据当前请求选择 1 个候选服务。',
  aggregation: '并行调用全部候选服务，再由 Aggregator 综合回答。',
  fallback: '按配置顺序逐个尝试候选服务，首个完整回答胜出。',
})

const UNKNOWN_PRESENTATION = Object.freeze({ label: '未知模式', icon: 'help' })
const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value, key)

export const ROUTER_MODE_OPTIONS = Object.freeze(
  ['routing', 'aggregation', 'fallback'].map(value => Object.freeze({
    value,
    label: MODE_PRESENTATION[value].label,
  })),
)

/** Return the canonical user-facing Router mode label and icon. */
export function routerModePresentation(mode) {
  return hasOwn(MODE_PRESENTATION, mode) ? MODE_PRESENTATION[mode] : UNKNOWN_PRESENTATION
}

/** Explain the execution topology represented by one Router mode. */
export function routerModeHint(mode) {
  return hasOwn(MODE_HINTS, mode) ? MODE_HINTS[mode] : 'Router 配置不可用'
}
