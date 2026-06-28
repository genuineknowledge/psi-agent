<template>
  <div class="model-zone">
    <div v-if="store.modelPanelOpen" class="model-panel-backdrop" @click="store.modelPanelOpen = false"></div>

    <div class="model-chip" :class="{ open: store.modelPanelOpen }" @click="store.modelPanelOpen = !store.modelPanelOpen" :title="currentModelLabel">
      <span class="material-symbols-outlined chip-icon">smart_toy</span>
      <span class="chip-label">{{ currentModelLabel }}</span>
      <span class="material-symbols-outlined chip-arrow">expand_more</span>
    </div>

    <div v-if="store.modelPanelOpen" class="model-panel">
      <div class="model-panel-header">
        <span>大模型</span>
        <button @click="openNewAi">
          <span class="material-symbols-outlined">add</span>链接新模型
        </button>
      </div>
      <div class="model-panel-list">
        <div v-if="store.ais.length === 0" class="model-panel-empty">暂无模型，请点击「链接新模型」</div>
        <div v-for="a in store.ais" :key="a.id"
             class="model-panel-item"
             :class="{ active: a.id === store.selectedAiId }"
             @click="selectAi(a.id)">
          <span class="material-symbols-outlined mpi-icon">smart_toy</span>
          <div class="mpi-info">
            <div class="mpi-name" :title="a.model || a.id">{{ a.model || a.id }}</div>
            <div class="mpi-provider">{{ a.provider }}</div>
          </div>
          <span v-if="a.id === store.selectedAiId" class="material-symbols-outlined mpi-check">check_circle</span>
          <button class="mpi-del" @click.stop="requestDelete(a.id)" title="删除此模型">
            <span class="material-symbols-outlined">delete</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { store } from '../store.js'

const emit = defineEmits(['select-ai', 'delete-ai', 'new-ai'])

const currentModelLabel = computed(() => {
  const ai = store.ais.find(a => a.id === store.selectedAiId)
  return ai ? (ai.model || ai.id) : '选择模型'
})

function selectAi(id) {
  emit('select-ai', id)
  store.modelPanelOpen = false
}

function requestDelete(id) {
  store.modelPanelOpen = false
  emit('delete-ai', id)
}

function openNewAi() {
  store.modelPanelOpen = false
  emit('new-ai')
}
</script>
