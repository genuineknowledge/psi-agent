export function validateRouterForm(form, ais) {
  const ids = new Set(ais.map(item => item.id))
  if (!form.name.trim()) return '请输入路由服务名称'
  if (!ids.has(form.router_ai_id)) return '请选择已连接的路由判断模型'
  if (!form.upstreams.length) return '请至少添加一个候选模型'
  const candidateIds = form.upstreams.map(item => item.ai_id)
  if (candidateIds.some(id => !ids.has(id))) return '候选模型不存在'
  if (form.upstreams.some(item => !item.description.trim())) return '请填写每个候选模型擅长的任务'
  if (new Set(candidateIds).size !== candidateIds.length) return '候选模型不能重复'
  if (!candidateIds.includes(form.default_ai_id)) return '默认模型必须是候选模型之一'
  const timeout = form.router_timeout
  if (timeout !== '' && timeout != null && (!(Number(timeout) > 0) || !Number.isFinite(Number(timeout)))) {
    return '路由超时必须是正数'
  }
  if (!Number.isInteger(Number(form.router_context_chars)) || Number(form.router_context_chars) <= 0) {
    return '上下文字符数必须是正整数'
  }
  return null
}

export function buildRouterPayload(form) {
  return {
    name: form.name.trim(),
    router_ai_id: form.router_ai_id,
    upstreams: form.upstreams.map(item => ({
      ai_id: item.ai_id,
      description: item.description.trim(),
    })),
    default_ai_id: form.default_ai_id,
    router_timeout: form.router_timeout === '' || form.router_timeout == null ? null : Number(form.router_timeout),
    router_context_chars: Number(form.router_context_chars),
  }
}
