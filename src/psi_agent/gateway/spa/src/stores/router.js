import { defineStore } from 'pinia'
import { ref } from 'vue'

function emptyForm() {
  return {
    name: '', router_ai_id: '', upstreams: [], default_ai_id: '',
    router_timeout: null, router_context_chars: 12000,
  }
}

export const useRouterStore = defineStore('router', () => {
  const routers = ref([])
  const routerForm = ref(emptyForm())
  const resetRouterForm = () => { routerForm.value = emptyForm() }
  return { routers, routerForm, resetRouterForm }
})
