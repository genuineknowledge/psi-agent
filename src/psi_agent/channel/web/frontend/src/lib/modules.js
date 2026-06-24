// Framework capability switches.
// `key` is the field sent to the backend in the `modules` object.
// Only `flow` and `security` actually route to a backend agent (see the web
// channel's AgentRoutes); the rest are display-only toggles.
// `drawer: true` marks a card that opens a side panel instead of toggling.
export const MODULES = [
  { key: 'memory',     icon: '◉', name: '本体记忆模块',     desc: '本体知识与跨会话长期记忆' },
  { key: 'flow',       icon: '◇', name: '符号执行模块',     desc: '符号化多步任务执行' },
  { key: 'planning',   icon: '⬡', name: '逻辑规划模块',     desc: '逻辑推理与任务规划' },
  { key: 'security',   icon: '▣', name: '访问权限控制模块', desc: '访问权限与风险拦截' },
  { key: 'scheduling', icon: '◎', name: '可靠调度模块',     desc: '可靠任务调度与执行' },
  { key: 'evolution',  icon: '⟳', name: 'OAG 自进化模块',   desc: '技能自我反思与能力进化', drawer: true },
]

// Default on/off state, keyed by module key.
export const DEFAULT_STATE = {
  flow: true, security: true, memory: true, evolution: true,
  planning: true, scheduling: true,
}
