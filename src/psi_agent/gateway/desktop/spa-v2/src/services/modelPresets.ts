export type ModelPreset = {
  id: string
  label: string
  mark: string
  accent: string
  provider: string
  model: string
  base_url: string
  hint?: string
}

/** Same presets as spa v1 Hub「大模型」. */
export const MODEL_PRESETS: ModelPreset[] = [
  {
    id: 'deepseek-official',
    label: 'DeepSeek',
    mark: '🐋',
    accent: '#4d6bfe',
    provider: 'deepseek',
    model: 'deepseek-v4-flash',
    base_url: 'https://api.deepseek.com/v1',
    hint: 'DeepSeek 官方 API',
  },
  {
    id: 'openai-official',
    label: 'OpenAI',
    mark: 'GPT',
    accent: '#10a37f',
    provider: 'openai',
    model: 'gpt-5.4',
    base_url: 'https://api.openai.com/v1',
    hint: 'OpenAI 官方 API',
  },
  {
    id: 'gemini-official',
    label: 'Gemini',
    mark: 'G',
    accent: '#4285f4',
    provider: 'gemini',
    model: 'gemini-3.5-flash',
    base_url: 'https://generativelanguage.googleapis.com',
    hint: 'Google Gemini 官方 API',
  },
  {
    id: 'anthropic-official',
    label: 'Anthropic',
    mark: 'AI',
    accent: '#d4a574',
    provider: 'anthropic',
    model: 'claude-sonnet-5',
    base_url: 'https://api.anthropic.com',
    hint: 'Claude 官方 API',
  },
  {
    id: 'qwen-official',
    label: '通义千问',
    mark: 'Q',
    accent: '#615ced',
    provider: 'qwen',
    model: 'qwen-max',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    hint: '阿里云 DashScope 兼容模式',
  },
  {
    id: 'moonshot-official',
    label: 'Kimi',
    mark: 'K',
    accent: '#000000',
    provider: 'moonshot',
    model: 'kimi-k2.7',
    base_url: 'https://api.moonshot.cn/v1',
    hint: '月之暗面 Kimi 官方 API',
  },
  {
    id: 'zhipu-official',
    label: '智谱 GLM',
    mark: 'GLM',
    accent: '#3366ff',
    provider: 'zhipu',
    model: 'glm-4.6',
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    hint: '智谱 AI 官方 API',
  },
  {
    id: 'doubao-official',
    label: '豆包',
    mark: '豆',
    accent: '#325ab4',
    provider: 'doubao',
    model: 'doubao-seed-2.1-pro',
    base_url: 'https://ark.cn-beijing.volces.com/api/v3',
    hint: '火山方舟豆包',
  },
]

export function getModelPreset(id: string) {
  return MODEL_PRESETS.find((p) => p.id === id)
}

export function presetToAiPayload(preset: ModelPreset, apiKey: string) {
  return {
    provider: preset.provider,
    model: preset.model,
    base_url: preset.base_url,
    api_key: apiKey.trim(),
  }
}
