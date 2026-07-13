<template>
  <BaseDialog :show="show" width="440px" @close="emit('close')">
    <template #title>设置</template>
    <section class="section">
      <h4>外观</h4>
      <button type="button" class="row-btn" @click="toggleTheme">
        <span class="material-symbols-outlined">{{ isLightMode ? 'dark_mode' : 'light_mode' }}</span>
        <span>{{ isLightMode ? '切换至暗色模式' : '切换至亮色模式' }}</span>
      </button>
    </section>
    <section class="section">
      <h4>权限与能力</h4>
      <p class="hint">部分工具依赖外部 API Key，可在「大模型」中配置模型连接；workspace 级密钥后续在此展示。</p>
    </section>
    <template #actions>
      <button class="ok" @click="emit('close')">关闭</button>
    </template>
  </BaseDialog>
</template>

<script setup>
import { storeToRefs } from 'pinia'
import { useUiStore } from '../stores/ui.js'
import { useTheme } from '../composables/useTheme.js'
import BaseDialog from './BaseDialog.vue'

defineProps({
  show: { type: Boolean, default: false },
})

const emit = defineEmits(['close'])

const { isLightMode } = storeToRefs(useUiStore())
const { toggleTheme } = useTheme()
</script>

<style scoped>
.section {
  margin-bottom: 20px;
}

.section h4 {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--md-text-secondary);
}

.row-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid var(--md-outline-variant);
  background: var(--md-surface-container);
  color: var(--md-text-primary);
  font: inherit;
  cursor: pointer;
  text-align: left;
}

.row-btn:hover {
  background: var(--md-surface-container-high);
}

.hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--md-text-secondary);
}
</style>
