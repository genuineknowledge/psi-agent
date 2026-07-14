<template>
  <div :class="['msg', msg.role]">
    <div class="msg-row">
      <div class="msg-avatar" :class="msg.role" :aria-label="speakerLabel">
        <img v-if="msg.role === 'user' && userAvatar" class="avatar-img" :src="userAvatar" alt="">
        <span v-else-if="msg.role === 'user' && userInitial" class="avatar-text">{{ userInitial }}</span>
        <span v-else-if="msg.role === 'user'" class="material-symbols-outlined">person</span>
        <span v-else class="avatar-logo" aria-hidden="true"></span>
      </div>
      <div class="msg-content">
        <div class="msg-speaker">{{ speakerLabel }}</div>
        <div class="bubble-wrap">
          <div v-if="msg.role === 'user'" class="side-actions">
            <button class="copy-btn" @click="copyMessage" :title="copied ? '已复制' : '复制'">
              <span class="material-symbols-outlined">{{ copied ? 'check' : 'content_copy' }}</span>
            </button>
            <button
              v-if="msg.failed"
              type="button"
              class="retry-btn"
              aria-label="重新发送"
              :title="failedLabel"
              :disabled="streaming"
              @click="retryMessage"
            >
              <span class="material-symbols-outlined" aria-hidden="true">replay</span>
            </button>
          </div>
          <ThinkingBubble v-if="isStreamingTarget && !hasVisibleContent" />
          <div v-else class="bubble">
            <div v-if="msg.text" class="bubble-content" v-html="msg.html"></div>
          </div>
        </div>
        <div
          v-if="showActions"
          class="msg-actions"
          role="toolbar"
          aria-label="消息操作"
        >
          <button
            type="button"
            class="action-btn"
            :class="{ active: msg.feedback === 'up' }"
            :title="msg.feedback === 'up' ? '取消点赞' : '点赞'"
            :aria-pressed="msg.feedback === 'up'"
            @click="setFeedback('up')"
          >
            <span class="material-symbols-outlined" aria-hidden="true">thumb_up</span>
          </button>
          <button
            type="button"
            class="action-btn"
            :class="{ active: msg.feedback === 'down' }"
            :title="msg.feedback === 'down' ? '取消点踩' : '点踩'"
            :aria-pressed="msg.feedback === 'down'"
            @click="setFeedback('down')"
          >
            <span class="material-symbols-outlined" aria-hidden="true">thumb_down</span>
          </button>
          <button
            type="button"
            class="action-btn"
            title="重新生成"
            aria-label="重新生成"
            :disabled="streaming"
            @click="regenerateMessage"
          >
            <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
          </button>
          <button
            type="button"
            class="action-btn"
            :title="copied ? '已复制' : '复制'"
            aria-label="复制"
            @click="copyMessage"
          >
            <span class="material-symbols-outlined" aria-hidden="true">{{ copied ? 'check' : 'content_copy' }}</span>
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
import { resendFailedMessage, regenerateAssistantMessage } from '../composables/useChat.js'
import FilePreview from './FilePreview.vue'
import ThinkingBubble from './ThinkingBubble.vue'
import { FAILED_REASON_LABEL } from '../messageTurn.js'
import { saveHistory } from '../utils.js'
import { useSessionStore } from '../stores/session.js'

import { LS_USER_AVATAR, LS_USER_NAME } from '../userProfile.js'

const chat = useChatStore()
const { streaming } = storeToRefs(chat)
const userName = useStorage(LS_USER_NAME, '')
const userAvatar = useStorage(LS_USER_AVATAR, '')

const props = defineProps({
  msg: {
    type: Object,
    required: true,
    validator: (m) => m && typeof m.role === 'string',
  },
  showActions: {
    type: Boolean,
    default: false,
  },
  isStreamingTarget: {
    type: Boolean,
    default: false,
  },
})

const hasVisibleContent = computed(() => {
  const text = typeof props.msg.text === 'string' ? props.msg.text.trim() : ''
  const hasFiles = Array.isArray(props.msg.files) && props.msg.files.length > 0
  return !!text || hasFiles
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

const failedLabel = computed(() => {
  if (!props.msg.failed) return ''
  const key = props.msg.failedReason
  return FAILED_REASON_LABEL[key] || FAILED_REASON_LABEL.incomplete
})

const { copy, copied } = useClipboard({ copiedDuring: 1500 })
const openPreviewKey = ref('')
const openPreviewFile = ref(null)

function copyMessage() {
  copy(props.msg.text)
}

function setFeedback(kind) {
  props.msg.feedback = props.msg.feedback === kind ? '' : kind
  const session = useSessionStore()
  const sid = session.selectedSessionId || session.draftSession?.draftId
  if (!sid) return
  const msgs = session.sessionMessages[sid] ?? chat.messages
  saveHistory(sid, msgs)
}

async function regenerateMessage() {
  if (streaming.value) return
  await regenerateAssistantMessage(props.msg)
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

async function retryMessage() {
  if (!props.msg.failed || streaming.value) return
  await resendFailedMessage(props.msg)
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
  overflow: hidden;
}

.avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.msg-avatar.assistant {
  background: var(--md-surface-container);
  overflow: hidden;
  padding: 0;
}

.avatar-logo {
  width: 100%;
  height: 100%;
  border-radius: inherit;
  background-image: url('/spa/haitun-logo.png');
  background-size: cover;
  background-position: center;
  display: block;
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

.bubble-content {
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
}

/* GFM tables: fit bubble width; wrap cell text instead of horizontal scroll. */
.bubble :deep(table) {
  border-collapse: collapse;
  margin: 10px 0;
  width: 100%;
  max-width: 100%;
  table-layout: fixed;
  font-size: 0.875em;
  border: 2px solid var(--md-outline);
}

.bubble :deep(th),
.bubble :deep(td) {
  border: 1.5px solid var(--md-outline);
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
  min-width: 0;
}

.bubble :deep(th) {
  background: var(--md-surface-container-highest, var(--md-surface-container-high));
  font-weight: 700;
  color: var(--md-text-primary);
}

.msg.user .bubble :deep(th) {
  background: color-mix(in srgb, var(--md-on-primary-container) 8%, var(--md-primary-container));
}

.msg.user .bubble {
  background: var(--md-primary-container);
  color: var(--md-on-primary-container);
  border-radius: 16px 16px 4px 16px;
  padding: 12px 16px;
  white-space: pre-wrap;
}

.side-actions {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  width: 32px;
  margin-top: 2px;
}

.retry-btn {
  width: 32px;
  height: 32px;
  border-radius: var(--md-shape-full);
  border: 1.5px solid var(--md-text-error);
  background: color-mix(in srgb, var(--md-text-error) 10%, transparent);
  color: var(--md-text-error);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  flex-shrink: 0;
  transition: background 0.15s, filter 0.15s;
}

.retry-btn:hover:not(:disabled) {
  background: color-mix(in srgb, var(--md-text-error) 18%, transparent);
  filter: brightness(1.05);
}

.retry-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.retry-btn .material-symbols-outlined {
  font-size: 18px;
}

.msg.assistant .bubble {
  background: var(--md-surface-container-high);
  color: var(--md-text-primary);
  border: 1px solid var(--md-outline-variant);
  border-radius: 16px 16px 16px 4px;
  padding: 12px 16px;
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
.copy-btn:focus-visible,
.side-actions:has(.retry-btn) .copy-btn {
  opacity: 1;
}

.msg-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px 0 0 4px;
}

.action-btn {
  background: none;
  border: none;
  border-radius: var(--md-shape-full);
  width: 32px;
  height: 32px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--md-text-secondary);
  padding: 0;
  transition: background 0.15s, color 0.15s;
}

.action-btn:hover:not(:disabled) {
  background: rgba(128, 128, 128, var(--md-state-hover));
  color: var(--md-text-primary);
}

.action-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.action-btn.active {
  color: var(--md-primary);
}

.action-btn .material-symbols-outlined {
  font-size: 18px;
}

.action-btn.active .material-symbols-outlined {
  font-variation-settings: 'FILL' 1;
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
