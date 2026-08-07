import { useEffect, useMemo, useState } from 'react'
import { Bot } from 'lucide-react'
import type { AiInfo } from '../../services/api'
import { createAi, listAis, listSessions } from '../../services/api'
import {
  clearAiPool,
  dedupeAisForDisplay,
  ensureDefaultAi,
  hydrateAiForSessions,
  purgePlaceholderAis,
  writeStoredAiId,
} from '../../services/bootstrapAi'
import { sessionBackendId } from '../../services/workspaceMatch'
import {
  getModelPreset,
  MODEL_PRESETS,
  presetToAiPayload,
} from '../../services/modelPresets'
import HubDialog from './HubDialog'

export const FREE_MODEL_NOTICE_TITLE = '已切换为免费模型（远程 deepseek-v4-flash）'
export const FREE_MODEL_NOTICE_BODY = '免费模型由远程服务提供，响应速度受服务负载与网络影响，可能较慢或出现波动'
export const FREE_MODEL_NOTICE = `${FREE_MODEL_NOTICE_TITLE}。${FREE_MODEL_NOTICE_BODY}`

type Props = {
  show: boolean
  onClose: () => void
  selectedAiId: string | null
  onSelectAi: (id: string | null) => void
  onOpenAdvanced: () => void
  onToast?: (message: string, durationMs?: number) => void
  onFreeModelNotice?: () => void
  onAisChanged?: (ais: AiInfo[]) => void
}

export default function HubModelsPanel({
  show,
  onClose,
  selectedAiId,
  onSelectAi,
  onOpenAdvanced,
  onToast,
  onFreeModelNotice,
  onAisChanged,
}: Props) {
  const [ais, setAis] = useState<AiInfo[]>([])
  const [presetId, setPresetId] = useState<string | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [pendingConnectedId, setPendingConnectedId] = useState<string | null>(null)

  const preset = useMemo(
    () => (presetId ? getModelPreset(presetId) : undefined),
    [presetId],
  )

  const visibleAis = useMemo(
    () => dedupeAisForDisplay(ais, selectedAiId),
    [ais, selectedAiId],
  )

  useEffect(() => {
    if (!show) return
    setPresetId(null)
    setApiKey('')
    setConnecting(false)
    setPendingConnectedId(null)
    void listAis()
      .then((list) => {
        setAis(list)
        onAisChanged?.(list)
      })
      .catch((e) => onToast?.(e instanceof Error ? e.message : '加载模型失败'))
  }, [show, onAisChanged, onToast])

  const connect = async () => {
    if (connecting) return
    if (!pendingConnectedId && (!preset || !apiKey.trim())) return
    setConnecting(true)
    try {
      if (pendingConnectedId) {
        onSelectAi(pendingConnectedId)
        writeStoredAiId(pendingConnectedId)
        onClose()
        return
      }
      // Connecting a real key: drop leftover free placeholders so they cannot stay ais[0].
      await purgePlaceholderAis()
      const info = await createAi(presetToAiPayload(preset, apiKey))
      const list = await listAis()
      setAis(list)
      onAisChanged?.(list)
      onSelectAi(info.id)
      writeStoredAiId(info.id)
      onToast?.(`${preset.label} 已连接`)
      onClose()
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : '连接失败')
    } finally {
      setConnecting(false)
    }
  }

  /** Free model = clear local keys, then revive Session backends (same id) as free
   * remotes so existing tasks stay chatable after refresh. No sessions → create one
   * free default via ensureDefaultAi. */
  const useFreeModel = async () => {
    if (connecting) return
    setConnecting(true)
    try {
      await clearAiPool()
      const sessions = await listSessions().catch(() => [])
      let { ais, preferred } = await hydrateAiForSessions(
        sessions.map((s) => sessionBackendId(s)),
        null,
      )
      if (ais.length === 0) {
        preferred = await ensureDefaultAi(null)
        ais = preferred ? await listAis() : []
      }
      setAis(ais)
      onAisChanged?.(ais)
      if (preferred?.id) {
        onSelectAi(preferred.id)
        writeStoredAiId(preferred.id)
        onToast?.(FREE_MODEL_NOTICE, 6000)
        onFreeModelNotice?.()
      } else {
        onSelectAi(null)
        onToast?.('免费模型暂时不可用，请检查网络或改连自有 API')
      }
      onClose()
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : '切换免费模型失败')
    } finally {
      setConnecting(false)
    }
  }

  return (
    <HubDialog
      show={show}
      width={560}
      onClose={onClose}
      title={(
        <div className="hub-models-title">
          <span>模型池</span>
          <button
            type="button"
            className="hub-link"
            onClick={() => {
              onClose()
              onOpenAdvanced()
            }}
          >
            高级配置
          </button>
        </div>
      )}
      actions={(
        <>
          <button
            type="button"
            className="hub-btn primary soft"
            disabled={connecting}
            onClick={() => void useFreeModel()}
          >
            使用免费模型
          </button>
          <button
            type="button"
            className="hub-btn primary"
            disabled={connecting || !((preset && apiKey.trim()) || pendingConnectedId)}
            onClick={() => void connect()}
          >
            {connecting ? '连接中…' : '连接'}
          </button>
        </>
      )}
    >
      {visibleAis.length > 0 && (
        <section className="hub-section">
          <h4>已连接</h4>
          <ul className="hub-ai-list">
            {visibleAis.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  className={`hub-ai-row ${a.id === selectedAiId || a.id === pendingConnectedId ? 'active' : ''}`}
                  onClick={() => {
                    setPendingConnectedId(a.id)
                    setPresetId(null)
                    setApiKey('')
                  }}
                >
                  <Bot size={18} />
                  <span className="hub-ai-info">
                    <strong>{a.model || a.id}</strong>
                    <em>{a.provider}</em>
                  </span>
                  {a.id === selectedAiId ? <span className="hub-badge">当前</span> : a.id === pendingConnectedId ? <span className="hub-badge">待连接</span> : null}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="hub-section">
        <h4>选择模型</h4>
        <div className="hub-preset-grid">
          {MODEL_PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              className={`hub-preset-card ${presetId === p.id ? 'active' : ''}`}
              title={p.hint || p.label}
              onClick={() => {
                setPendingConnectedId(null)
                setPresetId(p.id)
                setApiKey('')
              }}
            >
              <span className="hub-preset-mark" style={{ background: `${p.accent}22`, color: p.accent }}>
                {p.mark}
              </span>
              <span>{p.label}</span>
            </button>
          ))}
        </div>
      </section>

      {preset && (
        <section className="hub-section hub-key-box">
          <h4>API Key</h4>
          <p>
            连接 <strong>{preset.label}</strong>
            <span> · {preset.model}</span>
          </p>
          <input
            type="password"
            value={apiKey}
            placeholder="sk-..."
            autoComplete="off"
            onChange={(e) => setApiKey(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                void connect()
              }
            }}
          />
        </section>
      )}
    </HubDialog>
  )
}
