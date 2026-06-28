<template>
  <div v-if="store.dlgSess" class="dialog-overlay" @click.self="store.dlgSess = false">
    <div class="dialog" style="width:480px">
      <h3>创建新会话</h3>
      <p v-if="currentAiLabel" style="font-size:13px;color:var(--md-text-secondary);margin-bottom:16px">
        大模型: {{ currentAiLabel }}
      </p>
      <div class="field"><label>工作区路径 (可选，默认当前目录)</label>
        <div style="display:flex;gap:8px">
          <input class="md-text-field" v-model="store.sessForm.workspace" placeholder="/path/to/workspace" style="flex:1">
          <button class="md-outlined-btn" style="padding:6px 14px;font-size:12px" @click="toggleBrowser">
            <span class="material-symbols-outlined" style="font-size:16px">folder_open</span>
          </button>
        </div>
      </div>
      <FileBrowser
        v-if="browserVisible"
        @browse="emit('browse', $event)"
        @set-path="store.sessForm.workspace = $event"
      />
      <div class="actions">
        <button class="cancel" @click="store.dlgSess = false">取消</button>
        <button class="ok" @click="emit('create')">创建</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { store } from '../store.js'
import FileBrowser from './FileBrowser.vue'

const emit = defineEmits(['create', 'browse'])

const browserVisible = ref(false)

const currentAiLabel = computed(() => {
  const ai = store.ais.find(a => a.id === store.selectedAiId)
  return ai ? (ai.model || ai.id) : ''
})

function toggleBrowser() {
  browserVisible.value = !browserVisible.value
  if (browserVisible.value) {
    emit('browse', store.sessForm.workspace || '.')
  }
}
</script>

<style scoped>
.md-text-field {
  width: 100%;
  padding: 10px 14px;
  border: 1px solid var(--md-outline);
  border-radius: var(--md-shape-small);
  background: var(--md-surface-variant);
  color: var(--md-text-primary);
  font-size: 14px;
  outline: none;
  box-sizing: border-box;
}

.md-text-field:focus {
  border-color: var(--md-primary);
}

.md-outlined-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: transparent;
  color: var(--md-primary);
  border: 1px solid var(--md-outline);
  border-radius: var(--md-shape-full);
  cursor: pointer;
  transition: background 0.2s;
  flex-shrink: 0;
}

.md-outlined-btn:hover {
  background: rgba(128, 128, 128, var(--md-state-hover));
}
</style>
