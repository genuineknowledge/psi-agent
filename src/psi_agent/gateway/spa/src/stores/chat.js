import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useChatStore = defineStore('chat', () => {
  const messages = ref([])
  const inputText = ref('')
  const streaming = ref(false)
  const abortController = ref(null)
  const selectedFiles = ref([])
  const userHasScrolledUp = ref(false)
  const uploadResetToken = ref(0)

  return {
    messages,
    inputText,
    streaming,
    abortController,
    selectedFiles,
    userHasScrolledUp,
    uploadResetToken,
  }
})
