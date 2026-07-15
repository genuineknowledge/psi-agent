<template>
  <span
    v-if="streaming || marked"
    class="session-stream-indicator"
    :aria-label="streaming ? '正在回复' : '回复已完成'"
    role="status"
  >
    <span v-if="streaming" class="status-spinner" aria-hidden="true"></span>
    <span v-else class="status-dot" aria-hidden="true"></span>
  </span>
</template>

<script setup>
defineProps({
  streaming: {
    type: Boolean,
    default: false,
  },
  marked: {
    type: Boolean,
    default: false,
  },
})
</script>

<style scoped>
.session-stream-indicator {
  flex-shrink: 0;
  width: 10px;
  height: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--md-primary);
}

.status-spinner {
  width: 8px;
  height: 8px;
  box-sizing: border-box;
  border: 2px solid var(--md-primary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: session-status-spin 0.75s linear infinite;
}

@keyframes session-status-spin {
  to { transform: rotate(360deg); }
}
</style>
