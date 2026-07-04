<template>
  <div class="thinking-bubble">
    <div class="thinking-dot"></div>
    <div class="thinking-dot"></div>
    <div class="thinking-dot"></div>
  </div>
</template>

<script setup>
</script>

<style scoped>
.thinking-bubble {
  background: var(--md-surface-container-high);
  border: 1px solid var(--md-outline-variant);
  border-radius: 16px 16px 16px 4px;
  padding: 14px 18px;
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 72px;
}

.thinking-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--g-spark);
  animation: g-pulse 1.4s ease-in-out infinite;
}

.thinking-dot:nth-child(2) {
  animation-delay: 0.2s;
}

.thinking-dot:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes g-pulse {
  0%, 100% { opacity: 0.5; transform: scale(0.9); }
  50%       { opacity: 1;   transform: scale(1);   }
}
</style>
