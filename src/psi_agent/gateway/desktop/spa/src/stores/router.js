import { defineStore } from 'pinia'
import { ref } from 'vue'

function emptyForm() {
  return {
    name: '',
    mode: 'routing',
    router_ai_id: '',
    upstreams: [],
    router_timeout: null,
    target_timeout: null,
    max_context_chars: 12000,
  }
}

export const useRouterStore = defineStore('router', () => {
  const routers = ref([])
  const routerForm = ref(emptyForm())
  const resetRouterForm = () => { routerForm.value = emptyForm() }
  return { routers, routerForm, resetRouterForm }
})
