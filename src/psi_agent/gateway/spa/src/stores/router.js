import { defineStore } from 'pinia'
import { ref } from 'vue'

function emptyForm() {
  return {
    name: '', mode: 'routing', router_ai_id: '', upstreams: [], default_ai_id: '',
    router_timeout: null, max_context_length: 12000,
  }
}

export const useRouterStore = defineStore('router', () => {
  const routers = ref([])
  const routerForm = ref(emptyForm())
  const resetRouterForm = () => { routerForm.value = emptyForm() }
  return { routers, routerForm, resetRouterForm }
})
