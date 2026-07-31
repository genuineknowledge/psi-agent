export const WORKFLOW_COMMAND_PREFIX = '/workflow:'

const WORKFLOW_NAME_RE = /^[a-z][a-z0-9-]{0,63}$/
const WINDOWS_RESERVED_WORKFLOW_NAMES = new Set([
  'con',
  'prn',
  'aux',
  'nul',
  ...Array.from({ length: 9 }, (_, index) => `com${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `lpt${index + 1}`),
])

function isValidWorkflowName(name) {
  return typeof name === 'string'
    && WORKFLOW_NAME_RE.test(name)
    && !WINDOWS_RESERVED_WORKFLOW_NAMES.has(name)
}

function invalid(error) {
  return { kind: 'invalid', error }
}

export function parseWorkflowCommand(value) {
  const text = String(value ?? '').trim()
  if (!text.startsWith(WORKFLOW_COMMAND_PREFIX)) return { kind: 'text' }

  const name = text.slice(WORKFLOW_COMMAND_PREFIX.length)
  if (!name) return invalid('请输入要运行的工作流名称，例如 /workflow:daily-brief')
  if (/\s/.test(name)) {
    return invalid('工作流指令只支持 /workflow:<名称>，不能附加参数')
  }
  if (!isValidWorkflowName(name)) {
    return invalid('工作流名称必须以小写字母开头，只能包含小写字母、数字和连字符，且不能使用系统保留名')
  }
  return { kind: 'workflow', name }
}

export function getWorkflowCommandQuery(value) {
  const text = String(value ?? '').trimStart()
  if (!text || /\s/.test(text)) return null

  if (WORKFLOW_COMMAND_PREFIX.startsWith(text)) return ''
  if (!text.startsWith(WORKFLOW_COMMAND_PREFIX)) return null
  return text.slice(WORKFLOW_COMMAND_PREFIX.length)
}

export function filterWorkflowOptions(workflows, query = '') {
  if (!Array.isArray(workflows)) return []
  const needle = String(query).toLowerCase()

  return workflows
    .filter(workflow => workflow
      && isValidWorkflowName(workflow.name))
    .map((workflow, index) => {
      const name = workflow.name.toLowerCase()
      let rank = 3
      if (!needle) rank = 0
      else if (name === needle) rank = 0
      else if (name.startsWith(needle)) rank = 1
      else if (name.includes(needle)) rank = 2
      return { workflow, index, rank }
    })
    .filter(item => item.rank < 3)
    .sort((a, b) => a.rank - b.rank
      || a.workflow.name.localeCompare(b.workflow.name)
      || a.index - b.index)
    .map(item => item.workflow)
}

export function formatWorkflowCommand(workflow) {
  const name = workflow?.name
  if (!isValidWorkflowName(name)) {
    throw new Error('Invalid workflow name')
  }
  return `${WORKFLOW_COMMAND_PREFIX}${name}`
}

export function moveWorkflowSelection(current, count, delta) {
  if (!Number.isInteger(count) || count <= 0) return -1
  if (!Number.isInteger(current) || current < 0 || current >= count) {
    return delta < 0 ? count - 1 : 0
  }
  return (current + delta + count) % count
}
