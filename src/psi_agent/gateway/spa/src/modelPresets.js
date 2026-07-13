/**
 * Hub「大模型」快捷连接预设表。
 *
 * 展示层（label / mark / accent）与后端默认配置（provider / model / base_url）
 * 集中维护于此；HubModelsPanel 只消费本文件，用户仅需填写 api_key。
 *
 * 高级自定义（任意 provider / model / URL）仍走 AiDialog + providers.js。
 */

/** @typedef {object} ModelPreset
 * @property {string} id
 * @property {string} label
 * @property {string} mark  卡片展示用短标记（emoji 或缩写）
 * @property {string} accent  卡片品牌色（CSS 颜色）
 * @property {string} provider  POST /ais provider
 * @property {string} model  默认模型名
 * @property {string} base_url  默认接口地址
 * @property {string} [hint]  可选说明
 */

/** @type {ModelPreset[]} */
export const MODEL_PRESETS = [
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

/** @param {string} id @returns {ModelPreset | undefined} */
export function getModelPreset(id) {
  return MODEL_PRESETS.find(p => p.id === id)
}

/** @param {ModelPreset} preset @param {string} apiKey */
export function presetToAiPayload(preset, apiKey) {
  return {
    provider: preset.provider,
    model: preset.model,
    base_url: preset.base_url,
    api_key: apiKey.trim(),
  }
}
