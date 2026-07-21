import { useEffect, useMemo, useState } from 'react'
import { Bot } from 'lucide-react'
import type { AiInfo } from '../../services/api'
import { createAi, listAis } from '../../services/api'
import { clearAiPool } from '../../services/bootstrapAi'
import {
  getModelPreset,
  MODEL_PRESETS,
  presetToAiPayload,
} from '../../services/modelPresets'
import HubDialog from './HubDialog'

type Props = {
  show: boolean
  onClose: () => void
  selectedAiId: string | null
  onSelectAi: (id: string | null) => void
  onOpenAdvanced: () => void
  onToast?: (message: string) => void
  onAisChanged?: (ais: AiInfo[]) => void
}

export default function HubModelsPanel({
  show,
  onClose,
  selectedAiId,
  onSelectAi,
  onOpenAdvanced,
  onToast,
  onAisChanged,
}: Props) {
  const [ais, setAis] = useState<AiInfo[]>([])
  const [presetId, setPresetId] = useState<string | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [connecting, setConnecting] = useState(false)

  const preset = useMemo(
    () => (presetId ? getModelPreset(presetId) : undefined),
    [presetId],
  )

  useEffect(() => {
    if (!show) return
    setPresetId(null)
    setApiKey('')
    setConnecting(false)
    void listAis()
      .then((list) => {
        setAis(list)
        onAisChanged?.(list)
      })
      .catch((e) => onToast?.(e instanceof Error ? e.message : '加载模型失败'))
  }, [show, onAisChanged, onToast])

  const connect = async () => {
    if (!preset || !apiKey.trim() || connecting) return
    setConnecting(true)
    try {
      const info = await createAi(presetToAiPayload(preset, apiKey))
      const list = await listAis()
      setAis(list)
      onAisChanged?.(list)
      onSelectAi(info.id)
      onToast?.(`${preset.label} 已连接`)
      onClose()
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : '连接失败')
    } finally {
      setConnecting(false)
    }
  }

  /** Free model = clear local config; remote defaults resolve lazily on first chat. */
  const useFreeModel = async () => {
    if (connecting) return
    setConnecting(true)
    try {
      await clearAiPool()
      setAis([])
      onAisChanged?.([])
      onSelectAi(null)
      onToast?.('已切换为免费模型（空配置，对话时走远程）')
      onClose()
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : '清空模型配置失败')
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
          <span>大模型</span>
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
            disabled={!preset || !apiKey.trim() || connecting}
            onClick={() => void connect()}
          >
            {connecting ? '连接中…' : '连接'}
          </button>
        </>
      )}
    >
      {ais.length > 0 && (
        <section className="hub-section">
          <h4>已连接</h4>
          <ul className="hub-ai-list">
            {ais.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  className={`hub-ai-row ${a.id === selectedAiId ? 'active' : ''}`}
                  onClick={() => onSelectAi(a.id)}
                >
                  <Bot size={18} />
                  <span className="hub-ai-info">
                    <strong>{a.model || a.id}</strong>
                    <em>{a.provider}</em>
                  </span>
                  {a.id === selectedAiId ? <span className="hub-badge">当前</span> : null}
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
