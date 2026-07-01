export const PROVIDERS = [
  { v: 'deepseek', l: 'DeepSeek', base: 'https://api.deepseek.com/v1',
    models: ['deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-chat', 'deepseek-reasoner'] },
  { v: 'openai',   l: 'OpenAI',   base: 'https://api.openai.com/v1',
    models: ['gpt-5.5', 'gpt-5.5-pro', 'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.1'] },
  { v: 'anthropic',l: 'Anthropic',base: 'https://api.anthropic.com',
    models: ['claude-opus-4-8', 'claude-sonnet-5', 'claude-fable-5', 'claude-sonnet-4-6'] },
  { v: 'gemini',   l: 'Gemini',   base: 'https://generativelanguage.googleapis.com',
    models: ['gemini-3.5-flash', 'gemini-3.1-pro', 'gemini-3-pro', 'gemini-2.5-pro'] },
]
