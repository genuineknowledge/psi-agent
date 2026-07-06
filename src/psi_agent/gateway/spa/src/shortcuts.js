// 侧栏全局快捷键判定：keydown 事件 → 动作字符串（无匹配返回 null）。
// 用 event.code 而非 key，规避 Shift 组合下的大小写/输入法差异；同时兼容 Cmd。
export function matchSidebarShortcut(e) {
  const mod = e.ctrlKey || e.metaKey
  if (!mod || !e.shiftKey) return null
  if (e.code === 'KeyO') return 'new-session'
  if (e.code === 'KeyK') return 'focus-search'
  return null
}
