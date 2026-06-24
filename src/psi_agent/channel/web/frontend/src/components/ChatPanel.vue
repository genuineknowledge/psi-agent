<script setup>
import { ref, nextTick } from 'vue'
import { renderMarkdown } from '../lib/markdown.js'
import { streamChat } from '../lib/chat.js'

const props = defineProps({
  modules: { type: Object, required: true },
  compare: { type: Boolean, default: false },
  activeCount: { type: Number, default: 0 },
})

// Each message: { role: 'user' } or
//   { role: 'agent', compare, dolphin: {trace, answer}, hermes: {answer}, error, loading }
const messages = ref([])
const input = ref('')
const busy = ref(false)
const logEl = ref(null)

function scrollDown() {
  nextTick(() => {
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  })
}

function modulesPayload() {
  const obj = {}
  for (const key of Object.keys(props.modules)) obj[key] = props.modules[key]
  return obj
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  input.value = ''
  busy.value = true

  messages.value.push({ role: 'user', text })
  scrollDown()

  const compareMode = props.compare
  messages.value.push({
    role: 'agent',
    compare: compareMode,
    dolphin: { trace: '', answer: '' },
    hermes: { answer: '' },
    error: '',
    loading: true,
  })
  // Hold the reactive proxy (not the raw object) so per-frame mutations
  // below actually trigger re-renders. Mutating the pre-push object would
  // bypass Vue's reactivity and the stream would only paint once at the end.
  const agent = messages.value[messages.value.length - 1]
  scrollDown()

  try {
    await streamChat({
      message: text,
      modules: modulesPayload(),
      compare: compareMode,
      onEvent: (evt) => {
        agent.loading = false
        if (evt.error) { agent.error = evt.error; scrollDown(); return }
        // route by channel when comparing; default to dolphin
        const side = evt.channel === 'hermes' ? agent.hermes : agent.dolphin
        if (evt.reasoning && 'trace' in side) side.trace += evt.reasoning
        if (evt.content) side.answer += evt.content
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
        <span class="h-title">{{ compare ? 'Hermes 对话' : 'dolphin-agent 对话' }}</span>
      </div>
      <span class="badge" :class="{ hermes: compare }">{{ compare ? 'Hermes 模式' : activeCount + ' 模块已加载' }}</span>
    </div>

    <!-- conversation (always single-column; comparison is done via OS split-screen) -->
    <div ref="logEl" class="log">
      <div v-if="!messages.length" class="empty">
        发一条消息开始对话。关闭右侧全部模块（或用「模块总开关」）即可切换到 Hermes 原生模式。
      </div>
      <template v-for="(msg, i) in messages" :key="i">
        <div v-if="msg.role === 'user'" class="row user">
          <div class="bubble">{{ msg.text }}</div>
        </div>
        <div v-else class="row agent">
          <div class="bubble agent-stack">
            <div v-if="msg.loading" class="typing"><span></span><span></span><span></span></div>
            <details v-if="msg.dolphin.trace" class="trace" open>
              <summary>思考过程</summary>
              <div class="t-body">{{ msg.dolphin.trace }}</div>
            </details>
            <div v-if="msg.dolphin.answer" class="answer markdown-body" v-html="renderMarkdown(msg.dolphin.answer)"></div>
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
.h-scene { font-size: 12px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.badge {
  padding: 5px 10px; border-radius: var(--r-full); background: var(--accent-soft);
  font-size: 11px; font-weight: 600; color: var(--accent); white-space: nowrap; flex: 0 0 auto;
}

.log { flex: 1 1 auto; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }

.row { display: flex; }
.row.user { justify-content: flex-end; }
.bubble { max-width: min(640px, 80%); border-radius: var(--r-xl); padding: 12px 14px; word-break: break-word; }
.row.user .bubble { background: var(--user-bubble); }
.row.agent .bubble { background: var(--surface-2); }
.agent-stack { display: flex; flex-direction: column; gap: 0; }

.trace {
  border: 1px solid var(--line-soft); background: var(--surface-inset);
  border-radius: var(--r-lg); overflow: hidden; margin-bottom: 10px;
}
.trace summary {
  list-style: none; cursor: pointer; display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; font-size: 11px; font-weight: 600; color: var(--soft);
  border-bottom: 1px solid var(--line-soft);
}
.trace summary::-webkit-details-marker { display: none; }
.trace summary::before { content: ""; width: 6px; height: 6px; border-radius: var(--r-full); background: var(--accent-2); }
.t-body { padding: 10px 12px; font: 11px/1.6 "Geist Mono", ui-monospace, Consolas, monospace; color: var(--muted); white-space: pre-wrap; }

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


