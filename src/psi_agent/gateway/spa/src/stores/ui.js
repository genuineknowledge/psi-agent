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
  const dlgSess = ref(false)

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

  return {
    loadingEnv,
    snackbar,
    dlgConfirm,
    isLightMode,
    isSidebarCollapsed,
    isMobileSidebarOpen,
    isDragging,
    dlgAI,
    dlgSess,
    showAlert,
    toggleSidebar,
    closeMobileSidebar,
  }
})
