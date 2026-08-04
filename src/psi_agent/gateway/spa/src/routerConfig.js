export function routerAiRole(mode) {
  return mode === 'aggregation' ? 'Aggregator' : 'Selector'
}

function nullablePositiveNumber(value) {
  return value === '' || value == null ? null : Number(value)
}

export function validateRouterForm(form, ais) {
  const ids = new Set(ais.map(item => item.id))
  if (!['routing', 'aggregation'].includes(form.mode)) return '请选择路由模式'
  if (!form.name.trim()) return '请输入路由服务名称'
  if (!ids.has(form.router_ai_id)) return `请选择已连接的 ${routerAiRole(form.mode)} 模型`
  if (!form.upstreams.length) return '请至少添加一个候选模型'
  const candidateIds = form.upstreams.map(item => item.ai_id)
  if (candidateIds.some(id => !ids.has(id))) return '候选模型不存在'
  if (form.upstreams.some(item => !item.description.trim())) return '请填写每个候选模型擅长的任务'
  if (new Set(candidateIds).size !== candidateIds.length) return '候选模型不能重复'
  if (form.mode === 'aggregation' && candidateIds.includes(form.router_ai_id)) {
    return '聚合模式下 Aggregator 不能同时作为候选模型'
  }
  for (const [field, label] of [
    ['router_timeout', 'Router 超时'],
    ['target_timeout', '候选模型超时'],
  ]) {
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
    router_ai_id: form.router_ai_id,
    upstreams: form.upstreams.map(item => ({
      ai_id: item.ai_id,
      description: item.description.trim(),
    })),
    router_timeout: nullablePositiveNumber(form.router_timeout),
    target_timeout: nullablePositiveNumber(form.target_timeout),
    max_context_chars: Number(form.max_context_chars),
  }
}
