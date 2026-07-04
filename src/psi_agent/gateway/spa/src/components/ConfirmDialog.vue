<template>
  <BaseDialog :show="dlgConfirm.show" @close="dlgConfirm.show = false">
    <template #title>确认删除</template>
    <p class="alert-desc">{{ dlgConfirm.message }}</p>
    <template #actions>
      <button class="cancel" @click="dlgConfirm.show = false">取消</button>
      <button class="ok" style="background: var(--md-text-error); color: #1a0002;" @click="$emit('confirm')">删除</button>
    </template>
  </BaseDialog>
</template>

<script setup>
import { storeToRefs } from 'pinia'
import { useUiStore } from '../stores/ui.js'
import BaseDialog from './BaseDialog.vue'

const ui = useUiStore()
const { dlgConfirm } = storeToRefs(ui)

defineEmits(['confirm'])
</script>

<style scoped>
.alert-desc {
  font-size: 14px;
  color: var(--md-text-secondary);
  line-height: 1.5;
  margin-bottom: 12px;
}
</style>
