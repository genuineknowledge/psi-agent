<template>
  <div class="onboarding">
    <div class="onboarding-icon">
      <span class="material-symbols-outlined">chat_add_on</span>
    </div>
    <h2 class="onboarding-title">开始新对话</h2>
    <p class="onboarding-desc">在当前工作区下创建会话，发送第一条消息后才会保存到服务端。</p>
    <button class="onboarding-action md-filled-btn" type="button" @click="emit('new-session')">
      <span class="material-symbols-outlined">add</span>
      <span>新对话</span>
    </button>
  </div>
</template>

<script setup>
const emit = defineEmits(['new-session'])
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
  background: var(--md-primary-container);
  color: var(--md-on-primary-container);
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
