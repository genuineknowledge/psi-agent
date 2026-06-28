<template>
  <div v-if="store.dlgConfirm.show" class="dialog-overlay" @click.self="store.dlgConfirm.show = false">
    <div class="dialog">
      <h3>确认删除</h3>
      <p class="alert-desc">{{ store.dlgConfirm.message }}</p>
      <div class="actions">
        <button class="cancel" @click="store.dlgConfirm.show = false">取消</button>
        <button class="ok" style="background: var(--md-text-error); color: #1a0002;" @click="$emit('confirm')">删除</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { store } from '../store.js'

defineEmits(['confirm'])
</script>

<style scoped>
.dialog-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  backdrop-filter: blur(2px);
}

.dialog {
  background: var(--md-surface-container-high);
  border: 1px solid var(--md-outline-variant);
  border-radius: 28px;
  padding: 24px;
  width: 400px;
  max-width: 90vw;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
}

.dialog h3 {
  font-size: 20px;
  font-weight: 400;
  margin-bottom: 20px;
  color: var(--md-text-primary);
}

.alert-desc {
  font-size: 14px;
  color: var(--md-text-secondary);
  line-height: 1.5;
  margin-bottom: 12px;
}

.actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 24px;
}

.actions button {
  padding: 10px 24px;
  border-radius: var(--md-shape-full);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border: none;
  transition: all 0.2s;
}

.actions button.ok {
  background: var(--md-primary);
  color: var(--md-on-primary);
  box-shadow: var(--md-elevation-1);
}

.actions button.ok:hover {
  box-shadow: var(--md-elevation-2);
  filter: brightness(1.08);
}

.actions button.cancel {
  background: transparent;
  color: var(--md-primary);
  border: 1px solid var(--md-outline);
}

.actions button.cancel:hover {
  background: rgba(128, 128, 128, var(--md-state-hover));
}
</style>
