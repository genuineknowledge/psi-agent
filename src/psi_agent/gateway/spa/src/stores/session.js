import { defineStore } from 'pinia'
import { ref } from 'vue'

import { loadPinnedSessionIds } from '../sessionList.js'

export const useSessionStore = defineStore('session', () => {
  const sessions = ref([])
  const selectedSessionId = ref(null)
  const sessionTitles = ref({})
  const sessionMessages = ref({})
  const sessionInputs = ref({})
  /** Per-session SSE in flight; survives sidebar switches without aborting backend. */
  const sessionStreaming = ref({})
  /** Per-session AbortController for stop button (in-memory only). */
  const sessionAbortControllers = ref({})
  const sessForm = ref({ workspace: '' })
  const editingSessionId = ref(null)
  const editingWorkspaceText = ref('')
  const sessionSearchText = ref('')
  const pinnedSessionIds = ref(loadPinnedSessionIds(window.localStorage))
  const browser = ref({ path: undefined, parent: '', entries: [] })

  return {
    sessions,
    selectedSessionId,
    sessionTitles,
    sessionMessages,
    sessionInputs,
    sessionStreaming,
    sessionAbortControllers,
    sessForm,
    editingSessionId,
    editingWorkspaceText,
    sessionSearchText,
    pinnedSessionIds,
    browser,
  }
})
