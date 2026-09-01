import { nextTick } from 'vue'
import { useChatStore } from '../stores/chat.js'

// 模块级单例:消息滚动容器(由拥有它的组件注册)
let container = null

export function registerScrollContainer(el) {
  container = el
}

// 供容器的 @scroll 调用:距底 > 60px 视为用户手动上滚
export function onContainerScroll() {
  const chat = useChatStore()
  if (!container) return
  const distanceFromBottom = container.scrollHeight - container.clientHeight - container.scrollTop
  chat.userHasScrolledUp = distanceFromBottom > 60
}

// 若未被用户上滚锁定,则滚到底(流式/新消息时调用)
export function scrollToBottomIfLocked() {
  const chat = useChatStore()
  nextTick(() => {
    if (!container) return
    const distanceFromBottom = container.scrollHeight - container.clientHeight - container.scrollTop
    if (chat.streaming) {
      if (chat.userHasScrolledUp && distanceFromBottom > 60) return
      if (distanceFromBottom <= 60) chat.userHasScrolledUp = false
    }
    container.scrollTop = container.scrollHeight
  })
}
