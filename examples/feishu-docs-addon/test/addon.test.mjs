/**
 * 纯逻辑单测 —— 跑 `npm test`, 无需浏览器和飞书环境。
 *
 * 覆盖的是三个「错了很难发现」的地方: SSE 分块解析(JSON 可能被切断在任意字节)、
 * keepalive 注释不能被当成正文、设置的读写与归一化。UI 与 SDK 调用不在这里测,
 * 那要真实宿主。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { describeStatus } from '../src/gateway.js'
import { DEFAULT_SETTINGS, isConfigured, normalizeBaseUrl, readSettings, settingsChangeset } from '../src/settings.js'
import { readSSE } from '../src/sse.js'

/** 把若干字符串当成网络分片喂给 readSSE。 */
function readerFrom(chunks) {
  const enc = new TextEncoder()
  let i = 0
  return {
    read: async () => (i < chunks.length ? { done: false, value: enc.encode(chunks[i++]) } : { done: true }),
  }
}

async function collect(chunks) {
  const out = []
  for await (const event of readSSE(readerFrom(chunks))) out.push(event)
  return out
}

test('reassembles a JSON event split across network chunks', async () => {
  // 网关的一条事件可能被 TCP 切在任意位置; 按字节拼不回来就会丢字。
  const events = await collect(['data: {"type":"te', 'xt","text":"好"}\n', 'data: [DONE]\n\n'])
  assert.deepEqual(events, [{ type: 'text', text: '好' }])
})

test('ignores keepalive comments and the DONE sentinel', async () => {
  const events = await collect([
    'data: {"type":"text","text":"你"}\n',
    ': keepalive\n\n',
    'data: {"type":"text","text":"好"}\n',
    'data: [DONE]\n\n',
  ])
  assert.equal(
    events.map((e) => e.text).join(''),
    '你好',
    'keepalive 注释不能进正文, 否则长回答里会夹进 "keepalive" 字样',
  )
})

test('passes through reasoning and error events', async () => {
  const events = await collect([
    'data: {"type":"reasoning","text":"想","kind":"thinking"}\n',
    'data: {"type":"error","error":"boom"}\n',
  ])
  assert.deepEqual(events.map((e) => e.type), ['reasoning', 'error'])
})

test('normalizeBaseUrl strips trailing slashes', () => {
  assert.equal(normalizeBaseUrl('http://h:8000///'), 'http://h:8000')
  assert.equal(normalizeBaseUrl('  http://h:8000  '), 'http://h:8000')
  assert.equal(normalizeBaseUrl(undefined), '')
})

test('readSettings falls back to defaults and normalizes stored values', () => {
  assert.equal(readSettings({}).baseUrl, DEFAULT_SETTINGS.baseUrl)
  assert.equal(readSettings(undefined).token, '')
  assert.equal(readSettings({ haitunBackend: { baseUrl: 'http://x/', token: 't' } }).baseUrl, 'http://x')
})

test('isConfigured requires both url and token', () => {
  assert.equal(isConfigured({ baseUrl: 'http://x', token: '' }), false)
  assert.equal(isConfigured({ baseUrl: '', token: 't' }), false)
  assert.equal(isConfigured({ baseUrl: 'http://x', token: 't' }), true)
})

test('settingsChangeset writes a normalized value under the record key', () => {
  const [changeset] = settingsChangeset({ baseUrl: 'http://x/', token: 't' })
  assert.equal(changeset.type, 'replace')
  assert.deepEqual(changeset.path, ['haitunBackend'])
  assert.deepEqual(changeset.value, { baseUrl: 'http://x', token: 't' })
})

test('status messages name the concrete fix', () => {
  // 「401」本身对用户没用, 得说清该去改什么。
  assert.match(describeStatus(401), /token/)
  assert.match(describeStatus(404), /--docs-addon-token/)
})
