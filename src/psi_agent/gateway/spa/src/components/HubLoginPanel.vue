<template>
  <BaseDialog :show="show" width="400px" @close="emit('close')">
    <template #title>登录</template>
    <p class="body">当前为<strong>本地 Gateway 模式</strong>，无需登录即可使用 Web 控制台。</p>
    <p class="hint">账号登录与云端同步将在后续版本提供。</p>
    <template #actions>
      <button class="ok" @click="emit('close')">知道了</button>
    </template>
  </BaseDialog>
</template>

<script setup>
import BaseDialog from './BaseDialog.vue'

defineProps({
  show: { type: Boolean, default: false },
})

const emit = defineEmits(['close'])
</script>

<style scoped>
.body {
  margin: 0 0 12px;
  font-size: 15px;
  line-height: 1.6;
  color: var(--md-text-primary);
}

.hint {
  margin: 0;
  font-size: 13px;
  color: var(--md-text-secondary);
}
</style>
