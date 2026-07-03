import { defineStore } from 'pinia'
import { ref } from 'vue'

import { loadPinnedSessionIds } from '../sessionList.js'

export const useSessionStore = defineStore('session', () => {
  const sessions = ref([])
  const selectedSessionId = ref(null)
  const sessionTitles = ref({})
  const dlgSess = ref(false)
  const sessForm = ref({ workspace: '' })
  const editingSessionId = ref(null)
  const editingWorkspaceText = ref('')
  const sessionSearchText = ref('')
  const pinnedSessionIds = ref(loadPinnedSessionIds(window.localStorage))

  return {
    sessions,
    selectedSessionId,
    sessionTitles,
    dlgSess,
    sessForm,
    editingSessionId,
    editingWorkspaceText,
    sessionSearchText,
    pinnedSessionIds,
  }
})
