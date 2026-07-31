import {
  type KeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useState,
} from 'react'
import {
  listWorkspaceWorkflows,
  type WorkspaceWorkflow,
} from '../services/api'
import {
  filterWorkflowOptions,
  formatWorkflowCommand,
  getWorkflowCommandQuery,
  moveWorkflowSelection,
  type WorkflowLoadStatus,
} from '../services/workflowCommands'

export type WorkflowCommandController = {
  activeDescendant?: string
  activeIndex: number
  dismiss: () => void
  handleKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => boolean
  loadError: string
  menuId: string
  menuOpen: boolean
  options: WorkspaceWorkflow[]
  reopen: () => void
  select: (workflow: WorkspaceWorkflow) => void
  setActiveIndex: (index: number) => void
  status: WorkflowLoadStatus
  workflows: WorkspaceWorkflow[]
}

export function useWorkflowCommandMenu({
  value,
  workspace,
  onChange,
}: {
  value: string
  workspace: string
  onChange: (value: string) => void
}): WorkflowCommandController {
  const reactId = useId()
  const menuId = `workflow-command-menu-${reactId}`
  const query = getWorkflowCommandQuery(value)
  const commandActive = query !== null
  const [workflows, setWorkflows] = useState<WorkspaceWorkflow[]>([])
  const [status, setStatus] = useState<WorkflowLoadStatus>('idle')
  const [loadError, setLoadError] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [dismissedValue, setDismissedValue] = useState<string | null>(null)
  const options = useMemo(
    () => filterWorkflowOptions(workflows, query ?? ''),
    [query, workflows],
  )
  const menuOpen = commandActive && dismissedValue !== value
  const activeDescendant = menuOpen && options.length
    ? `${menuId}-option-${activeIndex}`
    : undefined

  useEffect(() => {
    if (!commandActive || !workspace.trim()) {
      setStatus('idle')
      setLoadError('')
      setWorkflows([])
      return
    }

    let cancelled = false
    setStatus('loading')
    setLoadError('')
    setWorkflows([])
    void listWorkspaceWorkflows(workspace)
      .then((result) => {
        if (cancelled) return
        setWorkflows(result)
        setStatus('ready')
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setLoadError(error instanceof Error ? error.message : '工作流加载失败')
        setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [commandActive, workspace])

  useEffect(() => {
    setActiveIndex(options.length ? 0 : -1)
  }, [options])

  const select = useCallback((workflow: WorkspaceWorkflow) => {
    const command = formatWorkflowCommand(workflow)
    onChange(command)
    setDismissedValue(command)
    setActiveIndex(0)
  }, [onChange])

  const dismiss = useCallback(() => setDismissedValue(value), [value])
  const reopen = useCallback(() => setDismissedValue(null), [])

  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || !menuOpen) {
      return false
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (options.length) {
        setActiveIndex((current) => moveWorkflowSelection(
          current,
          options.length,
          event.key === 'ArrowDown' ? 1 : -1,
        ))
      }
      return true
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      dismiss()
      return true
    }
    if (
      event.key === 'Enter'
      && !event.shiftKey
      && !event.altKey
      && !event.ctrlKey
      && !event.metaKey
    ) {
      if (!options.length) return false
      event.preventDefault()
      select(options[activeIndex] ?? options[0])
      return true
    }
    return false
  }, [activeIndex, dismiss, menuOpen, options, select])

  return {
    activeDescendant,
    activeIndex,
    dismiss,
    handleKeyDown,
    loadError,
    menuId,
    menuOpen,
    options,
    reopen,
    select,
    setActiveIndex,
    status,
    workflows,
  }
}

export function WorkflowCommandMenu({
  controller,
}: {
  controller: WorkflowCommandController
}) {
  if (!controller.menuOpen) return null

  return (
    <div
      id={controller.menuId}
      className="workflow-command-menu"
      role="listbox"
      aria-label="可复用工作流"
    >
      {controller.status === 'loading' && (
        <div className="workflow-command-menu-state">正在加载工作流…</div>
      )}
      {controller.status === 'error' && (
        <div className="workflow-command-menu-state error">
          {controller.loadError || '工作流加载失败'}
        </div>
      )}
      {controller.status === 'ready' && controller.options.length === 0 && (
        <div className="workflow-command-menu-state">没有匹配的工作流</div>
      )}
      {controller.status === 'ready' && controller.options.map((workflow, index) => (
        <button
          id={`${controller.menuId}-option-${index}`}
          key={workflow.name}
          type="button"
          className={`workflow-command-option ${index === controller.activeIndex ? 'active' : ''}`}
          role="option"
          aria-selected={index === controller.activeIndex}
          onMouseEnter={() => controller.setActiveIndex(index)}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => controller.select(workflow)}
        >
          <span>/workflow:{workflow.name}</span>
        </button>
      ))}
    </div>
  )
}
