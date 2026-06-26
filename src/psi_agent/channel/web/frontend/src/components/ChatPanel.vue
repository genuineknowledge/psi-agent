<script setup>
import { ref, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { renderMarkdown } from '../lib/markdown.js'
import { createSession, deleteSession, streamMessage } from '../lib/chat.js'

const props = defineProps({
  workspace: { type: String, required: true },
})

// Each message: { role: 'user', text } or
//   { role: 'agent', answer, files: [{url, name}], error, loading }
const messages = ref([])
const input = ref('')
const busy = ref(false)
const logEl = ref(null)
const sessionId = ref(null)
// Follow the stream only while the user is parked at the bottom. Once they
// scroll up to read, stop yanking them back down on every incoming frame.
let stickToBottom = true

function onLogScroll() {
  const el = logEl.value
  if (!el) return
  // within 60px of the bottom counts as "at bottom"
  stickToBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
}

function scrollDown() {
  if (!stickToBottom) return
  nextTick(() => {
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  })
}

async function ensureSession() {
  if (sessionId.value) return sessionId.value
  const data = await createSession({ workspace: props.workspace })
  sessionId.value = data.session_id
  return sessionId.value
}

onMounted(() => {
  // Create the session up front so the first message has no extra latency.
  ensureSession().catch(() => {})
})

onBeforeUnmount(() => {
  deleteSession(sessionId.value)
})

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  input.value = ''
  busy.value = true

  messages.value.push({ role: 'user', text })
  stickToBottom = true // sending a message means: jump to the latest
  scrollDown()

  messages.value.push({ role: 'agent', answer: '', files: [], error: '', loading: true })
  // Hold the reactive proxy (not the raw object) so per-frame mutations below
  // actually trigger re-renders.
  const agent = messages.value[messages.value.length - 1]
  scrollDown()

  try {
    const id = await ensureSession()
    await streamMessage({
      sessionId: id,
      text,
      onEvent: (evt) => {
        agent.loading = false
        if (evt.event === 'error') { agent.error = evt.message; scrollDown(); return }
        if (evt.event === 'text_delta' && evt.text) agent.answer += evt.text
        if (evt.event === 'file') agent.files.push({ url: evt.url, name: evt.name })
        scrollDown()
      },
    })
  } catch (e) {
    agent.loading = false
    agent.error = String(e && e.message ? e.message : e)
    scrollDown()
  } finally {
    busy.value = false
  }
}

function onKeydown(e) {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send() }
}
</script>

<template>
  <section class="chat">
    <div class="chat-head">
      <div class="h-left">
        <span class="h-title">对话</span>
      </div>
      <span class="badge">{{ workspace }}</span>
    </div>

    <div ref="logEl" class="log" @scroll="onLogScroll">
      <div v-if="!messages.length" class="empty">
        发一条消息开始对话。在左侧选择 workspace 切换对应的 agent 服务。
      </div>
      <template v-for="(msg, i) in messages" :key="i">
        <div v-if="msg.role === 'user'" class="row user">
          <div class="bubble">{{ msg.text }}</div>
        </div>
        <div v-else class="row agent">
          <div class="bubble agent-stack">
            <div v-if="msg.loading" class="typing"><span></span><span></span><span></span></div>
            <div v-if="msg.answer" class="answer markdown-body" v-html="renderMarkdown(msg.answer)"></div>
            <div v-if="msg.files.length" class="files">
              <a v-for="(f, fi) in msg.files" :key="fi" class="file" :href="f.url" target="_blank" rel="noopener">
                {{ f.name }}
              </a>
            </div>
            <div v-if="msg.error" class="err">{{ msg.error }}</div>
          </div>
        </div>
      </template>
    </div>

    <div class="composer">
      <div class="pick">+</div>
      <textarea
        v-model="input"
        placeholder="输入消息…"
        rows="1"
        @keydown="onKeydown"
      ></textarea>
      <button class="send" :disabled="busy" @click="send">发送</button>
    </div>
  </section>
</template>

<style scoped>
.chat {
  display: flex; flex-direction: column; min-width: 0;
  border-radius: var(--r-2xl); background: var(--surface); border: 1px solid var(--line); overflow: hidden;
}
.chat-head {
  height: 52px; display: flex; align-items: center; justify-content: space-between;
  padding: 0 20px; border-bottom: 1px solid var(--line-soft); flex: 0 0 auto; gap: 10px;
}
.h-left { display: flex; align-items: center; gap: 8px; min-width: 0; }
.h-title { font-weight: 650; font-size: 14px; white-space: nowrap; }
.badge {
  padding: 5px 10px; border-radius: var(--r-full); background: var(--accent-soft);
  font-size: 11px; font-weight: 600; color: var(--accent); white-space: nowrap; flex: 0 0 auto;
}

.log { flex: 1 1 auto; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }

.row { display: flex; }
.row.user { justify-content: flex-end; }
.row.agent { justify-content: flex-start; }
.bubble {
  max-width: 78%; padding: 11px 14px; border-radius: var(--r-xl);
  font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
}
.row.user .bubble { background: var(--accent-soft); color: var(--text); }
.row.agent .bubble { background: var(--surface-inset); border: 1px solid var(--line-soft); }
.agent-stack { display: flex; flex-direction: column; gap: 10px; white-space: normal; }
.answer { white-space: normal; }

.files { display: flex; flex-direction: column; gap: 6px; }
.file {
  font-size: 12px; color: var(--accent); text-decoration: none;
  padding: 6px 10px; border: 1px solid var(--line-soft); border-radius: var(--r-lg);
}
.file:hover { background: var(--accent-soft); }

.typing { display: inline-flex; gap: 4px; }
.typing span {
  width: 6px; height: 6px; border-radius: var(--r-full); background: var(--muted);
  animation: typing-bounce 1s infinite ease-in-out;
}
.typing span:nth-child(2) { animation-delay: .15s; }
.typing span:nth-child(3) { animation-delay: .3s; }
@keyframes typing-bounce { 0%, 80%, 100% { opacity: .3; } 40% { opacity: 1; } }

.err {
  border: 1px solid rgba(229, 72, 77, .4); background: rgba(229, 72, 77, .08);
  color: #e5484d; border-radius: var(--r-lg); padding: 10px 12px; font-size: 12px; line-height: 1.55;
}
.empty {
  margin: auto; max-width: 320px; text-align: center; color: var(--muted);
  font-size: 13px; line-height: 1.7; padding: 24px;
}

/* composer */
.composer {
  display: flex; gap: 10px; align-items: flex-end; padding: 16px;
  border-top: 1px solid var(--line-soft); flex: 0 0 auto;
}
.pick {
  width: 40px; height: 40px; border-radius: var(--r-lg); background: var(--surface-2);
  border: 1px solid var(--line); color: var(--muted); font-size: 20px;
  cursor: pointer; display: grid; place-items: center; flex: 0 0 auto;
}
.composer textarea {
  flex: 1 1 auto; min-height: 40px; max-height: 120px; resize: none;
  background: var(--surface-inset); border: 1px solid var(--line); border-radius: var(--r-lg);
  padding: 10px 14px; font: inherit; font-size: 13px; color: var(--text); outline: none;
}
.composer textarea:focus { border-color: var(--accent); }
.send {
  height: 40px; padding: 0 20px; border: 0; border-radius: var(--r-lg);
  background: var(--accent); color: var(--on-accent); font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; flex: 0 0 auto;
}
.send:disabled { opacity: .55; cursor: default; }
</style>
