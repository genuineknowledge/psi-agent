import { useEffect, useMemo, useState } from 'react'
import { createAi, listAis, type AiInfo } from '../../services/api'
import { PROVIDERS } from '../../services/providers'
import HubDialog from './HubDialog'

type Props = {
  show: boolean
  onClose: () => void
  onSelectAi: (id: string) => void
  onToast?: (message: string) => void
  onAisChanged?: (ais: AiInfo[]) => void
  requireAi?: boolean
}

export default function HubAdvancedPanel({
  show,
  onClose,
  onSelectAi,
  onToast,
  onAisChanged,
  requireAi = false,
}: Props) {
  const [provider, setProvider] = useState(PROVIDERS[0]?.v ?? 'openai')
  const [model, setModel] = useState(PROVIDERS[0]?.models[0] ?? '')
  const [baseUrl, setBaseUrl] = useState(PROVIDERS[0]?.base ?? '')
  const [apiKey, setApiKey] = useState('')
  const [connecting, setConnecting] = useState(false)

  const current = useMemo(() => PROVIDERS.find((p) => p.v === provider), [provider])

  useEffect(() => {
    if (!show) return
    const first = PROVIDERS[0]
    if (!first) return
    setProvider(first.v)
    setModel(first.models[0] ?? '')
    setBaseUrl(first.base)
    setApiKey('')
  }, [show])

  const selectProvider = (v: string) => {
    const p = PROVIDERS.find((item) => item.v === v)
    if (!p) return
    setProvider(p.v)
    setBaseUrl(p.base)
    setModel(p.models[0] ?? '')
  }

  const connect = async () => {
    if (!apiKey.trim() || !model.trim() || !baseUrl.trim() || connecting) return
    setConnecting(true)
    try {
      const info = await createAi({
        provider,
        model: model.trim(),
        base_url: baseUrl.trim(),
        api_key: apiKey.trim(),
      })
      const list = await listAis()
      onAisChanged?.(list)
      onSelectAi(info.id)
      onToast?.('大模型已连接')
      onClose()
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : '连接失败')
    } finally {
      setConnecting(false)
    }
  }

  const handleClose = () => {
    if (requireAi) {
      onToast?.('请先连接至少一个大模型')
      return
    }
    onClose()
  }

  return (
    <HubDialog
      show={show}
      title="链接大模型"
      width={480}
      onClose={handleClose}
      actions={(
        <>
          <button type="button" className="hub-btn ghost" onClick={handleClose}>取消</button>
          <button
            type="button"
            className="hub-btn primary"
            disabled={!apiKey.trim() || !model.trim() || connecting}
            onClick={() => void connect()}
          >
            {connecting ? '连接中…' : '链接'}
          </button>
        </>
      )}
    >
      <label className="hub-field">
        <span>供应商</span>
        <select value={provider} onChange={(e) => selectProvider(e.target.value)}>
          {PROVIDERS.map((p) => (
            <option key={p.v} value={p.v}>{p.l}</option>
          ))}
        </select>
      </label>
      <label className="hub-field">
        <span>模型名称</span>
        <input
          value={model}
          list="hub-advanced-models"
          onChange={(e) => setModel(e.target.value)}
          placeholder="选择或输入模型名称"
        />
        <datalist id="hub-advanced-models">
          {(current?.models ?? []).map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      </label>
      <label className="hub-field">
        <span>接口地址</span>
        <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://..." />
      </label>
      <label className="hub-field">
        <span>API 密钥</span>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="sk-..."
          autoComplete="off"
        />
      </label>
    </HubDialog>
  )
}
