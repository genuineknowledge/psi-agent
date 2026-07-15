<template>
  <BaseDialog :show="dlgSess" width="400px" @close="dlgSess = false">
    <template #title>创建新会话</template>
    <p v-if="currentAiLabel" class="ai-label">
      大模型: {{ currentAiLabel }}
    </p>
    <p v-if="workspaceLabel" class="ws-label">
      工作区: {{ workspaceLabel }}
    </p>
    <p v-else class="ws-label warn">请先在侧栏打开一个工作区</p>
    <template #actions>
      <button class="cancel" type="button" @click="dlgSess = false">取消</button>
      <button class="ok" type="button" :disabled="!selectedWorkspacePath" @click="emit('create')">创建</button>
    </template>
  </BaseDialog>
</template>

<script setup>
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useSessionStore } from '../stores/session.js'
import { useUiStore } from '../stores/ui.js'
import { useAiStore } from '../stores/ai.js'
import { getWorkspaceLabel } from '../sessionList.js'
import BaseDialog from './BaseDialog.vue'

const session = useSessionStore()
const { selectedWorkspacePath } = storeToRefs(session)
const ui = useUiStore()
const { dlgSess } = storeToRefs(ui)
const ai = useAiStore()
const { selectedAiId, ais } = storeToRefs(ai)

const emit = defineEmits(['create'])

const currentAiLabel = computed(() => {
  const found = ais.value.find(a => a.id === selectedAiId.value)
  return found ? (found.model || found.id) : ''
})

const workspaceLabel = computed(() => {
  if (!selectedWorkspacePath.value) return ''
  return getWorkspaceLabel(selectedWorkspacePath.value)
})
</script>

<style scoped>
.ai-label,
.ws-label {
  font-size: 13px;
  color: var(--md-text-secondary);
  margin-bottom: 8px;
}
.ws-label.warn {
  color: var(--md-text-error);
  margin-bottom: 16px;
}
</style>
