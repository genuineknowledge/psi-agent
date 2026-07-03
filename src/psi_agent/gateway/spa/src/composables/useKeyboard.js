import { onMounted } from 'vue'
import { useChatStore } from '../stores/chat.js'

export function useKeyboard() {
  const chat = useChatStore()

  onMounted(() => {
    const messagesEl = document.getElementById('messages')

    if (messagesEl) {
      messagesEl.addEventListener('scroll', () => {
        if (!chat.streaming) return
        const diff = messagesEl.scrollHeight - messagesEl.clientHeight - messagesEl.scrollTop
        if (diff > 60) {
          if (!chat.userHasScrolledUp) chat.userHasScrolledUp = true
        } else {
          chat.userHasScrolledUp = false
        }
      })
    }

    if (window.visualViewport) {
      const syncInputPosition = () => {
        const vv = window.visualViewport
        const inputWrapper = document.getElementById('input-wrapper')
        const topbar = document.getElementById('mobile-topbar')
        const sidebar = document.getElementById('sidebar')
        const overlay = document.querySelector('.mobile-overlay')

        if (window.innerWidth > 768) {
          if (inputWrapper) inputWrapper.style.bottom = ''
          if (topbar) topbar.style.top = ''
          if (messagesEl) {
            messagesEl.style.top = ''
            messagesEl.style.paddingBottom = ''
          }
          if (sidebar) sidebar.style.top = ''
          if (overlay) overlay.style.top = ''
          return
        }

        if (!vv || !inputWrapper) return

        const viewportTop = Math.max(0, vv.offsetTop)
        const keyboardHeight = Math.max(0, window.innerHeight - viewportTop - vv.height)

        inputWrapper.style.bottom = keyboardHeight + 'px'

        if (topbar) {
          topbar.style.top = viewportTop + 'px'
        }

        const topbarH = topbar ? topbar.offsetHeight : 52
        const wrapperHeight = inputWrapper.offsetHeight

        if (messagesEl) {
          messagesEl.style.top = (topbarH + viewportTop) + 'px'
          messagesEl.style.paddingBottom = (wrapperHeight + 8) + 'px'
          if (keyboardHeight > 50) {
            messagesEl.scrollTop = messagesEl.scrollHeight
          }
        }

        if (sidebar) sidebar.style.top = (topbarH + viewportTop) + 'px'
        if (overlay) overlay.style.top = (topbarH + viewportTop) + 'px'
      }

      window.visualViewport.addEventListener('resize', syncInputPosition)
      window.visualViewport.addEventListener('scroll', syncInputPosition)
      window.addEventListener('resize', syncInputPosition)
      syncInputPosition()
    }

    const ta = document.querySelector('#input-area textarea')
    if (ta) {
      ta.addEventListener('focus', () => {
        if (window.innerWidth > 768) return
        setTimeout(() => {
          if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight
        }, 350)
      })
    }
  })
}
