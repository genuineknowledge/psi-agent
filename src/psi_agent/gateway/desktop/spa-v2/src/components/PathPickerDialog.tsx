import { ArrowUp, Folder, HardDrive, Loader2, Search, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import {
  browseWorkspace,
  fetchWorkspacePlaces,
  type BrowseEntry,
  type WorkspaceDrive,
  type WorkspacePlace,
} from '../services/api'
import './path-picker.css'

function normalizePath(path: string): string {
  return path.replace(/\\/g, '/').replace(/\/+$/, '')
}

function filterEntries(entries: BrowseEntry[], query: string): BrowseEntry[] {
  const q = query.trim().toLowerCase()
  const list = [...entries].sort((a, b) => {
    const ak = a.kind === 'directory' ? 0 : 1
    const bk = b.kind === 'directory' ? 0 : 1
    if (ak !== bk) return ak - bk
    return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
  })
  if (!q) return list
  return list.filter((e) => e.name.toLowerCase().includes(q))
}

type Props = {
  open: boolean
  initialPath?: string
  title?: string
  confirmLabel?: string
  hint?: string
  onConfirm: (path: string) => void
  onCancel: () => void
}

/** Directory picker backed by Gateway `/workspace/places` + `/workspace/browse` (spa v1 parity). */
export default function PathPickerDialog({
  open,
  initialPath = '',
  title = '选择文件夹',
  confirmLabel = '选择文件夹',
  hint = '',
  onConfirm,
  onCancel,
}: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [places, setPlaces] = useState<WorkspacePlace[]>([])
  const [drives, setDrives] = useState<WorkspaceDrive[]>([])
  const [currentPath, setCurrentPath] = useState('')
  const [selectedPath, setSelectedPath] = useState('')
  const [parent, setParent] = useState('')
  const [segments, setSegments] = useState<{ name: string; path: string }[]>([])
  const [entries, setEntries] = useState<BrowseEntry[]>([])
  const [filterText, setFilterText] = useState('')
  const [address, setAddress] = useState('')

  const visible = useMemo(() => filterEntries(entries, filterText), [entries, filterText])
  const canGoUp = Boolean(parent && normalizePath(parent) !== normalizePath(currentPath))

  const loadBrowse = async (path: string) => {
    setLoading(true)
    setError('')
    try {
      const data = await browseWorkspace(path, { kind: 'directory' })
      const next = data.path || path
      setCurrentPath(next)
      setSelectedPath(next)
      setAddress(next)
      setParent(data.parent || '')
      setSegments(Array.isArray(data.segments) ? data.segments : [])
      setEntries(Array.isArray(data.entries) ? data.entries : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!open) return
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError('')
      setFilterText('')
      try {
        const placesData = await fetchWorkspacePlaces()
        if (cancelled) return
        setPlaces(Array.isArray(placesData.places) ? placesData.places : [])
        setDrives(Array.isArray(placesData.drives) ? placesData.drives : [])
        const data = await browseWorkspace(normalizePath(initialPath) || '', { kind: 'directory' })
        if (cancelled) return
        const next = data.path || normalizePath(initialPath)
        setCurrentPath(next)
        setSelectedPath(next)
        setAddress(next)
        setParent(data.parent || '')
        setSegments(Array.isArray(data.segments) ? data.segments : [])
        setEntries(Array.isArray(data.entries) ? data.entries : [])
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open, initialPath])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  if (!open) return null

  return (
    <div className="path-picker-overlay" role="presentation" onClick={onCancel}>
      <div
        className="path-picker-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="path-picker-header">
          <h3>{title}</h3>
          <button type="button" className="path-picker-icon-btn" aria-label="关闭" onClick={onCancel}>
            <X size={18} />
          </button>
        </header>
        {hint ? <p className="path-picker-hint">{hint}</p> : null}

        <div className="path-picker-body">
          <aside className="path-picker-nav">
            {drives.length > 0 && (
              <div className="path-picker-nav-section">
                <div className="path-picker-nav-label">此电脑</div>
                {drives.map((d) => (
                  <button key={d.path} type="button" className="path-picker-nav-item" onClick={() => void loadBrowse(d.path)}>
                    <HardDrive size={16} />
                    <span>{d.label}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="path-picker-nav-section">
              <div className="path-picker-nav-label">快捷位置</div>
              {places.map((p) => (
                <button key={p.id} type="button" className="path-picker-nav-item" onClick={() => void loadBrowse(p.path)}>
                  <Folder size={16} />
                  <span>{p.label}</span>
                </button>
              ))}
            </div>
          </aside>

          <section className="path-picker-main">
            <div className="path-picker-toolbar">
              <button
                type="button"
                className="path-picker-icon-btn"
                title="上级目录"
                disabled={!canGoUp || loading}
                onClick={() => void loadBrowse(parent)}
              >
                <ArrowUp size={16} />
              </button>
              <div className="path-picker-crumbs">
                {segments.map((seg, i) => (
                  <span key={seg.path} className="path-picker-crumb-wrap">
                    <button type="button" className="path-picker-crumb" onClick={() => void loadBrowse(seg.path)}>
                      {seg.name}
                    </button>
                    {i < segments.length - 1 ? <span className="path-picker-crumb-sep">›</span> : null}
                  </span>
                ))}
              </div>
            </div>

            <input
              className="path-picker-address"
              value={address}
              aria-label="路径"
              onChange={(e) => setAddress(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  void loadBrowse(address.trim())
                }
              }}
            />

            <div className="path-picker-filter">
              <Search size={15} />
              <input
                type="search"
                value={filterText}
                placeholder="筛选当前文件夹"
                aria-label="筛选"
                onChange={(e) => setFilterText(e.target.value)}
              />
            </div>

            {error ? <div className="path-picker-error">{error}</div> : null}
            {loading ? (
              <div className="path-picker-loading"><Loader2 className="spin" size={18} /> 加载中…</div>
            ) : (
              <div className="path-picker-listing" role="listbox">
                {visible.map((entry) => {
                  const disabled = entry.kind !== 'directory'
                  return (
                    <button
                      key={entry.path}
                      type="button"
                      role="option"
                      aria-selected={entry.path === selectedPath}
                      className={`path-picker-entry${entry.path === selectedPath ? ' selected' : ''}${disabled ? ' disabled' : ''}`}
                      disabled={disabled}
                      onClick={() => setSelectedPath(entry.path)}
                      onDoubleClick={() => {
                        if (entry.kind === 'directory') void loadBrowse(entry.path)
                      }}
                    >
                      <Folder size={16} />
                      <span>{entry.name}</span>
                    </button>
                  )
                })}
                {visible.length === 0 ? <div className="path-picker-empty">此文件夹为空</div> : null}
              </div>
            )}
          </section>
        </div>

        <footer className="path-picker-footer">
          <label>
            <span>文件夹</span>
            <input
              value={selectedPath}
              onChange={(e) => setSelectedPath(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && selectedPath.trim()) {
                  e.preventDefault()
                  onConfirm(normalizePath(selectedPath.trim()))
                }
              }}
            />
          </label>
          <div className="path-picker-footer-actions">
            <button type="button" className="path-picker-cancel" onClick={onCancel}>取消</button>
            <button
              type="button"
              className="path-picker-ok"
              disabled={!selectedPath.trim()}
              onClick={() => onConfirm(normalizePath(selectedPath.trim()))}
            >
              {confirmLabel}
            </button>
          </div>
        </footer>
      </div>
    </div>
  )
}
