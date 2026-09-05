export type ProviderDef = {
  v: string
  l: string
  base: string
  models: string[]
}

/** Same provider table as spa v1 AiDialog. */
export const PROVIDERS: ProviderDef[] = [
  { v: 'deepseek', l: 'DeepSeek', base: 'https://api.deepseek.com/v1', models: ['deepseek-v4-pro', 'deepseek-v4-flash'] },
  { v: 'openai', l: 'OpenAI', base: 'https://api.openai.com/v1', models: ['gpt-5.5', 'gpt-5.5-pro', 'gpt-5.4', 'gpt-5.1'] },
  { v: 'anthropic', l: 'Anthropic', base: 'https://api.anthropic.com', models: ['claude-opus-4-8', 'claude-sonnet-5', 'claude-fable-5', 'claude-sonnet-4-6'] },
  { v: 'gemini', l: 'Gemini', base: 'https://generativelanguage.googleapis.com', models: ['gemini-3.5-flash', 'gemini-3.1-pro'] },
  { v: 'qwen', l: '通义千问 Qwen', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: ['qwen3.7-max', 'qwen3.7-plus', 'qwen-max', 'qwen-plus'] },
  { v: 'zhipu', l: '智谱 GLM', base: 'https://open.bigmodel.cn/api/paas/v4', models: ['glm-4.6', 'glm-4.6-flash', 'glm-4.5'] },
  { v: 'moonshot', l: '月之暗面 Kimi', base: 'https://api.moonshot.cn/v1', models: ['kimi-k2.7', 'kimi-k2.6', 'moonshot-v1-128k'] },
  { v: 'doubao', l: '豆包 (火山方舟)', base: 'https://ark.cn-beijing.volces.com/api/v3', models: ['doubao-seed-2.1-pro', 'doubao-seed-2.1-turbo'] },
  { v: 'stepfun', l: '阶跃星辰 StepFun', base: 'https://api.stepfun.com/v1', models: ['step-3.7-flash'] },
  { v: 'minimax', l: 'MiniMax', base: 'https://api.minimax.chat/v1', models: ['MiniMax-M3', 'MiniMax-M2.5'] },
  { v: 'baichuan', l: '百川 Baichuan', base: 'https://api.baichuan-ai.com/v1', models: ['Baichuan4-Turbo', 'Baichuan4-Air', 'Baichuan4'] },
  { v: 'yi', l: '零一万物 Yi', base: 'https://api.lingyiwanwu.com/v1', models: ['yi-lightning'] },
  { v: 'xai', l: 'xAI Grok', base: 'https://api.x.ai/v1', models: ['grok-4.3', 'grok-4-fast-reasoning'] },
]
