// Framework capability switches.
// `key` is the field sent to the backend in the `modules` object.
export const MODULES = [
  { key: 'flow',          icon: '◇', name: 'Flow 编排',  desc: '多步任务规划与执行' },
  { key: 'security',      icon: '▣', name: '安全护栏',    desc: '输入输出风险拦截' },
  { key: 'memory',        icon: '◉', name: '记忆系统',    desc: '跨会话长期记忆' },
  { key: 'tools',         icon: '⚙', name: '工具调用',    desc: '外部工具与函数' },
  { key: 'skills',        icon: '✦', name: '技能加载',    desc: '领域技能包注入' },
  { key: 'subagent',      icon: '⬡', name: '子智能体',    desc: '并行委派子任务' },
  { key: 'observability', icon: '◎', name: '可观测性',    desc: '执行轨迹与追踪' },
]

// Default on/off state, keyed by module key.
export const DEFAULT_STATE = {
  flow: true, security: true, memory: true, tools: true,
  skills: true, subagent: false, observability: true,
}
