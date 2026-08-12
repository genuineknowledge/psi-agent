import { FolderOpen, Loader2, Package } from 'lucide-react'
import { FormEvent, useEffect, useState } from 'react'
import { BrandLogo } from '../haitun-agent/primitives'
import { fetchCwd, fetchDefaults } from '../services/api'
import PathPickerDialog from './PathPickerDialog'

export type PathPickKind = 'workspace' | 'agent'

type Props = {
  /** Prefill when switching from an existing path. */
  initialPath?: string
  /** workspace = user open folder; agent = capability pack (tools/schedules/systems). */
  kind?: PathPickKind
  onReady: (path: string) => void
  /** Return without changing (settings → 切换). */
  onCancel?: () => void
}

/** Pick / confirm a directory (workspace gate or agent-package gate). */
export default function WorkspaceGate({
  initialPath = '',
  kind = 'workspace',
  onReady,
  onCancel,
}: Props) {
  const isAgent = kind === 'agent'
  const [path, setPath] = useState(initialPath)
  const [loading, setLoading] = useState(!initialPath)
  const [error, setError] = useState<string | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)

  useEffect(() => {
    if (initialPath.trim()) {
      setPath(initialPath.trim())
      setLoading(false)
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const d = await fetchDefaults().catch(() => null)
        if (isAgent) {
          if (!cancelled && d?.agent) setPath(d.agent)
          else if (!cancelled) setPath('')
        } else if (!cancelled && d?.workspace) {
          setPath(d.workspace)
        } else {
          const cwd = await fetchCwd()
          if (!cancelled) setPath(cwd?.cwd || '')
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [initialPath, isAgent])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const clean = path.trim()
    if (!clean) {
      setError(isAgent ? '请选择或输入 Agent 包路径' : '请选择或输入海豚工作室路径')
      return
    }
    onReady(clean)
  }

  const ConfirmIcon = isAgent ? Package : FolderOpen

  return (
    <div className="workspace-gate">
      <div className="workspace-gate-card">
        <BrandLogo size="hero" />
        <span className="eyebrow">HaiTun Agent</span>
        <h1>{isAgent ? '选择 Agent 包' : '打开海豚工作室'}</h1>
        <p>
          {isAgent ? (
            <>
              Agent 包是能力根目录（<code>tools/</code>、<code>schedules/</code>、<code>systems/</code>）。
              可与用户海豚工作室不同；切换后<strong>新建任务/聊天</strong>会挂上此包，已有任务仍用创建时绑定的包。
            </>
          ) : (
            <>
              先选一个本地文件夹作为海豚工作室吧，之后的任务都会围绕这个文件夹进行。
            </>
          )}
        </p>
        {loading ? (
          <div className="workspace-gate-loading"><Loader2 className="spin" size={22} /> 正在连接 Gateway…</div>
        ) : (
          <form onSubmit={submit}>
            <label>
              <span>{isAgent ? 'Agent 包路径' : '海豚工作室路径'}</span>
              <div className="workspace-gate-path-row">
                <button
                  type="button"
                  className="workspace-gate-browse"
                  onClick={() => setPickerOpen(true)}
                  aria-label="浏览文件夹"
                  title="浏览文件夹"
                >
                  <FolderOpen size={18} />
                </button>
                <input
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder={
                    isAgent
                      ? '例如 D:\\Haitun\\examples\\haitun-workspace'
                      : '例如 D:\\Projects\\my-folder'
                  }
                  autoFocus
                />
              </div>
            </label>
            {error && <div className="workspace-gate-error" role="alert">{error}</div>}
            <div className="workspace-gate-actions">
              {onCancel && (
                <button type="button" className="secondary-button" onClick={onCancel}>
                  取消
                </button>
              )}
              <button type="submit" className="primary-button" disabled={!path.trim()}>
                <ConfirmIcon size={16} /> {isAgent ? '使用此 Agent 包' : '进入任务工作台'}
              </button>
            </div>
          </form>
        )}
      </div>

      <PathPickerDialog
        open={pickerOpen}
        initialPath={path}
        title={isAgent ? '选择 Agent 包' : '打开海豚工作室'}
        confirmLabel={isAgent ? '选择' : '打开'}
        hint={
          isAgent
            ? '选择含 tools / schedules / systems 的 Agent 能力包目录。'
            : '选择本地文件夹作为用户海豚工作室。'
        }
        onCancel={() => setPickerOpen(false)}
        onConfirm={(picked) => {
          setPath(picked)
          setPickerOpen(false)
          setError(null)
        }}
      />
    </div>
  )
}
