<template>
  <BaseDialog :show="dlgAI" @close="handleCancel">
    <template #title>链接大模型</template>
    <div class="field" style="position:relative">
      <label>供应商</label>
      <div
        class="select-trigger"
        tabindex="0"
        @click="providerOpen = !providerOpen"
        @keydown.enter.prevent="providerOpen = !providerOpen"
        @keydown.escape="providerOpen = false"
        @blur="onProviderBlur"
      >
        <span>{{ currentProviderLabel }}</span>
        <span class="material-symbols-outlined select-arrow">arrow_drop_down</span>
      </div>
      <div v-if="providerOpen" class="custom-dropdown">
        <div
          v-for="p in PROVIDERS"
          :key="p.v"
          class="custom-dropdown-item"
          :class="{ active: p.v === aiForm.provider }"
          @mousedown.prevent="selectProvider(p.v)"
        >{{ p.l }}</div>
      </div>
    </div>
    <div class="field" style="position:relative">
      <label>模型名称</label>
      <input
        ref="modelInput"
        v-model="modelText"
        placeholder="选择或输入模型名称"
        @focus="openModelDropdown"
        @click="openModelDropdown"
        @blur="onBlur"
        @keydown.down.prevent="moveDown"
        @keydown.up.prevent="moveUp"
        @keydown.enter.prevent="selectCurrent"
        @keydown.escape="dropdownOpen = false"
        @input="onInput"
      >
      <div v-if="dropdownOpen && filteredModels.length" class="custom-dropdown">
        <div
          v-for="(m, i) in filteredModels"
          :key="m"
          class="custom-dropdown-item"
          :class="{ active: i === activeIdx }"
          @mousedown.prevent="selectModel(m)"
        >{{ m }}</div>
      </div>
      <span v-if="loadingModels" class="loading-indicator">获取中...</span>
    </div>
    <div class="field"><label>接口地址</label>
      <input v-model="aiForm.base_url" placeholder="https://..." @change="emit('fetchModels')">
    </div>
    <div class="field"><label>API 密钥</label>
      <input v-model="aiForm.api_key" type="password" placeholder="sk-..." @change="emit('fetchModels')">
    </div>
    <template #actions>
      <button class="cancel" @click="handleCancel">取消</button>
      <button class="ok" @click="emit('create')">链接</button>
    </template>
  </BaseDialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useAiStore } from '../stores/ai.js'
import { useUiStore } from '../stores/ui.js'
import { PROVIDERS } from '../providers.js'
import BaseDialog from './BaseDialog.vue'

const ai = useAiStore()
const { aiForm, fetchedModels, loadingModels, ais } = storeToRefs(ai)
const ui = useUiStore()
const { snackbar, dlgAI } = storeToRefs(ui)

const emit = defineEmits(['create', 'fetchModels'])

const modelText = ref(aiForm.value.model)
const dropdownOpen = ref(false)
const activeIdx = ref(-1)
const modelInput = ref(null)
const providerOpen = ref(false)

watch(dlgAI, (v) => {
  if (v) { modelText.value = aiForm.value.model; dropdownOpen.value = false; activeIdx.value = -1; providerOpen.value = false }
})

watch(modelText, (v) => { aiForm.value.model = v })

const currentProviderLabel = computed(() => {
  return PROVIDERS.find(p => p.v === aiForm.value.provider)?.l || aiForm.value.provider
})

function selectProvider(v) {
  aiForm.value.provider = v
  providerOpen.value = false
  handleProviderChange()
}

function onProviderBlur() {
  setTimeout(() => { providerOpen.value = false }, 150)
}

const availableModels = computed(() => {
  const preset = (PROVIDERS.find(p => p.v === aiForm.value.provider)?.models) || []
  return [...new Set([...preset, ...fetchedModels.value])]
})

const filteredModels = computed(() => {
  const q = modelText.value.toLowerCase()
  // When the text exactly matches an available model (i.e. one was just
  // selected/filled), show the full list so the user can pick another,
  // instead of filtering down to that single entry.
  if (!q || availableModels.value.some(m => m.toLowerCase() === q)) return availableModels.value
  return availableModels.value.filter(m => m.toLowerCase().includes(q))
})

function openModelDropdown() {
  dropdownOpen.value = filteredModels.value.length > 0
  activeIdx.value = -1
}

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
  aiForm.value.model = m
  dropdownOpen.value = false
}

function handleProviderChange() {
  const match = PROVIDERS.find(p => p.v === aiForm.value.provider)
  if (match) aiForm.value.base_url = match.base
  fetchedModels.value = []
}

function handleCancel() {
  if (ais.value.length === 0) {
    snackbar.value.show = true
    snackbar.value.message = '至少需要链接一个大模型'
    setTimeout(() => { snackbar.value.show = false }, 2000)
  } else {
    dlgAI.value = false
  }
}
</script>

<style scoped>
.field .loading-indicator { position: absolute; right: 12px; bottom: 10px; font-size: 12px; color: var(--md-primary); }

.select-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  background: var(--md-bg);
  color: var(--md-text-primary);
  border: 1px solid var(--md-outline-variant);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 14px;
  cursor: pointer;
  outline: none;
  box-sizing: border-box;
}
.select-trigger:focus { border-color: var(--md-primary); }
.select-arrow { font-size: 20px; color: var(--md-text-secondary); }

.custom-dropdown {
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

.custom-dropdown-item {
  padding: 8px 12px;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.12s;
}

.custom-dropdown-item:hover,
.custom-dropdown-item.active {
  background: rgba(128, 128, 128, var(--md-state-hover));
}
</style>
