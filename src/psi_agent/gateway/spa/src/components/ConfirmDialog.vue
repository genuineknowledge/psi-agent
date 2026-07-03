<template>
  <BaseDialog :show="dlgConfirm.show" @close="dlgConfirm.show = false">
    <template #title>{{ isUndo ? '确认撤回' : '确认删除' }}</template>
    <p class="alert-desc">{{ dlgConfirm.message }}</p>
    <label v-if="isUndo" class="skip-confirm">
      <input type="checkbox" v-model="dontAsk">
      <span>以后撤回对话不再提示</span>
    </label>
    <template #actions>
      <button class="cancel" @click="dlgConfirm.show = false">取消</button>
      <button class="ok" style="background: var(--md-text-error); color: #1a0002;" @click="onConfirm">{{ isUndo ? '撤回' : '删除' }}</button>
    </template>
  </BaseDialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useUiStore } from '../stores/ui.js'
import { saveUndoSkipConfirm } from '../utils.js'
import BaseDialog from './BaseDialog.vue'

const ui = useUiStore()
const { dlgConfirm } = storeToRefs(ui)

const emit = defineEmits(['confirm'])

const isUndo = computed(() => dlgConfirm.value.actionType === 'undo')
const dontAsk = ref(false)

// 每次打开确认框时重置复选框
watch(() => dlgConfirm.value.show, (show) => {
  if (show) dontAsk.value = false
})

function onConfirm() {
  if (isUndo.value && dontAsk.value) saveUndoSkipConfirm(true)
  emit('confirm')
}
</script>

<style scoped>
.alert-desc {
  font-size: 14px;
  color: var(--md-text-secondary);
  line-height: 1.5;
  margin-bottom: 12px;
}
.skip-confirm {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--md-text-secondary);
  margin-bottom: 12px;
  cursor: pointer;
  user-select: none;
}
.skip-confirm input {
  cursor: pointer;
}
</style>
