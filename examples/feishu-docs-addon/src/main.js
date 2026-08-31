/**
 * Haitun Agent 云文档小组件 —— 在飞书文档里跟 agent 对话, 并可把回答插入正文。
 *
 * 三件事值得先说清楚:
 *
 * 1. **身份只用于隔离。** `Service.User.getUserId()` 是客户端值, 网关无从验证, 所以它只是
 *    「谁的上下文」的键, 不是认证。真正的访问控制是设置里那个预共享 token。
 * 2. **默认不写文档。** 回答先落在小组件自己的气泡里, 只有用户点「插入文档」才动正文 ——
 *    小组件常出现在多人协作的文档里, 自动写入等于替所有协作者做决定。
 * 3. **写入前必须查权限。** 只读模式 / 无编辑权时插入 API 会失败, 与其让用户点了没反应,
 *    不如先把按钮禁掉并说明原因。
 */
import { BlockitClient } from '@lark-opdev/block-docs-addon-api'

import { streamChat } from './gateway.js'
import { DEFAULT_SETTINGS, isConfigured, readSettings, settingsChangeset } from './settings.js'
import './styles.css'

const app = new BlockitClient().initAPI()

/** 小组件内的会话记录 —— 只在内存里, 刷新即清空 (Record 只存后端设置)。 */
const state = {
  settings: { ...DEFAULT_SETTINGS },
  messages: [],
  busy: false,
  editable: false,
  docToken: '',
  userId: '',
  showSettings: false,
  notice: null,
}

const el = {}

function h(tag, className, text) {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text != null) node.textContent = text
  return node
}

function buildUI(root) {
  root.textContent = ''
  const shell = h('div', 'ha-shell')

  const header = h('div', 'ha-header')
  header.appendChild(h('span', 'ha-title', 'Haitun Agent'))
  el.settingsBtn = h('button', 'ha-icon-btn', '设置')
  el.settingsBtn.type = 'button'
  el.settingsBtn.addEventListener('click', () => {
    state.showSettings = !state.showSettings
    render()
  })
  header.appendChild(el.settingsBtn)
  shell.appendChild(header)

  el.settingsPanel = h('div', 'ha-settings')
  buildSettingsPanel(el.settingsPanel)
  shell.appendChild(el.settingsPanel)

  el.log = h('div', 'ha-log')
  shell.appendChild(el.log)

  el.notice = h('div', 'ha-notice')
  shell.appendChild(el.notice)

  const composer = h('div', 'ha-composer')
  el.input = document.createElement('textarea')
  el.input.className = 'ha-input'
  el.input.rows = 2
  el.input.placeholder = '问点什么…（Enter 发送，Shift+Enter 换行）'
  el.input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void send()
    }
  })
  composer.appendChild(el.input)

  el.sendBtn = h('button', 'ha-send', '发送')
  el.sendBtn.type = 'button'
  el.sendBtn.addEventListener('click', () => void send())
  composer.appendChild(el.sendBtn)
  shell.appendChild(composer)

  root.appendChild(shell)
}

function buildSettingsPanel(panel) {
  panel.appendChild(h('label', 'ha-label', '网关地址'))
  el.baseUrl = document.createElement('input')
  el.baseUrl.className = 'ha-field'
  el.baseUrl.placeholder = 'http://localhost:8000'
  panel.appendChild(el.baseUrl)

  panel.appendChild(h('label', 'ha-label', '访问令牌 (--docs-addon-token)'))
  el.token = document.createElement('input')
  el.token.className = 'ha-field'
  el.token.type = 'password'
  el.token.placeholder = '与网关启动参数一致'
  panel.appendChild(el.token)

  panel.appendChild(
    h('p', 'ha-hint', '令牌随本区块保存，文档的协作者都能读到 —— 请当作团队共享密钥。'),
  )

  const saveBtn = h('button', 'ha-save', '保存')
  saveBtn.type = 'button'
  saveBtn.addEventListener('click', () => void saveSettings())
  panel.appendChild(saveBtn)
}

async function saveSettings() {
  state.settings = {
    baseUrl: el.baseUrl.value,
    token: el.token.value,
  }
  try {
    await app.Record.setRecord(settingsChangeset(state.settings))
    state.showSettings = false
    setNotice('设置已保存', 'ok')
  } catch (error) {
    setNotice(`保存失败：${error.message}`, 'error')
  }
  render()
}

function setNotice(text, kind) {
  state.notice = text ? { text, kind } : null
  renderNotice()
}

function renderNotice() {
  el.notice.textContent = state.notice ? state.notice.text : ''
  el.notice.className = `ha-notice${state.notice ? ` ha-notice--${state.notice.kind}` : ''}`
}

function renderLog() {
  el.log.textContent = ''
  for (const message of state.messages) {
    const row = h('div', `ha-msg ha-msg--${message.role}`)
    row.appendChild(h('div', 'ha-msg-body', message.text || (message.pending ? '…' : '')))

    // 只给已完成、非空的助手回答挂插入按钮 —— 半截的流式文本插进文档没有意义。
    if (message.role === 'assistant' && !message.pending && message.text.trim()) {
      const insertBtn = h('button', 'ha-insert', state.editable ? '插入文档' : '文档只读，无法插入')
      insertBtn.type = 'button'
      insertBtn.disabled = !state.editable
      insertBtn.addEventListener('click', () => void insertIntoDoc(message.text, insertBtn))
      row.appendChild(insertBtn)
    }
    el.log.appendChild(row)
  }
  el.log.scrollTop = el.log.scrollHeight
}

function render() {
  el.settingsPanel.classList.toggle('ha-settings--open', state.showSettings)
  if (state.showSettings) {
    el.baseUrl.value = state.settings.baseUrl
    el.token.value = state.settings.token
  }
  el.sendBtn.disabled = state.busy
  el.input.disabled = state.busy
  renderLog()
  renderNotice()
  void syncHeight()
}

/** 让宿主把 iframe 高度贴合内容 —— 否则长回答会被裁掉。 */
async function syncHeight() {
  try {
    const height = document.documentElement.scrollHeight
    await app.Bridge.updateHeight(height)
  } catch (_) {
    // 高度同步是锦上添花, 失败不该打断对话。
  }
}

async function insertIntoDoc(markdown, button) {
  button.disabled = true
  button.textContent = '插入中…'
  try {
    const blockRef = await app.getActiveBlockRef()
    // insertBlocksByMarkdown 让标题/列表/代码块落成真正的原生块, 比塞一段纯文本有用。
    await app.Document.insertBlocksByMarkdown(markdown, blockRef)
    button.textContent = '已插入'
    setNotice('已插入到文档', 'ok')
  } catch (error) {
    button.disabled = false
    button.textContent = '插入文档'
    setNotice(`插入失败：${error.message}`, 'error')
  }
}

async function send() {
  const text = el.input.value.trim()
  if (!text || state.busy) return

  if (!isConfigured(state.settings)) {
    state.showSettings = true
    setNotice('请先填写网关地址与访问令牌', 'error')
    render()
    return
  }

  el.input.value = ''
  state.messages.push({ role: 'user', text })
  const reply = { role: 'assistant', text: '', pending: true }
  state.messages.push(reply)
  state.busy = true
  setNotice('', null)
  render()

  try {
    const events = streamChat({
      baseUrl: state.settings.baseUrl,
      token: state.settings.token,
      docToken: state.docToken,
      userId: state.userId,
      text,
    })
    for await (const event of events) {
      if (event.type === 'text') {
        reply.text += event.text
        renderLog()
        void syncHeight()
      } else if (event.type === 'error') {
        setNotice(`出错：${event.error}`, 'error')
      }
      // reasoning / blob 事件先忽略: 前者是过程噪音, 后者要下载附件, 都不是这一版的重点。
    }
    if (!reply.text.trim()) reply.text = '（没有返回内容）'
  } catch (error) {
    reply.text = reply.text || '（请求失败）'
    setNotice(describeError(error), 'error')
  } finally {
    reply.pending = false
    state.busy = false
    render()
  }
}

function describeError(error) {
  // 跨源被拦、网关没起、域名不在 CSP 白名单, 在 JS 里都表现为一个信息量极低的 TypeError,
  // 所以这里主动把三种可能一起说出来, 免得用户只看到「Failed to fetch」。
  if (error instanceof TypeError) {
    return '连不上网关：确认网关在运行、地址正确，且该域名已加入飞书后台的服务器域名白名单'
  }
  return `出错：${error.message}`
}

async function boot() {
  const root = document.getElementById('root')
  buildUI(root)

  try {
    const [docToken, userId, record] = await Promise.all([
      app.getActiveWikiToken().catch(() => ''),
      app.Service.User.getUserId(),
      app.Record.getRecord().catch(() => ({})),
    ])
    state.userId = userId || ''
    state.settings = readSettings(record)

    const docRef = await app.getActiveDocumentRef()
    // getActiveWikiToken 只在知识库里有值; 普通文档回落到 documentRef 的 docToken。
    state.docToken = docToken || docRef?.docToken || ''

    const permission = await app.Service.Permission.getDocumentPermission(docRef)
    state.editable = Boolean(permission?.editable)
  } catch (error) {
    setNotice(`初始化失败：${error.message}`, 'error')
  }

  if (!isConfigured(state.settings)) {
    state.showSettings = true
    setNotice('首次使用：请填写网关地址与访问令牌', 'info')
  }
  render()

  // useHostLoading 为 true 时, 宿主一直显示 loading 直到这一句。
  await app.LifeCycle.notifyAppReady()
}

void boot()
