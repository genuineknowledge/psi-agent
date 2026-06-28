<template>
  <div id="messages" ref="messagesRef">
    <div v-if="store.messages.length === 0" class="empty">选择一个会话开始聊天</div>
    <MessageBubble
      v-for="m in store.messages"
      :key="m.id"
      :msg="m"
    />
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { store } from '../store.js'
import MessageBubble from './MessageBubble.vue'

const messagesRef = ref(null)

function scrollToBottom() {
  const el = messagesRef.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

watch(
  () => store.messages.length,
  () => {
    if (store.userHasScrolledUp) return
    nextTick(() => scrollToBottom())
  }
)
</script>

<style scoped>
#messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
}

.empty {
  color: var(--md-text-secondary);
  text-align: center;
  margin-top: 140px;
  font-size: 15px;
}

@media (max-width: 768px) {
  #messages {
    position: absolute;
    top: 52px;
    left: 0;
    right: 0;
    bottom: 0;
    padding: 12px 12px 80px;
  }
}
</style>
