import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useChatStore = defineStore('chat', () => {
  const messages = ref([])
  const sessionMessages = ref({})
  const sessionInputs = ref({})
  const inputText = ref('')
  const streaming = ref(false)
  const abortController = ref(null)
  const selectedFiles = ref([])
  const browser = ref({ path: undefined, parent: '', entries: [] })
  const userHasScrolledUp = ref(false)
  const uploadResetToken = ref(0)
  const isDragging = ref(false)

  return {
    messages,
    sessionMessages,
    sessionInputs,
    inputText,
    streaming,
    abortController,
    selectedFiles,
    browser,
    userHasScrolledUp,
    uploadResetToken,
    isDragging,
  }
})
