<template>
  <div class="reasoning-panel" :class="{ open: isOpen, streaming: streaming }">
    <button
      type="button"
      class="reasoning-header"
      :aria-expanded="isOpen"
      @click="toggle"
    >
      <span class="reasoning-dots" aria-hidden="true">
        <span class="thinking-dot"></span>
        <span class="thinking-dot"></span>
        <span class="thinking-dot"></span>
      </span>
      <span class="reasoning-status">{{ status }}</span>
      <span
        v-if="body"
        class="material-symbols-outlined reasoning-chevron"
        aria-hidden="true"
      >
        {{ isOpen ? 'expand_less' : 'expand_more' }}
      </span>
    </button>
    <div v-if="isOpen && body" class="reasoning-body">
      <pre class="reasoning-text">{{ body }}</pre>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { reasoningStatusLabel } from '../reasoningStatus.js'

const props = defineProps({
  /** Accumulated Session reasoning text (thinking + tool markers). */
  text: {
    type: String,
    default: '',
  },
  /** Controlled open state; parent owns the flag on the message object. */
  open: {
    type: Boolean,
    default: false,
  },
  /** True while this assistant row is the live streaming target. */
  streaming: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['update:open'])

const body = computed(() => (typeof props.text === 'string' ? props.text : ''))
const status = computed(() => reasoningStatusLabel(body.value))
const isOpen = computed(() => !!props.open)

function toggle() {
  if (!body.value.trim()) return
  emit('update:open', !isOpen.value)
}
</script>

<style scoped>
.reasoning-panel {
  background: var(--md-surface-container-high);
  border: 1px solid var(--md-outline-variant);
  border-radius: 16px 16px 16px 4px;
  min-width: 72px;
  max-width: min(100%, 640px);
  overflow: hidden;
}

.reasoning-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  margin: 0;
  padding: 10px 14px;
  border: 0;
  background: transparent;
  color: var(--md-on-surface-variant);
  font: inherit;
  font-size: 0.875rem;
  line-height: 1.35;
  text-align: left;
  cursor: pointer;
}

.reasoning-header:hover {
  background: color-mix(in srgb, var(--md-on-surface) 4%, transparent);
}

.reasoning-dots {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.thinking-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--g-spark);
  opacity: 0.55;
}

.reasoning-panel.streaming .thinking-dot {
  animation: g-pulse 1.4s ease-in-out infinite;
}

.reasoning-panel.streaming .thinking-dot:nth-child(2) {
  animation-delay: 0.2s;
}

.reasoning-panel.streaming .thinking-dot:nth-child(3) {
  animation-delay: 0.4s;
}

.reasoning-status {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reasoning-chevron {
  font-size: 18px;
  opacity: 0.7;
  flex-shrink: 0;
}

.reasoning-body {
  border-top: 1px solid var(--md-outline-variant);
  padding: 10px 14px 12px;
  max-height: 220px;
  overflow: auto;
}

.reasoning-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  line-height: 1.45;
  color: var(--md-on-surface-variant);
}

@keyframes g-pulse {
  0%, 100% { opacity: 0.45; transform: scale(0.9); }
  50%       { opacity: 1;   transform: scale(1);   }
}
</style>
