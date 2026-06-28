<template>
  <div v-if="store.browser.path !== undefined" class="browser-container">
    <div class="md-list-item" @click="$emit('set-path', store.browser.path)">
      <span class="material-symbols-outlined folder-icon">folder</span>
      <span class="item-label">使用当前目录</span>
    </div>
    <div class="md-list-item" @click="$emit('browse', store.browser.parent)">
      <span class="material-symbols-outlined parent-icon">arrow_upward</span>
      <span class="item-label">.. 上级目录</span>
    </div>
    <div
      v-for="e in store.browser.entries"
      :key="e.path"
      class="md-list-item"
      @click="enterDir(e.path)"
    >
      <span class="material-symbols-outlined entry-folder-icon">folder</span>
      <span class="item-label entry-name">{{ e.name }}</span>
    </div>
    <div v-if="store.browser.entries.length === 0" class="browser-empty">
      此目录下没有子文件夹
    </div>
  </div>
</template>

<script setup>
import { store } from '../store.js'

const emit = defineEmits(['browse', 'set-path'])

function enterDir(path) {
  store.sessForm.workspace = path
  emit('browse', path)
}
</script>

<style scoped>
.browser-container {
  background: var(--md-surface-variant);
  border-radius: var(--md-shape-medium);
  padding: 4px;
  max-height: 200px;
  overflow-y: auto;
  margin-bottom: 12px;
}

.md-list-item {
  padding: 8px 12px;
  border-radius: var(--md-shape-small);
  cursor: pointer;
  font-size: 13px;
  margin: 2px;
  display: flex;
  align-items: center;
  transition: background 0.2s;
}

.md-list-item:hover {
  background: rgba(128, 128, 128, var(--md-state-hover));
}

.folder-icon {
  font-size: 18px;
  color: var(--md-primary);
  flex-shrink: 0;
}

.entry-folder-icon {
  font-size: 18px;
  color: var(--md-outline-variant);
  flex-shrink: 0;
}

.parent-icon {
  font-size: 18px;
  color: var(--md-text-secondary);
  flex-shrink: 0;
}

.item-label {
  flex: 1;
  margin-left: 10px;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.md-list-item:first-child .item-label {
  color: var(--md-primary);
  font-weight: 500;
}

.browser-empty {
  padding: 12px;
  text-align: center;
  color: var(--md-text-secondary);
  font-size: 13px;
}
</style>
