<template>
  <BaseDialog :show="show" width="560px" @close="handleClose">
    <template #title>
      <div class="title-row">
        <span>大模型</span>
        <button type="button" class="advanced-link" @click="openAdvanced">
          高级配置
        </button>
      </div>
    </template>

    <section v-if="ais.length" class="section connected-section">
      <h4 class="section-label">已连接</h4>
      <ul class="ai-list">
        <li v-for="a in ais" :key="a.id" class="ai-row">
          <span class="material-symbols-outlined ai-icon">smart_toy</span>
          <div class="ai-info">
            <div class="ai-model">{{ a.model || a.id }}</div>
            <div class="ai-provider">{{ a.provider }}</div>
          </div>
          <span v-if="a.id === selectedAiId" class="badge">当前</span>
        </li>
      </ul>
    </section>

    <section class="section">
      <h4 class="section-label">选择模型</h4>
      <div class="preset-grid">
        <button
          v-for="preset in MODEL_PRESETS"
          :key="preset.id"
          type="button"
          class="preset-card"
          :class="{ active: selectedPresetId === preset.id }"
          :title="preset.hint || preset.label"
          @click="selectPreset(preset.id)"
        >
          <span class="preset-mark" :style="{ background: presetAccentBg(preset) }">
            <span class="preset-mark-inner" :style="{ color: preset.accent }">{{ preset.mark }}</span>
          </span>
          <span class="preset-label">{{ preset.label }}</span>
        </button>
      </div>
    </section>

    <section v-if="selectedPreset" class="section key-section">
      <h4 class="section-label">API Key</h4>
      <p class="key-context">
        连接 <strong>{{ selectedPreset.label }}</strong>
        <span class="key-model">· {{ selectedPreset.model }}</span>
      </p>
      <input
        v-model="apiKey"
        type="password"
        class="key-input"
        placeholder="sk-..."
        autocomplete="off"
        @keydown.enter.prevent="connect"
      >
    </section>

    <template #actions>
      <button class="cancel" @click="handleClose">关闭</button>
      <button
        v-if="selectedPreset"
        class="cancel"
        @click="clearSelection"
      >换模型</button>
      <button
        class="ok"
        :disabled="!canConnect || connecting"
        @click="connect"
      >
        {{ connecting ? '连接中…' : '连接' }}
      </button>
    </template>
  </BaseDialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useAiStore } from '../stores/ai.js'
import { useUiStore } from '../stores/ui.js'
import { api } from '../api.js'
import { MODEL_PRESETS, getModelPreset, presetToAiPayload } from '../modelPresets.js'
import BaseDialog from './BaseDialog.vue'

const props = defineProps({
  show: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'connected'])

const ui = useUiStore()
const ai = useAiStore()
const { ais, selectedAiId } = storeToRefs(ai)

const selectedPresetId = ref(null)
const apiKey = ref('')
const connecting = ref(false)

const selectedPreset = computed(() =>
  selectedPresetId.value ? getModelPreset(selectedPresetId.value) : null
)

const canConnect = computed(() =>
  !!selectedPreset.value && apiKey.value.trim().length > 0
)

function presetAccentBg(preset) {
  return `color-mix(in srgb, ${preset.accent} 14%, var(--md-surface-container-high))`
}

function resetForm() {
  selectedPresetId.value = null
  apiKey.value = ''
  connecting.value = false
}

watch(
  () => props.show,
  (open) => { if (open) resetForm() },
)

function selectPreset(id) {
  if (selectedPresetId.value === id) return
  selectedPresetId.value = id
  apiKey.value = ''
}

function clearSelection() {
  selectedPresetId.value = null
  apiKey.value = ''
}

function handleClose() {
  resetForm()
  emit('close')
}

function openAdvanced() {
  emit('close')
  resetForm()
  ui.dlgAI = true
}

async function connect() {
  const preset = selectedPreset.value
  if (!preset || !canConnect.value || connecting.value) return
  connecting.value = true
  try {
    const info = await api('POST', '/ais', presetToAiPayload(preset, apiKey.value))
    ais.value = await api('GET', '/ais')
    selectedAiId.value = info.id
    emit('connected', info.id)
    ui.showAlert(`${preset.label} 已连接`)
    handleClose()
  } catch (e) {
    ui.showAlert(e.message || '连接失败')
  } finally {
    connecting.value = false
  }
}
</script>

<style scoped>
.title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.advanced-link {
  border: none;
  background: none;
  padding: 4px 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--md-text-secondary);
  cursor: pointer;
  border-radius: 8px;
}

.advanced-link:hover {
  color: var(--md-primary);
  background: rgba(128, 128, 128, var(--md-state-hover));
}

.section {
  margin-bottom: 18px;
}

.section:last-child {
  margin-bottom: 0;
}

.section-label {
  margin: 0 0 10px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--md-text-secondary);
  text-transform: uppercase;
}

.connected-section {
  padding-bottom: 4px;
  border-bottom: 1px solid var(--md-outline-variant);
}

.ai-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ai-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid var(--md-outline-variant);
  background: var(--md-surface-container);
}

.ai-icon {
  font-size: 20px;
  color: var(--md-primary);
}

.ai-info {
  flex: 1;
  min-width: 0;
}

.ai-model {
  font-size: 14px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ai-provider {
  font-size: 12px;
  color: var(--md-text-secondary);
}

.badge {
  font-size: 11px;
  font-weight: 600;
  color: var(--md-primary);
  background: color-mix(in srgb, var(--md-primary) 12%, transparent);
  padding: 4px 8px;
  border-radius: 999px;
}

.preset-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.preset-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 12px 8px;
  border-radius: 14px;
  border: 1.5px solid var(--md-outline-variant);
  background: var(--md-surface-container);
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.preset-card:hover {
  border-color: color-mix(in srgb, var(--md-primary) 35%, var(--md-outline-variant));
}

.preset-card.active {
  border-color: var(--md-primary);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--md-primary) 25%, transparent);
}

.preset-mark {
  width: 44px;
  height: 44px;
  border-radius: var(--md-shape-full);
  display: flex;
  align-items: center;
  justify-content: center;
}

.preset-mark-inner {
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
}

.preset-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--md-text-primary);
  text-align: center;
  line-height: 1.2;
}

.key-section {
  padding: 14px;
  border-radius: 14px;
  background: var(--md-surface-container);
  border: 1px solid var(--md-outline-variant);
}

.key-context {
  margin: 0 0 10px;
  font-size: 14px;
  color: var(--md-text-primary);
}

.key-model {
  font-size: 12px;
  font-weight: 500;
  color: var(--md-text-secondary);
}

.key-input {
  width: 100%;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid var(--md-outline-variant);
  background: var(--md-surface-container-high);
  color: var(--md-text-primary);
  font: inherit;
}

@media (max-width: 520px) {
  .preset-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
</style>
