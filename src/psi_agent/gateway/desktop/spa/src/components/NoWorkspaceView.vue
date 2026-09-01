<template>
  <div class="onboarding">
    <div class="onboarding-icon">
      <span class="material-symbols-outlined">folder_open</span>
    </div>
    <h2 class="onboarding-title">打开工作区</h2>
    <p class="onboarding-desc">选择本地文件夹作为 Agent 工作区，之后可在其下创建多个会话。</p>
    <button class="onboarding-action md-filled-btn" type="button" @click="emit('open-workspace')">
      <span class="material-symbols-outlined">folder_open</span>
      <span>打开工作区</span>
    </button>
  </div>
</template>

<script setup>
const emit = defineEmits(['open-workspace'])
</script>

<style scoped>
.onboarding {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 32px 24px;
  max-width: 420px;
  margin: 0 auto;
}
.onboarding-icon {
  width: 64px;
  height: 64px;
  border-radius: var(--md-shape-full);
  background: var(--md-secondary-container);
  color: var(--md-on-secondary-container);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 20px;
}
.onboarding-icon .material-symbols-outlined { font-size: 32px; }
.onboarding-title {
  margin: 0 0 8px;
  font-size: 28px;
  font-weight: 500;
  color: var(--md-text-primary);
}
.onboarding-desc {
  margin: 0 0 24px;
  font-size: 15px;
  line-height: 1.6;
  color: var(--md-text-secondary);
}
.onboarding-action {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 12px 24px;
  font-size: 15px;
}
</style>
