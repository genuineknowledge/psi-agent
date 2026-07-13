<template>
  <div id="messages" ref="messagesRef" @scroll="onContainerScroll">
    <MessageBubble
      v-for="(m, i) in messages"
      :key="m.id || i"
      :msg="m"
      :show-actions="showMessageActions(m, i)"
      :is-streaming-target="isStreamingTarget(m, i)"
    />
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useChatStore } from '../stores/chat.js'
import MessageBubble from './MessageBubble.vue'
import { registerScrollContainer, scrollToBottomIfLocked, onContainerScroll } from '../composables/useScroll.js'
import { clearStaleStreaming } from '../composables/useChat.js'
import { isCompleteAssistant } from '../messageTurn.js'

const chat = useChatStore()
const { messages, streaming } = storeToRefs(chat)

function isStreamingTarget(msg, index) {
  return streaming.value && index === messages.value.length - 1 && msg.role === 'assistant'
}

function showMessageActions(msg, index) {
  if (msg.role !== 'assistant') return false
  if (isStreamingTarget(msg, index)) return false
  if (streaming.value && index !== messages.value.length - 1) return false
  return isCompleteAssistant(msg)
}

const messagesRef = ref(null)

onMounted(() => {
  registerScrollContainer(messagesRef.value)
  clearStaleStreaming()
})

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
  -webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 24px), transparent 100%);
          mask-image: linear-gradient(to bottom, #000 calc(100% - 24px), transparent 100%);
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
