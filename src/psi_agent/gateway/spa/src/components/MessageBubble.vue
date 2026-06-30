<template>
  <div :class="['msg', msg.role]">
    <div class="role">{{ msg.role === 'user' ? 'You' : 'Assistant' }}</div>
    <div class="bubble-wrap">
      <button v-if="msg.role === 'user'" class="copy-btn" @click="copyMessage" :title="copied ? '已复制' : '复制'">
        <span class="material-symbols-outlined">{{ copied ? 'check' : 'content_copy' }}</span>
      </button>
      <ThinkingBubble v-if="msg.role === 'assistant' && store.streaming && !msg.text" />
      <div v-else class="bubble">
        <span v-if="msg.text" v-html="msg.html"></span>
        <span v-if="msg.stopped" class="stopped-tag">（已停止）</span>
      </div>
      <button v-if="msg.role !== 'user'" class="copy-btn" @click="copyMessage" :title="copied ? '已复制' : '复制'">
        <span class="material-symbols-outlined">{{ copied ? 'check' : 'content_copy' }}</span>
      </button>
    </div>
    <template v-for="f in msg.files" :key="f.name">
      <div class="blob">
        <span class="material-symbols-outlined blob-icon">description</span>
        <a :href="fileUrl(f)" :download="f.name" target="_blank">{{ f.name }}</a>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { store } from '../store.js'
import ThinkingBubble from './ThinkingBubble.vue'

const props = defineProps({
  msg: {
    type: Object,
    required: true,
    validator: (m) => m && typeof m.role === 'string',
  },
})

const copied = ref(false)

async function copyMessage() {
  await navigator.clipboard.writeText(props.msg.text)
  copied.value = true
  setTimeout(() => {
    copied.value = false
  }, 1500)
}

function mimeType(name) {
  const ext = (name || '').split('.').pop().toLowerCase()
  const map = {
    png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
    gif: 'image/gif', webp: 'image/webp', svg: 'image/svg+xml',
    pdf: 'application/pdf', txt: 'text/plain', json: 'application/json',
    html: 'text/html', css: 'text/css', js: 'text/javascript',
    py: 'text/x-python', zip: 'application/zip', gz: 'application/gzip',
    tar: 'application/x-tar', mp3: 'audio/mpeg', wav: 'audio/wav',
    mp4: 'video/mp4', mov: 'video/quicktime',
  }
  return map[ext] || 'application/octet-stream'
}

function fileUrl(f) {
  if (f._url) return f._url
  const bin = Uint8Array.from(atob(f.data), (c) => c.charCodeAt(0))
  const mime = mimeType(f.name)
  const blob = new Blob([bin], { type: mime })
  f._url = URL.createObjectURL(blob)
  return f._url
}
</script>

<style scoped>
.msg {
  margin-bottom: 20px;
  max-width: 75%;
  width: max-content;
  min-width: 50px;
  display: flex;
  flex-direction: column;
}

.msg.user {
  margin-left: auto;
  align-items: flex-end;
}

.msg.assistant {
  margin-right: auto;
  align-items: flex-start;
}

.role {
  font-size: 11px;
  font-weight: 500;
  color: var(--md-text-secondary);
  margin-bottom: 4px;
  padding: 0 6px;
}

.bubble-wrap {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 4px;
}

.bubble {
  flex: 1;
  padding: 12px 16px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
  max-width: 100%;
}

/* Rendered markdown: collapse inter-tag whitespace (the bubble used to be
   pre-wrap, which surfaced marked's HTML newlines as blank lines). Code
   blocks still preserve their own whitespace via <pre>/<code>. */
.bubble :deep(pre),
.bubble :deep(code) {
  white-space: pre-wrap;
}

.msg.user .bubble {
  background: var(--md-primary-container);
  color: var(--md-on-primary-container);
  border-radius: 16px 16px 4px 16px;
  /* User text is html-escaped (not markdown), so keep literal newlines. */
  white-space: pre-wrap;
}

.msg.assistant .bubble {
  background: var(--md-surface-container-high);
  color: var(--md-text-primary);
  border: 1px solid var(--md-outline-variant);
  border-radius: 16px 16px 16px 4px;
}

/* 固定字体的「已停止」标记：字号/字重/颜色不随回复内容(markdown)变化 */
.stopped-tag {
  display: inline-block;
  margin-left: 6px;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 12px;
  font-weight: 500;
  font-style: normal;
  line-height: 1.6;
  color: var(--md-text-secondary);
  white-space: nowrap;
  vertical-align: baseline;
}

.copy-btn {
  background: none;
  border: none;
  border-radius: var(--md-shape-full);
  width: 32px;
  height: 32px;
  cursor: pointer;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--md-text-secondary);
  opacity: 0;
  transition: opacity 0.15s, background 0.15s;
  margin-top: 2px;
}

.bubble-wrap:hover .copy-btn,
.copy-btn:focus-visible {
  opacity: 1;
}

.copy-btn:hover {
  background: rgba(128, 128, 128, var(--md-state-hover));
}

.copy-btn .material-symbols-outlined {
  font-size: 16px;
}

@media (hover: none) {
  .copy-btn {
    opacity: 1;
  }
}

.blob {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  padding: 8px 14px;
  background: var(--md-surface-container-high);
  border: 1px solid var(--md-outline-variant);
  border-radius: 12px;
  font-size: 13px;
  max-width: 100%;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  transition: border-color 0.2s;
}

.blob:hover {
  border-color: var(--md-primary);
}

.blob a {
  color: var(--md-primary);
  text-decoration: none;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.blob a:hover {
  text-decoration: underline;
}

.blob-icon {
  font-size: 18px;
  color: var(--md-primary);
}

.msg.user .blob {
  align-self: flex-end;
}

@media (max-width: 768px) {
  .msg {
    max-width: 90%;
  }
}

@media (max-width: 400px) {
  .msg {
    max-width: 95%;
  }
}
</style>
