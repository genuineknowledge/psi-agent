<template>
  <div :class="['msg', msg.role]">
    <div class="role">{{ msg.role === 'user' ? 'You' : 'Assistant' }}</div>
    <div class="bubble-wrap">
      <button v-if="msg.role === 'user'" class="copy-btn" @click="copyMessage" :title="copied ? '已复制' : '复制'">
        <span class="material-symbols-outlined">{{ copied ? 'check' : 'content_copy' }}</span>
      </button>
      <ThinkingBubble v-if="msg.role === 'assistant' && streaming && !msg.text" />
      <div v-else class="bubble">
        <div v-if="msg.text" class="bubble-content" v-html="msg.html"></div>
        <div v-if="msg.stopped" class="stopped-tag">（已停止）</div>
      </div>
      <button v-if="msg.role !== 'user'" class="copy-btn" @click="copyMessage" :title="copied ? '已复制' : '复制'">
        <span class="material-symbols-outlined">{{ copied ? 'check' : 'content_copy' }}</span>
      </button>
    </div>
    <template v-for="(f, i) in msg.files" :key="previewKey(f, i)">
      <button
        class="blob"
        :class="{ active: openPreviewKey === previewKey(f, i) }"
        type="button"
        @click="openPreview(f, i)"
        :aria-label="`预览文件 ${f.name}`"
      >
        <span class="material-symbols-outlined blob-icon">description</span>
        <span class="blob-name">{{ f.name }}</span>
      </button>
    </template>
    <FilePreview
      v-if="openPreviewFile"
      :file="openPreviewFile"
      @close="closePreview"
    />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useClipboard } from '@vueuse/core'
import { useChatStore } from '../stores/chat.js'
import FilePreview from './FilePreview.vue'
import ThinkingBubble from './ThinkingBubble.vue'

const chat = useChatStore()
const { streaming } = storeToRefs(chat)

const props = defineProps({
  msg: {
    type: Object,
    required: true,
    validator: (m) => m && typeof m.role === 'string',
  },
})

const { copy, copied } = useClipboard({ copiedDuring: 1500 })
const openPreviewKey = ref('')
const openPreviewFile = ref(null)
function copyMessage() {
  copy(props.msg.text)
}

function previewKey(f, i) {
  return `${i}:${f.name || ''}`
}

function openPreview(f, i) {
  const key = previewKey(f, i)
  openPreviewKey.value = key
  openPreviewFile.value = f
}

function closePreview() {
  openPreviewKey.value = ''
  openPreviewFile.value = null
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

/* Rendered markdown: block code (<pre>) preserves whitespace without
   wrapping so ASCII diagrams / wide code keep their layout — the box
   scrolls horizontally on narrow windows instead of collapsing. */
.bubble :deep(pre),
.bubble :deep(pre code) {
  white-space: pre;
  overflow-x: auto;
  max-width: 100%;
}

/* Inline code (not inside a <pre>) still wraps so a long token can't
   overflow the bubble. */
.bubble :deep(code) {
  overflow-wrap: break-word;
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

/* 文本容器保持块级：v-html 产出的块级 markdown 嵌进块级元素，避免
   block-in-inline 的无效嵌套。已停止标记作为块级兄弟节点。 */
.bubble-content {
  min-width: 0;
}

/* 固定字体的「已停止」标记：字号/字重/颜色不随回复内容(markdown)变化 */
.stopped-tag {
  display: block;
  margin-top: 4px;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 12px;
  font-weight: 500;
  font-style: normal;
  line-height: 1.6;
  color: var(--md-text-secondary);
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
  color: var(--md-text-primary);
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.2;
  max-width: 100%;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  transition: border-color 0.2s;
}

.blob:hover {
  border-color: var(--md-primary);
}

.blob.active {
  background: var(--md-secondary-container);
  color: var(--md-on-secondary-container);
  border-color: color-mix(in srgb, var(--md-primary) 45%, var(--md-outline-variant));
}

.blob-name {
  color: var(--md-primary);
  text-decoration: none;
  font-weight: 500;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.blob:hover .blob-name {
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
