import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useRouterStore } from './router.js'

describe('router store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('resets to the current seven-field Router form', () => {
    const store = useRouterStore()
    store.routerForm.default_ai_id = 'legacy'
    store.routerForm.max_context_length = 1
    store.resetRouterForm()

    expect(store.routerForm).toEqual({
      name: '',
      mode: 'routing',
      router_ai_id: '',
      upstreams: [],
      router_timeout: null,
      target_timeout: null,
      max_context_chars: 12000,
    })
  })
})
