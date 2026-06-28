import { watch } from 'vue'
import { store } from '../store.js'

const LS_THEME = 'gw-theme'

export function useTheme() {
  const saved = localStorage.getItem(LS_THEME)
  if (saved !== 'dark') {
    store.isLightMode = true
    document.documentElement.classList.add('light-mode')
  } else {
    store.isLightMode = false
    document.documentElement.classList.remove('light-mode')
  }

  function toggleTheme() {
    store.isLightMode = !store.isLightMode
    if (store.isLightMode) {
      document.documentElement.classList.add('light-mode')
      localStorage.setItem(LS_THEME, 'light')
    } else {
      document.documentElement.classList.remove('light-mode')
      localStorage.setItem(LS_THEME, 'dark')
    }
  }

  return { toggleTheme }
}
