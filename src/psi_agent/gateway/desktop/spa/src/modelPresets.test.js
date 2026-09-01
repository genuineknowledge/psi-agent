import { describe, expect, it } from 'vitest'
import { MODEL_PRESETS, getModelPreset, presetToAiPayload } from './modelPresets.js'
import { PROVIDERS } from './providers.js'

describe('modelPresets', () => {
  it('each preset has display and connect fields', () => {
    for (const p of MODEL_PRESETS) {
      expect(p.id).toBeTruthy()
      expect(p.label).toBeTruthy()
      expect(p.provider).toBeTruthy()
      expect(p.model).toBeTruthy()
      expect(p.base_url.startsWith('https://')).toBe(true)
    }
  })

  it('getModelPreset finds by id', () => {
    expect(getModelPreset('deepseek-official')?.label).toBe('DeepSeek')
    expect(getModelPreset('missing')).toBeUndefined()
  })

  it('presetToAiPayload maps api key only', () => {
    const p = getModelPreset('deepseek-official')
    expect(presetToAiPayload(p, ' sk-abc ')).toEqual({
      provider: 'deepseek',
      model: 'deepseek-v4-flash',
      base_url: 'https://api.deepseek.com/v1',
      api_key: 'sk-abc',
    })
  })

  it('uses the any-llm dashscope provider for both Qwen connection paths', () => {
    const preset = getModelPreset('qwen-official')
    expect(presetToAiPayload(preset, ' qwen-key ')).toEqual({
      provider: 'dashscope',
      model: 'qwen-max',
      base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      api_key: 'qwen-key',
    })

    expect(PROVIDERS.find(provider => provider.l === '通义千问 Qwen')).toMatchObject({
      v: 'dashscope',
      base: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    })
  })
})
