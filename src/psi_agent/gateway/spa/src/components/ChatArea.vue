<template>
  <div id="messages" ref="messagesRef" @scroll="onContainerScroll">
    <MessageBubble
      v-for="(m, i) in messages"
      :key="m.id || i"
      :msg="m"
    />
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useChatStore } from '../stores/chat.js'
import MessageBubble from './MessageBubble.vue'
import { registerScrollContainer, scrollToBottomIfLocked, onContainerScroll } from '../composables/useScroll.js'

const chat = useChatStore()
const { messages } = storeToRefs(chat)

const messagesRef = ref(null)

onMounted(() => registerScrollContainer(messagesRef.value))

watch(
  () => messages.value.length,
  () => scrollToBottomIfLocked()
)

defineExpose({ onContainerScroll })
</script>

<style scoped>
#messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
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
