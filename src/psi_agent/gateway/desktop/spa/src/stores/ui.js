import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUiStore = defineStore('ui', () => {
  const loadingEnv = ref(true)
  const snackbar = ref({ show: false, message: '' })
  const dlgConfirm = ref({ show: false, message: '', actionArgs: null, actionType: 'session' })
  const isLightMode = ref(true)
  const isSidebarCollapsed = ref(false)
  const isMobileSidebarOpen = ref(false)
  const isDragging = ref(false)
  const dlgAI = ref(false)
  const dlgRouter = ref(false)
  const dlgSess = ref(false)
  const sessionSearchFocusToken = ref(0)
  const hubMenuOpen = ref(false)
  /** @type {import('vue').Ref<'profile'|'models'|'login'|'settings'|null>} */
  const hubPanel = ref(null)

  let snackbarTimer = null

  function showAlert(message) {
    snackbar.value.message = message
    snackbar.value.show = true
    if (snackbarTimer) clearTimeout(snackbarTimer)
    snackbarTimer = setTimeout(() => {
      if (snackbar.value.message === message) snackbar.value.show = false
    }, 4000)
  }

  function toggleSidebar(isMobile) {
    if (isMobile) {
      isMobileSidebarOpen.value = !isMobileSidebarOpen.value
    } else {
      isSidebarCollapsed.value = !isSidebarCollapsed.value
    }
  }

  function closeMobileSidebar() {
    isMobileSidebarOpen.value = false
  }

  function focusSessionSearch() {
    sessionSearchFocusToken.value++
  }

  function toggleHubMenu() {
    hubMenuOpen.value = !hubMenuOpen.value
  }

  function closeHubMenu() {
    hubMenuOpen.value = false
  }

  function openHubPanel(panel) {
    hubPanel.value = panel
    hubMenuOpen.value = false
  }

  function closeHubPanel() {
    hubPanel.value = null
  }

  return {
    loadingEnv,
    snackbar,
    dlgConfirm,
    isLightMode,
    isSidebarCollapsed,
    isMobileSidebarOpen,
    isDragging,
    dlgAI,
    dlgRouter,
    dlgSess,
    sessionSearchFocusToken,
    hubMenuOpen,
    hubPanel,
    showAlert,
    toggleSidebar,
    closeMobileSidebar,
    focusSessionSearch,
    toggleHubMenu,
    closeHubMenu,
    openHubPanel,
    closeHubPanel,
  }
})
