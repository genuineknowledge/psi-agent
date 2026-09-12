import { type ChangeEvent, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Download, Eye, EyeOff, FileText, FolderOpen, Layers, Plus, Trash2, Upload } from 'lucide-react'
import type { SkillItem } from '../../services/api'
import { deleteSkill, disableSkill, enableSkill, exportSkill, importSkill, listSkills, readWorkspaceFile, revealWorkspacePath } from '../../services/api'
import { useI18n } from '../../i18n'
import HubDialog from './HubDialog'

type Props = {
  show: boolean
  onClose: () => void
  onToast?: (message: string, durationMs?: number) => void
  /** "+ new skill" jumps into the chat composer (callback chain wired in UserHub). */
  onNewSkill?: () => void
}

type Filter = 'all' | 'official' | 'global'

/** base64 (from /workspace/file) -> UTF-8 text, so CJK SKILL.md bodies render right. */
function decodeBase64Utf8(b64: string): string {
  const bin = atob(b64)
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0))
  return new TextDecoder('utf-8').decode(bytes)
}

/**
 * Content console (skills) -- read-only list of the two layers with source badges.
 * Mirrors HubModelsPanel: HubDialog shell + hub-section/hub-ai-list rows + useI18n.
 * New/edit is NOT here (design doc section 11: authoring goes through chat); the
 * "+ new" button just jumps into the composer via onNewSkill.
 */
export default function HubContentPanel({ show, onClose, onToast, onNewSkill }: Props) {
  const { t } = useI18n()
  const [skills, setSkills] = useState<SkillItem[]>([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  /** Path of the row expanded for read-only preview, plus its decoded body. */
  const [expanded, setExpanded] = useState<string | null>(null)
  const [preview, setPreview] = useState('')
  /** Hidden file input behind the toolbar "import" button (zip upload). */
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!show) return
    setFilter('all')
    setQuery('')
    setExpanded(null)
    setPreview('')
    setLoading(true)
    void listSkills()
      .then(setSkills)
      .catch((e) => onToast?.(e instanceof Error ? e.message : t('content.loadFailed')))
      .finally(() => setLoading(false))
  }, [show, onToast, t])

  const counts = useMemo(
    () => ({
      all: skills.length,
      official: skills.filter((s) => s.source === 'official').length,
      global: skills.filter((s) => s.source === 'global').length,
    }),
    [skills],
  )

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return skills.filter((s) => {
      if (filter !== 'all' && s.source !== filter) return false
      if (q && !`${s.name} ${s.description}`.toLowerCase().includes(q)) return false
      return true
    })
  }, [skills, filter, query])

  const toggleView = async (s: SkillItem) => {
    if (expanded === s.path) {
      setExpanded(null)
      setPreview('')
      return
    }
    setExpanded(s.path)
    setPreview('')
    try {
      // No root -> reads the winning layer's SKILL.md even outside the workspace.
      const f = await readWorkspaceFile(s.path)
      setPreview(decodeBase64Utf8(f.data))
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : t('content.viewFailed'))
    }
  }

  const reveal = async (s: SkillItem) => {
    try {
      await revealWorkspacePath(s.path)
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : t('content.revealFailed'))
    }
  }

  const handleDelete = async (s: SkillItem) => {
    // Personal skills only (the button renders only for source==='global');
    // the backend also refuses anything outside ~/.agent/skills.
    if (!window.confirm(t('content.confirmDelete', { name: s.name }))) return
    try {
      await deleteSkill(s.name)
      setSkills((cur) => cur.filter((x) => x.path !== s.path))
      if (expanded === s.path) {
        setExpanded(null)
        setPreview('')
      }
      onToast?.(t('content.deleted', { name: s.name }))
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : t('content.deleteFailed'))
    }
  }

  const handleDisable = async (s: SkillItem) => {
    // Official skills only (the button renders only for source==='official' &&
    // !tombstoned). The tombstone hides it from the agent index; the read-only
    // package file is untouched (an upgrade would restore a deletion anyway).
    if (!window.confirm(t('content.confirmDisable', { name: s.name }))) return
    try {
      await disableSkill(s.name)
      setSkills((cur) => cur.map((x) => (x.path === s.path ? { ...x, tombstoned: true } : x)))
      onToast?.(t('content.disabled', { name: s.name }))
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : t('content.disableFailed'))
    }
  }

  const handleEnable = async (s: SkillItem) => {
    // Re-enable a tombstoned official skill. No confirm -- restoring is safe and
    // reversible, unlike disable which hides a skill from the model.
    try {
      await enableSkill(s.name)
      setSkills((cur) => cur.map((x) => (x.path === s.path ? { ...x, tombstoned: false } : x)))
      onToast?.(t('content.enabled', { name: s.name }))
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : t('content.enableFailed'))
    }
  }

  const handleExport = async (s: SkillItem) => {
    // Any layer can be exported (decision 2): exportSkill zips the winning dir
    // and triggers the browser download itself.
    try {
      await exportSkill(s.name)
      onToast?.(t('content.exported', { name: s.name }))
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : t('content.exportFailed'))
    }
  }

  const handleImport = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    try {
      if (!file) return
      const result = await importSkill(file)
      onToast?.(t('content.imported', { name: result.name }))
      setSkills(await listSkills())  // refresh so the imported skill shows up
    } catch (err) {
      onToast?.(err instanceof Error ? err.message : t('content.importFailed'))
    } finally {
      e.target.value = ''  // reset so re-picking the same file re-fires onChange
    }
  }

  return (
    <HubDialog
      show={show}
      width={680}
      onClose={onClose}
      title={t('content.title')}
      actions={(
        <button
          type="button"
          className="hub-btn primary"
          onClick={() => {
            onNewSkill?.()
            onClose()
          }}
        >
          <Plus size={15} /> {t('content.new')}
        </button>
      )}
    >
      <div className="hub-content-toolbar">
        <div className="hub-content-tabs" role="tablist" aria-label={t('content.title')}>
          {(['all', 'official', 'global'] as Filter[]).map((f) => (
            <button
              key={f}
              type="button"
              role="tab"
              aria-selected={filter === f}
              className={`hub-content-tab${filter === f ? ' active' : ''}`}
              onClick={() => setFilter(f)}
            >
              {t(`content.tab.${f}`)} {counts[f]}
            </button>
          ))}
        </div>
        <input
          type="search"
          className="hub-content-search"
          value={query}
          placeholder={t('content.search')}
          aria-label={t('content.search')}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button
          type="button"
          className="hub-btn"
          onClick={() => fileInputRef.current?.click()}
          title={t('content.import')}
        >
          <Upload size={15} /> {t('content.import')}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".zip"
          hidden
          onChange={(e) => void handleImport(e)}
        />
      </div>

      <section className="hub-section">
        {loading ? (
          <p className="hub-content-hint">{t('content.loading')}</p>
        ) : visible.length === 0 ? (
          <div className="hub-content-empty">
            <Layers size={28} aria-hidden="true" />
            <p className="hub-content-empty-title">{t('content.empty.title')}</p>
            <p className="hub-content-empty-hint">{t('content.empty.hint')}</p>
          </div>
        ) : (
          <ul className="hub-ai-list hub-skill-list">
            {visible.map((s) => (
              <li key={s.path}>
                <div className={`hub-skill-row${s.tombstoned ? ' hub-skill-row-disabled' : ''}`}>
                  <button
                    type="button"
                    className="hub-skill-main"
                    aria-expanded={expanded === s.path}
                    onClick={() => void toggleView(s)}
                  >
                    {expanded === s.path ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                    <FileText size={16} aria-hidden="true" />
                    <span className="hub-ai-info">
                      <strong>{s.name}</strong>
                      <em>{s.description}</em>
                    </span>
                    <span className={`hub-badge hub-badge-${s.source}`}>
                      {s.source === 'global' ? t('content.badge.mine') : t('content.badge.official')}
                    </span>
                    {s.source === 'official' && s.tombstoned && (
                      <span className="hub-badge hub-badge-disabled">{t('content.badge.disabled')}</span>
                    )}
                  </button>
                  <button
                    type="button"
                    className="hub-skill-export"
                    onClick={() => void handleExport(s)}
                    aria-label={t('content.export')}
                    title={t('content.export')}
                  >
                    <Download size={15} />
                  </button>
                  {s.source === 'global' && (
                    <>
                      <button
                        type="button"
                        className="hub-skill-reveal"
                        onClick={() => void reveal(s)}
                        aria-label={t('content.reveal')}
                        title={t('content.reveal')}
                      >
                        <FolderOpen size={15} />
                      </button>
                      <button
                        type="button"
                        className="hub-skill-delete"
                        onClick={() => void handleDelete(s)}
                        aria-label={t('content.delete')}
                        title={t('content.delete')}
                      >
                        <Trash2 size={15} />
                      </button>
                    </>
                  )}
                  {s.source === 'official' &&
                    (s.tombstoned ? (
                      <button
                        type="button"
                        className="hub-skill-enable"
                        onClick={() => void handleEnable(s)}
                        aria-label={t('content.enable')}
                        title={t('content.enable')}
                      >
                        <Eye size={15} />
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="hub-skill-disable"
                        onClick={() => void handleDisable(s)}
                        aria-label={t('content.disable')}
                        title={t('content.disable')}
                      >
                        <EyeOff size={15} />
                      </button>
                    ))}
                </div>
                {expanded === s.path && (
                  <pre className="hub-skill-preview">{preview || t('content.loading')}</pre>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </HubDialog>
  )
}
