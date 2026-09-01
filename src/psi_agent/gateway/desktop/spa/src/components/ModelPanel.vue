<template>
  <div class="model-zone">
    <div v-if="modelPanelOpen" class="model-panel-backdrop" @click="modelPanelOpen = false"></div>

    <div class="model-chip" :class="{ open: modelPanelOpen }" @click="modelPanelOpen = !modelPanelOpen" :title="currentModelLabel">
      <span class="material-symbols-outlined chip-icon">smart_toy</span>
      <span class="chip-label">{{ currentModelLabel }}</span>
      <span class="material-symbols-outlined chip-arrow">expand_more</span>
    </div>

    <div v-if="modelPanelOpen" class="model-panel">
      <div class="model-panel-header">
        <span>切换模型</span>
      </div>
      <div class="model-panel-list">
        <div v-if="ais.length === 0 && routers.length === 0" class="model-panel-empty">
          暂无模型，请到右上角 → 大模型 连接
        </div>
        <div v-for="a in ais" :key="a.id"
             class="model-panel-item"
             :class="{ active: selectedBackendType === 'ai' && a.id === selectedBackendId }"
             @click="selectBackend('ai', a.id)">
          <span class="material-symbols-outlined mpi-icon">smart_toy</span>
          <div class="mpi-info">
            <div class="mpi-name" :title="a.model || a.id">{{ a.model || a.id }}</div>
            <div class="mpi-provider">{{ a.provider }}</div>
          </div>
          <span v-if="selectedBackendType === 'ai' && a.id === selectedBackendId" class="material-symbols-outlined mpi-check">check_circle</span>
          <button class="mpi-del" @click.stop="requestDelete(a.id)" title="删除此模型">
            <span class="material-symbols-outlined">delete</span>
          </button>
        </div>
        <div v-if="routers.length" class="model-group-label">智能路由</div>
        <div v-for="r in routers" :key="`router:${r.id}`" class="model-panel-item"
             :class="{ active: selectedBackendType === 'router' && r.id === selectedBackendId }"
             @click="selectBackend('router', r.id)">
          <span class="material-symbols-outlined mpi-icon">route</span>
          <div class="mpi-info"><div class="mpi-name">{{ r.name || r.id }}</div><div class="mpi-provider">{{ r.upstreams.length }} 个候选模型</div></div>
          <span v-if="selectedBackendType === 'router' && r.id === selectedBackendId" class="material-symbols-outlined mpi-check">check_circle</span>
          <button class="mpi-del" @click.stop="requestDeleteRouter(r.id)" title="停止此路由"><span class="material-symbols-outlined">delete</span></button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useAiStore } from '../stores/ai.js'
import { useRouterStore } from '../stores/router.js'
import { useSessionStore } from '../stores/session.js'
import { getBackendLabel } from '../backendOptions.js'

const ai = useAiStore()
const { modelPanelOpen, ais } = storeToRefs(ai)
const { routers } = storeToRefs(useRouterStore())
const { selectedBackendType, selectedBackendId } = storeToRefs(useSessionStore())

const emit = defineEmits(['select-backend', 'delete-ai', 'delete-router'])

const currentModelLabel = computed(() => {
  return getBackendLabel(selectedBackendType.value, selectedBackendId.value, ais.value, routers.value)
})

function selectBackend(type, id) {
  emit('select-backend', { type, id })
  modelPanelOpen.value = false
}
function requestDeleteRouter(id) { modelPanelOpen.value = false; emit('delete-router', id) }

function requestDelete(id) {
  modelPanelOpen.value = false
  emit('delete-ai', id)
}
</script>

<style scoped>
.model-zone {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  position: relative;
}
.model-chip {
  display: flex; align-items: center; gap: 6px;
  height: 36px; padding: 0 10px;
  background: transparent; border: none;
  border-radius: var(--md-shape-full); cursor: pointer;
  transition: all 0.2s; max-width: 160px;
  color: var(--md-text-primary); font-size: 13px; font-weight: 500;
  white-space: nowrap; overflow: hidden;
}
.model-chip:hover { background: var(--md-surface-container-high); }
.model-chip.open {
  background: var(--md-secondary-container);
  color: var(--md-on-secondary-container);
}
.model-chip .chip-icon { font-size: 16px; flex-shrink: 0; opacity: 0.75; }
.model-chip .chip-label { overflow: hidden; text-overflow: ellipsis; flex: 1; }
.model-chip .chip-arrow { font-size: 16px; flex-shrink: 0; opacity: 0.6; transition: transform 0.2s; }
.model-chip.open .chip-arrow { transform: rotate(180deg); }
.model-panel {
  position: absolute;
  bottom: calc(100% + 8px);
  right: 0;
  width: 300px;
  background: var(--md-surface-container-high);
  border: 1px solid var(--md-outline-variant);
  border-radius: 16px;
  box-shadow: 0 8px 28px rgba(0,0,0,0.25);
  z-index: 50;
  overflow: hidden;
}
.model-panel-header {
  display: flex;
  align-items: center;
  padding: 14px 16px 10px;
  border-bottom: 1px solid var(--md-outline-variant);
}
.model-panel-header span {
  font-size: 13px; font-weight: 600; color: var(--md-text-secondary);
  letter-spacing: 0.5px; text-transform: uppercase;
}
.model-panel-list { padding: 6px 8px; max-height: 220px; overflow-y: auto; }
.model-panel-item {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 10px; border-radius: 10px; cursor: pointer;
  transition: background 0.15s;
}
.model-panel-item:hover { background: var(--md-surface-variant); }
.model-panel-item.active {
  background: var(--md-secondary-container); color: var(--md-on-secondary-container);
}
.model-panel-item .mpi-icon { font-size: 16px; flex-shrink: 0; opacity: 0.7; }
.model-panel-item .mpi-info { flex: 1; overflow: hidden; }
.model-panel-item .mpi-name {
  font-size: 13px; font-weight: 500;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.model-panel-item .mpi-provider { font-size: 11px; color: var(--md-text-secondary); margin-top: 1px; }
.model-panel-item.active .mpi-provider { opacity: 0.75; }
.model-panel-item .mpi-check { font-size: 18px; color: var(--md-primary); flex-shrink: 0; }
.model-panel-item .mpi-del {
  background: none; border: none; cursor: pointer;
  padding: 4px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: var(--md-text-secondary);
  opacity: 0; transition: opacity 0.15s, color 0.15s, background 0.15s;
  flex-shrink: 0;
}
.model-panel-item:hover .mpi-del { opacity: 1; }
.model-panel-item .mpi-del:hover { background: rgba(255,80,80,0.12); color: var(--md-text-error); }
.model-panel-item .mpi-del .material-symbols-outlined { font-size: 16px; }
.model-panel-empty { padding: 16px; text-align: center; font-size: 13px; color: var(--md-text-secondary); }
.model-group-label { padding: 8px 10px 4px; font-size: 11px; color: var(--md-text-secondary); text-transform: uppercase; }
.model-panel-backdrop { position: fixed; inset: 0; z-index: 49; }

@media (hover: none) {
  .model-panel-item .mpi-del { opacity: 1; }
}

@media (max-width: 768px) {
  .model-chip { max-width: 100px; font-size: 12px; padding: 0 8px; }
  .model-panel { width: 260px; right: 0; }
}
@media (max-width: 400px) {
  .model-chip { max-width: 80px; font-size: 11px; }
}
</style>
