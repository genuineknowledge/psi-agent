import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useSessionStore } from '../stores/session.js'
import { useUiStore } from '../stores/ui.js'
import { normalizeWorkspacePath } from '../sessionList.js'

export const MainView = {
  LOADING: 'loading',
  NO_WORKSPACE: 'no-workspace',
  NO_SESSION: 'no-session',
  CHAT: 'chat',
}

/** Pure derivation — no side effects. Exported for unit tests. */
export function computeMainView({
  loadingEnv,
  selectedWorkspacePath,
  draftSession,
  selectedSessionId,
}) {
  if (loadingEnv) return MainView.LOADING
  if (!normalizeWorkspacePath(selectedWorkspacePath)) return MainView.NO_WORKSPACE
  if (draftSession || selectedSessionId) return MainView.CHAT
  return MainView.NO_SESSION
}

export function useMainView() {
  const session = useSessionStore()
  const ui = useUiStore()
  const { loadingEnv } = storeToRefs(ui)
  const { selectedWorkspacePath, draftSession, selectedSessionId } = storeToRefs(session)

  const mainView = computed(() =>
    computeMainView({
      loadingEnv: loadingEnv.value,
      selectedWorkspacePath: selectedWorkspacePath.value,
      draftSession: draftSession.value,
      selectedSessionId: selectedSessionId.value,
    }),
  )

  const isChatActive = computed(() => mainView.value === MainView.CHAT)

  return { mainView, isChatActive, MainView }
}
