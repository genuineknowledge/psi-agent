import { reactive } from 'vue'

import { loadPinnedSessionIds } from './sessionList.js'

export const store = reactive({
  loadingEnv: true,
  ais: [],
  sessions: [],
  selectedAiId: null,
  selectedSessionId: null,
  sessionTitles: {},
  messages: [],
  sessionMessages: {},
  sessionInputs: {},
  inputText: '',
  streaming: false,
  selectedFiles: [],
  dlgAI: false,
  dlgSess: false,
  browser: { path: undefined, parent: '', entries: [] },
  snackbar: { show: false, message: '' },
  dlgConfirm: { show: false, message: '', actionArgs: null, actionType: 'session' },
  aiForm: { provider: 'deepseek', base_url: 'https://api.deepseek.com/v1', api_key: '', model: '' },
  sessForm: { workspace: '' },
  isLightMode: true,
  fetchedModels: [],
  loadingModels: false,
  isSidebarCollapsed: false,
  isMobileSidebarOpen: false,
  modelPanelOpen: false,
  editingSessionId: null,
  editingWorkspaceText: '',
  sessionSearchText: '',
  pinnedSessionIds: loadPinnedSessionIds(window.localStorage),
  userHasScrolledUp: false,
  uploadResetToken: 0,
  isDragging: false,
})

export function useStore() { return store }
