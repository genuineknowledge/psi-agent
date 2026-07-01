import { store } from '../store.js'
import { htmlEscape, renderMd, saveHistory, loadHistory } from '../utils.js'
import { readSSE } from './useSSE.js'
import { streamChat } from '../api.js'
import { scrollToBottomIfLocked } from './useScroll.js'

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

function addMessage(role, id) {
  const m = { id, role, text: '', html: '', files: [] }
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

  const asst = addMessage('assistant', `a-${Date.now()}`)

  try {
    const reader = await streamChat(store.selectedSessionId, fd)
    for await (const chunkData of readSSE(reader)) {
      if (chunkData.type === 'text' && chunkData.text !== undefined) {
        asst.text += chunkData.text
        asst.html = renderMd(asst.text)
      } else if (chunkData.type === 'blob') {
        asst.files.push({ name: chunkData.name, data: chunkData.data })
      } else if (chunkData.type === 'error') {
        asst.text += '\n[Error: ' + chunkData.error + ']'
      }
      scrollToBottomIfLocked()
    }
  } catch (e) {
    asst.text += '\n[Error: ' + e.message + ']'
    asst.html = renderMd(asst.text)
  }

  store.streaming = false
  saveHistory(store.selectedSessionId, store.messages)

  const currentTitle = store.sessionTitles[store.selectedSessionId]
  if (!currentTitle || currentTitle === '新会话' || currentTitle.trim() === '') generateTitle()
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
