<template>
  <BaseDialog :show="store.dlgAI" @close="handleCancel">
    <template #title>链接大模型</template>
    <div class="field"><label>供应商</label>
      <select v-model="store.aiForm.provider" @change="handleProviderChange">
        <option v-for="p in PROVIDERS" :key="p.v" :value="p.v">{{ p.l }}</option>
      </select>
    </div>
    <div class="field"><label>接口地址</label>
      <input v-model="store.aiForm.base_url" placeholder="https://..." @change="emit('fetchModels')">
    </div>
    <div class="field"><label>API 密钥</label>
      <input v-model="store.aiForm.api_key" type="password" placeholder="sk-..." @change="emit('fetchModels')">
    </div>
    <div class="field" style="position:relative">
      <label>模型名称</label>
      <input
        ref="modelInput"
        v-model="modelText"
        placeholder="选择或输入模型名称"
        @focus="dropdownOpen = store.fetchedModels.length > 0"
        @blur="onBlur"
        @keydown.down.prevent="moveDown"
        @keydown.up.prevent="moveUp"
        @keydown.enter.prevent="selectCurrent"
        @keydown.escape="dropdownOpen = false"
        @input="onInput"
      >
      <div v-if="dropdownOpen && filteredModels.length" class="model-dropdown">
        <div
          v-for="(m, i) in filteredModels"
          :key="m"
          class="model-dropdown-item"
          :class="{ active: i === activeIdx }"
          @mousedown.prevent="selectModel(m)"
        >{{ m }}</div>
      </div>
      <span v-if="store.loadingModels" class="loading-indicator">获取中...</span>
    </div>
    <template #actions>
      <button class="cancel" @click="handleCancel">取消</button>
      <button class="ok" @click="emit('create')">链接</button>
    </template>
  </BaseDialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { store } from '../store.js'
import { PROVIDERS } from '../providers.js'
import BaseDialog from './BaseDialog.vue'

const emit = defineEmits(['create', 'fetchModels'])

const modelText = ref(store.aiForm.model)
const dropdownOpen = ref(false)
const activeIdx = ref(-1)
const modelInput = ref(null)

watch(() => store.dlgAI, (v) => {
  if (v) { modelText.value = store.aiForm.model; dropdownOpen.value = false; activeIdx.value = -1 }
})

watch(modelText, (v) => { store.aiForm.model = v })

const filteredModels = computed(() => {
  const q = modelText.value.toLowerCase()
  if (!q) return store.fetchedModels
  return store.fetchedModels.filter(m => m.toLowerCase().includes(q))
})

function onInput() {
  dropdownOpen.value = filteredModels.value.length > 0
  activeIdx.value = -1
}

function onBlur() {
  setTimeout(() => { dropdownOpen.value = false }, 150)
}

function moveDown() {
  if (!dropdownOpen.value) { dropdownOpen.value = true; return }
  activeIdx.value = Math.min(activeIdx.value + 1, filteredModels.value.length - 1)
}

function moveUp() {
  activeIdx.value = Math.max(activeIdx.value - 1, -1)
}

function selectCurrent() {
  if (activeIdx.value >= 0 && filteredModels.value[activeIdx.value]) {
    selectModel(filteredModels.value[activeIdx.value])
  }
}

function selectModel(m) {
  modelText.value = m
  store.aiForm.model = m
  dropdownOpen.value = false
}

function handleProviderChange() {
  const match = PROVIDERS.find(p => p.v === store.aiForm.provider)
  if (match) store.aiForm.base_url = match.base
}

function handleCancel() {
  if (store.ais.length === 0) {
    store.snackbar.show = true
    store.snackbar.message = '至少需要链接一个大模型'
    setTimeout(() => { store.snackbar.show = false }, 2000)
  } else {
    store.dlgAI = false
  }
}
</script>

<style scoped>
.field { margin-bottom: 16px; position: relative; }
.field label { display: block; font-size: 12px; font-weight: 500; color: var(--md-primary); margin-bottom: 6px; }
.field input, .field select {
  width: 100%; background: var(--md-bg); color: var(--md-text-primary);
  border: 1px solid var(--md-outline-variant); border-radius: 8px;
  padding: 10px 12px; font-size: 14px; outline: none;
}
.field input:focus, .field select:focus { border-color: var(--md-primary); }
.field .loading-indicator { position: absolute; right: 12px; bottom: 10px; font-size: 12px; color: var(--md-primary); }

.model-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  background: var(--md-surface-container-high);
  border: 1px solid var(--md-outline-variant);
  border-radius: var(--md-shape-small);
  box-shadow: var(--md-elevation-2);
  max-height: 180px;
  overflow-y: auto;
  z-index: 10;
  margin-top: 2px;
}

.model-dropdown-item {
  padding: 8px 12px;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.12s;
}

.model-dropdown-item:hover,
.model-dropdown-item.active {
  background: rgba(128, 128, 128, var(--md-state-hover));
}
</style>
