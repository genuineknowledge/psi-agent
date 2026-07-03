import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAiStore = defineStore('ai', () => {
  const ais = ref([])
  const selectedAiId = ref(null)
  const aiForm = ref({ provider: 'deepseek', base_url: 'https://api.deepseek.com/v1', api_key: '', model: '' })
  const fetchedModels = ref([])
  const loadingModels = ref(false)
  const modelPanelOpen = ref(false)

  return {
    ais,
    selectedAiId,
    aiForm,
    fetchedModels,
    loadingModels,
    modelPanelOpen,
  }
})
