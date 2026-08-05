import { backendExists, getBackendLabel } from './backendOptions.js'

export function routerAiRole(mode) {
  if (mode === 'aggregation') return 'Aggregator'
  if (mode === 'routing') return 'Selector'
  return ''
}

export function routerSummary(item, ais, routers) {
  const modeLabel = {
    routing: 'Routing',
    aggregation: 'Aggregation',
    fallback: 'Fallback',
  }[item.mode] || item.mode
  const upstreams = Array.isArray(item.upstreams)
    ? item.upstreams.map(upstream => {
      const typeLabel = upstream.backend_type === 'router' ? 'Router' : 'AI'
      return `${typeLabel} ${getBackendLabel(upstream.backend_type, upstream.backend_id, ais, routers)}`
    }).join(' → ')
    : ''
  const targetTimeout = item.target_timeout == null ? '候选不限时' : `候选 ${item.target_timeout}s`
  if (item.mode === 'fallback') return `${modeLabel} · ${upstreams} · ${targetTimeout}`

  const role = routerAiRole(item.mode)
  const controller = getBackendLabel('ai', item.router_ai_id, ais, routers)
  const controllerTimeout = item.router_timeout == null ? `${role} 不限时` : `${role} ${item.router_timeout}s`
  return `${modeLabel} · ${role} ${controller} · ${upstreams} · ${controllerTimeout} · ${targetTimeout}`
}

function nullablePositiveNumber(value) {
  return value === '' || value == null ? null : Number(value)
}

export function validateRouterForm(form, ais, routers = []) {
  const aiIds = new Set(ais.map(item => item.id))
  if (!['routing', 'aggregation', 'fallback'].includes(form.mode)) return '请选择路由模式'
  if (!form.name.trim()) return '请输入路由服务名称'
  if (form.mode === 'fallback') {
    if (form.router_ai_id) return 'Fallback 模式不能配置 Selector 或 Aggregator'
  } else if (!aiIds.has(form.router_ai_id)) {
    return `请选择已连接的 ${routerAiRole(form.mode)} 模型`
  }
  if (!form.upstreams.length) return '请至少添加一个候选服务'
  if (form.upstreams.some(item => !['ai', 'router'].includes(item.backend_type))) {
    return '候选服务类型无效'
  }
  if (form.upstreams.some(item => !backendExists(item.backend_type, item.backend_id, ais, routers))) {
    return '候选服务不存在'
  }
  if (form.upstreams.some(item => !item.description.trim())) return '请填写每个候选服务擅长的任务'
  const candidateKeys = form.upstreams.map(item => `${item.backend_type}:${item.backend_id}`)
  if (new Set(candidateKeys).size !== candidateKeys.length) return '候选服务不能重复'
  if (
    form.mode === 'aggregation'
    && form.upstreams.some(item => item.backend_type === 'ai' && item.backend_id === form.router_ai_id)
  ) {
    return '聚合模式下 Aggregator 不能同时作为候选模型'
  }
  const timeoutFields = [['target_timeout', '候选服务超时']]
  if (form.mode !== 'fallback') timeoutFields.unshift(['router_timeout', 'Router 超时'])
  for (const [field, label] of timeoutFields) {
    const value = form[field]
    if (value !== '' && value != null && (!(Number(value) > 0) || !Number.isFinite(Number(value)))) {
      return `${label}必须是正数`
    }
  }
  if (!Number.isInteger(Number(form.max_context_chars)) || Number(form.max_context_chars) <= 0) {
    return '最大上下文字符数必须是正整数'
  }
  return null
}

export function buildRouterPayload(form) {
  return {
    name: form.name.trim(),
    mode: form.mode,
    router_ai_id: form.mode === 'fallback' ? null : form.router_ai_id,
    upstreams: form.upstreams.map(item => ({
      backend_type: item.backend_type,
      backend_id: item.backend_id,
      description: item.description.trim(),
    })),
    router_timeout: form.mode === 'fallback' ? null : nullablePositiveNumber(form.router_timeout),
    target_timeout: nullablePositiveNumber(form.target_timeout),
    max_context_chars: Number(form.max_context_chars),
  }
}
