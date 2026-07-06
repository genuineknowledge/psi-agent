<template>
  <div v-if="show" class="dialog-overlay" @click.self="$emit('close')">
    <div class="dialog" :style="{ width }">
      <h3 v-if="$slots.title"><slot name="title" /></h3>
      <slot />
      <div v-if="$slots.actions" class="actions">
        <slot name="actions" />
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  show: { type: Boolean, default: false },
  width: { type: String, default: '400px' },
})
defineEmits(['close'])
</script>

<style scoped>
.dialog-overlay {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex; align-items: center; justify-content: center;
  z-index: 100; backdrop-filter: blur(2px);
}
.dialog {
  background: var(--md-surface-container-high);
  border: 1px solid var(--md-outline-variant);
  border-radius: 28px; padding: 24px; max-width: 90vw;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
}
.dialog h3 {
  font-size: 20px; font-weight: 400; margin-bottom: 20px; color: var(--md-text-primary);
}
.actions {
  display: flex; gap: 8px; justify-content: flex-end; margin-top: 24px;
}
.actions :deep(button) {
  padding: 10px 24px; border-radius: var(--md-shape-full);
  font-size: 14px; font-weight: 500; cursor: pointer; border: none; transition: all 0.2s;
}
.actions :deep(button.ok) {
  background: var(--md-primary); color: var(--md-on-primary); box-shadow: var(--md-elevation-1);
}
.actions :deep(button.ok:hover) { box-shadow: var(--md-elevation-2); filter: brightness(1.08); }
.actions :deep(button.cancel) {
  background: transparent; color: var(--md-primary); border: 1px solid var(--md-outline);
}
.actions :deep(button.cancel:hover) { background: rgba(128, 128, 128, var(--md-state-hover)); }

@media (max-width: 768px) {
  .dialog { width: 94vw !important; border-radius: 20px; padding: 20px; }
  .dialog h3 { font-size: 18px; }
}
</style>
