export function backendExists(type, id, ais, routers) {
  const values = type === 'router' ? routers : ais
  return values.some(item => item.id === id)
}

export function getBackendLabel(type, id, ais, routers) {
  const values = type === 'router' ? routers : ais
  const found = values.find(item => item.id === id)
  if (!found) return '选择模型'
  return type === 'router' ? (found.name || found.id) : (found.model || found.id)
}
