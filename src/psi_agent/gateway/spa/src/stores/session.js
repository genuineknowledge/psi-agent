import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  loadCollapsedWorkspaces,
  loadRegisteredWorkspaces,
  loadSelectedWorkspace,
  saveCollapsedWorkspaces,
  saveRegisteredWorkspaces,
  saveSelectedWorkspace,
} from '../utils.js'
import { loadPinnedSessionIds, mergeWorkspacePaths, normalizeWorkspacePath } from '../sessionList.js'

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
  /** Gateway process cwd — used when session.workspace is empty. */
  const gatewayCwd = ref('')
  /** User-registered workspace paths (localStorage). */
  const registeredWorkspaces = ref(loadRegisteredWorkspaces())
  const selectedWorkspacePath = ref(loadSelectedWorkspace())
  const collapsedWorkspacePaths = ref(loadCollapsedWorkspaces())
  const editingSessionId = ref(null)
  const editingWorkspaceText = ref('')
  const sessionSearchText = ref('')
  const pinnedSessionIds = ref(loadPinnedSessionIds(window.localStorage))

  function syncRegisteredWorkspaces() {
    const merged = mergeWorkspacePaths(registeredWorkspaces.value, sessions.value, gatewayCwd.value)
    registeredWorkspaces.value = merged
    saveRegisteredWorkspaces(merged)
  }

  function setSelectedWorkspace(path) {
    const n = normalizeWorkspacePath(path)
    selectedWorkspacePath.value = n
    saveSelectedWorkspace(n)
  }

  function addRegisteredWorkspace(path) {
    const n = normalizeWorkspacePath(path)
    if (!n) return false
    if (!registeredWorkspaces.value.includes(n)) {
      registeredWorkspaces.value = [...registeredWorkspaces.value, n]
      saveRegisteredWorkspaces(registeredWorkspaces.value)
    }
    return true
  }

  function removeRegisteredWorkspace(path) {
    const n = normalizeWorkspacePath(path)
    registeredWorkspaces.value = registeredWorkspaces.value.filter(p => p !== n)
    saveRegisteredWorkspaces(registeredWorkspaces.value)
    collapsedWorkspacePaths.value = collapsedWorkspacePaths.value.filter(p => p !== n)
    saveCollapsedWorkspaces(collapsedWorkspacePaths.value)
    if (selectedWorkspacePath.value === n) {
      setSelectedWorkspace('')
    }
  }

  function isWorkspaceExpanded(path) {
    const n = normalizeWorkspacePath(path)
    return !collapsedWorkspacePaths.value.includes(n)
  }

  function toggleWorkspaceExpanded(path) {
    const n = normalizeWorkspacePath(path)
    if (collapsedWorkspacePaths.value.includes(n)) {
      collapsedWorkspacePaths.value = collapsedWorkspacePaths.value.filter(p => p !== n)
    } else {
      collapsedWorkspacePaths.value = [...collapsedWorkspacePaths.value, n]
    }
    saveCollapsedWorkspaces(collapsedWorkspacePaths.value)
  }

  function ensureWorkspaceExpanded(path) {
    const n = normalizeWorkspacePath(path)
    if (!n || isWorkspaceExpanded(n)) return
    collapsedWorkspacePaths.value = collapsedWorkspacePaths.value.filter(p => p !== n)
    saveCollapsedWorkspaces(collapsedWorkspacePaths.value)
  }

  return {
    sessions,
    selectedSessionId,
    sessionTitles,
    sessionMessages,
    sessionInputs,
    sessionStreaming,
    sessionAbortControllers,
    gatewayCwd,
    registeredWorkspaces,
    selectedWorkspacePath,
    collapsedWorkspacePaths,
    editingSessionId,
    editingWorkspaceText,
    sessionSearchText,
    pinnedSessionIds,
    syncRegisteredWorkspaces,
    setSelectedWorkspace,
    addRegisteredWorkspace,
    removeRegisteredWorkspace,
    isWorkspaceExpanded,
    toggleWorkspaceExpanded,
    ensureWorkspaceExpanded,
  }
})
