import { watch } from 'vue'
import { useColorMode, useStorage } from '@vueuse/core'
import { useUiStore } from '../stores/ui.js'

const LS_THEME = 'gw-theme'

export function useTheme() {
  const ui = useUiStore()

  // 用 VueUse useColorMode 管理主题，同时保持既有行为：
  // - storageKey 仍是 'gw-theme'，存 'light' / 'dark'（格式不变）
  // - class 语义反转：亮色给 <html> 加 'light-mode'，暗色不加（modes 映射）
  // - 传入自建 storageRef（writeDefaults: false）—— useColorMode 内部的 useStorage
  //   默认 writeDefaults:true 会在首次挂载写 localStorage；用 storageRef 绕过它，
  //   保持“用户不切换就从不主动写”的原有行为。
  // - 未存储时默认亮色（storageRef 的默认值 'light'），不跟随系统偏好（emitAuto:false）。
  // - disableTransition:false —— 保留切换主题时的 CSS 过渡动画（useColorMode 默认会
  //   注入 *{transition:none} 强制瞬切，与原手写实现的渐变行为不一致）。
  // - listenToStorageChanges:false —— 不做跨 tab 同步（对齐原手写实现）。
  const store = useStorage(LS_THEME, 'light', undefined, {
    writeDefaults: false,
    listenToStorageChanges: false,
  })
  const mode = useColorMode({
    attribute: 'class',
    selector: 'html',
    modes: { light: 'light-mode', dark: '' },
    emitAuto: false,
    disableTransition: false,
    storageRef: store,
  })

  // ui.isLightMode 与主题联动：只有恰好 'dark' 视为暗色，其它一律亮色。
  const syncIsLightMode = () => { ui.isLightMode = mode.value !== 'dark' }
  syncIsLightMode()
  watch(mode, syncIsLightMode)

  function toggleTheme() {
    mode.value = mode.value === 'dark' ? 'light' : 'dark'
  }

  return { toggleTheme }
}
