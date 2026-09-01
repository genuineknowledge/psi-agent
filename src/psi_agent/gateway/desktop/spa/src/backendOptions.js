export function backendValues(type, ais, routers) {
  if (type === 'ai') return ais
  if (type === 'router') return routers
  return []
}

export function backendExists(type, id, ais, routers) {
  return backendValues(type, ais, routers).some(item => item.id === id)
}

export function getBackendLabel(type, id, ais, routers) {
  const found = backendValues(type, ais, routers).find(item => item.id === id)
  if (!found) return '选择服务'
  return type === 'router' ? (found.name || found.id) : (found.model || found.id)
}
