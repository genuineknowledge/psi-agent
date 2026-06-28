<template>
  <div class="md-snackbar" :class="{ active: store.snackbar.show }">
    <div class="sb-msg">{{ store.snackbar.message }}</div>
    <button class="sb-btn" @click="store.snackbar.show = false">知道了</button>
  </div>
</template>

<script setup>
import { store } from '../store.js'
</script>

<style scoped>
.md-snackbar {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%) translateY(40px);
  background: var(--md-surface-variant);
  border: 1px solid var(--md-outline);
  color: var(--md-text-primary);
  padding: 14px 24px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  z-index: 1000;
  opacity: 0;
  pointer-events: none;
  transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.2s ease;
}

.md-snackbar.active {
  transform: translateX(-50%) translateY(0);
  opacity: 1;
  pointer-events: auto;
}

.sb-msg {
  font-size: 14px;
  flex: 1;
}

.sb-btn {
  background: none;
  border: none;
  color: var(--md-primary);
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 8px;
}

.sb-btn:hover {
  background: rgba(255, 255, 255, 0.05);
}

@media (max-width: 768px) {
  .md-snackbar {
    bottom: calc(16px + env(safe-area-inset-bottom));
    width: 90vw;
    max-width: 360px;
  }
}
</style>
