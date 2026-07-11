<template>
  <div :class="['msg', msg.role]">
    <div class="msg-row">
      <div class="msg-avatar" :class="msg.role" :aria-label="speakerLabel">
        <span v-if="msg.role === 'user' && userInitial" class="avatar-text">{{ userInitial }}</span>
        <span v-else-if="msg.role === 'user'" class="material-symbols-outlined">person</span>
        <span v-else class="material-symbols-outlined">smart_toy</span>
      </div>
      <div class="msg-content">
        <div class="msg-speaker">{{ speakerLabel }}</div>
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
      </div>
    </div>
    <FilePreview
      v-if="openPreviewFile"
      :file="openPreviewFile"
      @close="closePreview"
    />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useClipboard, useStorage } from '@vueuse/core'
import { useChatStore } from '../stores/chat.js'
import FilePreview from './FilePreview.vue'
import ThinkingBubble from './ThinkingBubble.vue'

const LS_USER_NAME = 'gw-user-name'

const chat = useChatStore()
const { streaming } = storeToRefs(chat)
const userName = useStorage(LS_USER_NAME, '')

const props = defineProps({
  msg: {
    type: Object,
    required: true,
    validator: (m) => m && typeof m.role === 'string',
  },
})

const userInitial = computed(() =>
  userName.value ? userName.value.trim().charAt(0).toUpperCase() : ''
)

const speakerLabel = computed(() => {
  if (props.msg.role === 'user') {
    return userName.value.trim() || '您'
  }
  return 'HaiTun'
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
  max-width: 820px;
  width: 100%;
  margin: 0 auto 24px;
}

.msg-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.msg.user .msg-row {
  flex-direction: row-reverse;
}

.msg-avatar {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: var(--md-shape-full);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 22px;
}

.msg-avatar.user {
  background: var(--md-primary);
  color: var(--md-on-primary);
}

.msg-avatar.assistant {
  background: var(--md-secondary-container);
  color: var(--md-on-secondary-container);
}

.avatar-text {
  font-size: 15px;
  font-weight: 600;
  line-height: 1;
}

.msg-avatar .material-symbols-outlined {
  font-size: 20px;
}

.msg-content {
  flex: 1;
  min-width: 0;
  max-width: calc(100% - 46px);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.msg.user .msg-content {
  align-items: flex-end;
}

.msg.assistant .msg-content {
  align-items: flex-start;
}

.msg-speaker {
  font-size: 12px;
  font-weight: 600;
  line-height: 1.2;
  color: var(--md-text-secondary);
  padding: 0 4px;
}

.bubble-wrap {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 4px;
  max-width: 100%;
}

.bubble {
  flex: 1;
  min-width: 0;
  padding: 12px 16px;
  font-size: 16px;
  line-height: 1.7;
  word-break: break-word;
  max-width: 100%;
}

.bubble :deep(p),
.bubble :deep(li),
.bubble :deep(h1),
.bubble :deep(h2),
.bubble :deep(h3),
.bubble :deep(h4),
.bubble :deep(th),
.bubble :deep(td) {
  font-family: inherit;
}

.bubble :deep(code),
.bubble :deep(pre) {
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, "Courier New", monospace;
}

.bubble :deep(:not(pre) > code) {
  background: var(--md-surface-container-high);
  border-radius: 6px;
  padding: 0.12em 0.4em;
  font-size: 0.9em;
  overflow-wrap: break-word;
}

.bubble :deep(pre) {
  background: var(--md-surface-container-low);
  border: 1px solid var(--md-outline-variant);
  border-radius: 10px;
  padding: 12px 14px;
  margin: 8px 0;
  overflow-x: auto;
  max-width: 100%;
}

.bubble :deep(pre code) {
  background: transparent;
  border: none;
  padding: 0;
  font-size: 0.875em;
  line-height: 1.6;
  white-space: pre;
}

.msg.user .bubble {
  background: var(--md-primary-container);
  color: var(--md-on-primary-container);
  border-radius: 16px 16px 4px 16px;
  padding: 12px 16px;
  white-space: pre-wrap;
}

.msg.assistant .bubble {
  background: var(--md-surface-container-high);
  color: var(--md-text-primary);
  border: 1px solid var(--md-outline-variant);
  border-radius: 16px 16px 16px 4px;
  padding: 12px 16px;
}

.bubble-content {
  min-width: 0;
}

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

@media (max-width: 768px) {
  .msg {
    max-width: 100%;
  }

  .msg-content {
    max-width: calc(100% - 42px);
  }
}
</style>
