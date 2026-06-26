// Available workspaces. Each entry maps to a backend agent service (one
// workspace == one session process == one socket). The selected workspace name
// is sent to the backend on session creation; the backend routes it to the
// matching socket (see the web channel's WorkspaceRoutes). This list is
// intentionally hardcoded and should stay in sync with DEFAULT_WORKSPACES on
// the backend.
export const WORKSPACES = [
  { name: 'fusion-flow', icon: '◆', desc: '符号执行 + 自进化技能工作区' },
  { name: 'hermes', icon: '◇', desc: 'Hermes 原生 agent 工作区' },
  { name: 'openclaw', icon: '▣', desc: '访问权限受控工作区' },
]

export const DEFAULT_WORKSPACE = WORKSPACES[0].name
