import { store } from '../store.js'
import { htmlEscape, renderMd, saveHistory, loadHistory } from '../utils.js'
import { readSSE } from './useSSE.js'
import { streamChat } from '../api.js'
import { scrollToBottomIfLocked } from './useScroll.js'

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

function addMessage(role, id) {
  const m = { id, role, text: '', html: '', files: [], stopped: false }
  store.messages.push(m)
  scrollToBottomIfLocked()
  return store.messages[store.messages.length - 1]
}

async function encodeFiles(files, um) {
  for (const f of files) {
    try {
      const b64 = await new Promise((resolve, reject) => {
        const r = new FileReader()
        r.onload = () => resolve(r.result.split(',')[1])
        r.onerror = e => reject(e)
        r.readAsDataURL(f)
      })
      if (um) um.files.push({ name: f.name, data: b64 })
    } catch {}
  }
}

export async function sendMessage() {
  if (store.streaming || !store.selectedSessionId) return
  const text = store.inputText.trim()
  const files = [...store.selectedFiles]
  if (!text && !files.length) return

  store.streaming = true
  store.inputText = ''
  store.selectedFiles = []
  store.uploadResetToken++
  store.userHasScrolledUp = false

  let um = null
  if (text) {
    um = addMessage('user', `u-${Date.now()}`)
    um.text = text
    um.html = htmlEscape(text)
    await encodeFiles(files, um)
  } else if (files.length) {
    um = addMessage('user', `u-${Date.now()}`)
    um.text = `[Uploaded File${files.length > 1 ? 's' : ''}: ${files.map(f => f.name).join(', ')}]`
    um.html = htmlEscape(um.text)
    await encodeFiles(files, um)
  }

  const fd = new FormData()
  const chunks = []
  if (text) chunks.push({ type: 'text', text })
  fd.append('chunks', JSON.stringify(chunks))
  for (const f of files) fd.append('file', f)

  let asst = addMessage('assistant', `a-${Date.now()}`)

  const controller = new AbortController()
  store.abortController = controller

  try {
    const reader = await streamChat(store.selectedSessionId, fd, controller.signal)
    for await (const chunkData of readSSE(reader)) {
      if (chunkData.type === 'text' && chunkData.text !== undefined) {
        if (!asst) asst = addMessage('assistant', `a-${Date.now()}`)
        asst.text += chunkData.text
        asst.html = renderMd(asst.text)
      } else if (chunkData.type === 'blob') {
        if (!asst) asst = addMessage('assistant', `a-${Date.now()}`)
        asst.files.push({ name: chunkData.name, data: chunkData.data })
      } else if (chunkData.type === 'error') {
        if (!asst) asst = addMessage('assistant', `a-${Date.now()}`)
        asst.text += '\n[Error: ' + chunkData.error + ']'
        asst.html = renderMd(asst.text)
      } else if (chunkData.type === 'reasoning') {
        // reasoning marks a non-text step (tool call/result or model thinking);
        // close the current text bubble so the next text starts a new one.
        // Only split when the current bubble actually has content — avoids
        // stranding the pre-created empty bubble or splitting on a leading think.
        if (asst && (asst.text || asst.files.length)) asst = null
      }
      scrollToBottomIfLocked()
    }
  } catch (e) {
    if (!asst) asst = addMessage('assistant', `a-${Date.now()}`)
    if (e.name === 'AbortError') {
      // 用户主动停止：保留已生成内容，用独立标记展示（样式固定，不走 markdown）
      asst.stopped = true
    } else {
      asst.text += '\n[Error: ' + e.message + ']'
      asst.html = renderMd(asst.text)
    }
  }

  // Drop a trailing empty assistant bubble (e.g. a reasoning-only turn, or the
  // pre-created bubble that never received text before the stream ended).
  const last = store.messages[store.messages.length - 1]
  if (last && last.role === 'assistant' && !last.text && !last.files.length) {
    store.messages.pop()
  }

  store.streaming = false
  store.abortController = null
  saveHistory(store.selectedSessionId, store.messages)

  const currentTitle = store.sessionTitles[store.selectedSessionId]
  if (!currentTitle || currentTitle === '新会话' || currentTitle.trim() === '') generateTitle()
}

export function stopMessage() {
  // 中止当前 fetch；取消信号会一路传导到后端，agent 停止生成。
  // sendMessage 的 catch(AbortError) 负责保留已生成内容并重置 streaming 状态。
  if (store.abortController) store.abortController.abort()
}

export function undoFrom(index) {
  // 撤回：删除索引 index 处的消息及其之后的所有消息，仅影响前端显示与本地历史。
  if (index < 0 || index >= store.messages.length) return
  if (store.streaming) return
  store.messages.splice(index)
  if (store.selectedSessionId) saveHistory(store.selectedSessionId, store.messages)
}

async function generateTitle() {
  const sid = store.selectedSessionId
  if (!sid) return
  const msgs = loadHistory(sid)
  if (!msgs.length) return
  const userMsg = msgs.find(m => m.role === 'user')
  const asstMsg = msgs.find(m => m.role === 'assistant')
  if (!userMsg || !asstMsg) return
  try {
    const r = await fetch(origin() + '/titles/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: sid, user_text: userMsg.text, assistant_text: asstMsg.text }),
    })
    if (!r.ok) return
    const data = await r.json()
    if (data.title) store.sessionTitles[sid] = data.title
  } catch (e) {}
}
