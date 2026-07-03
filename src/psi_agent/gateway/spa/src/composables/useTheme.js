import { useUiStore } from '../stores/ui.js'

const LS_THEME = 'gw-theme'

export function useTheme() {
  const ui = useUiStore()

  const saved = localStorage.getItem(LS_THEME)
  if (saved !== 'dark') {
    ui.isLightMode = true
    document.documentElement.classList.add('light-mode')
  } else {
    ui.isLightMode = false
    document.documentElement.classList.remove('light-mode')
  }

  function toggleTheme() {
    ui.isLightMode = !ui.isLightMode
    if (ui.isLightMode) {
      document.documentElement.classList.add('light-mode')
      localStorage.setItem(LS_THEME, 'light')
    } else {
      document.documentElement.classList.remove('light-mode')
      localStorage.setItem(LS_THEME, 'dark')
    }
  }

  return { toggleTheme }
}
