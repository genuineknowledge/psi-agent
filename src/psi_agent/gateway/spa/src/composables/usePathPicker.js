import { reactive } from 'vue'
import { browseWorkspace, fetchWorkspacePlaces } from '../api.js'
import { filterPickerEntries } from '../pathPicker.js'
import { normalizeWorkspacePath } from '../sessionList.js'

const LS_LAST_BROWSE = 'gw-last-browse-path'

const state = reactive({
  visible: false,
  loading: false,
  mode: 'directory',
  title: '选择文件夹',
  confirmLabel: '选择文件夹',
  hint: '',
  currentPath: '',
  selectedPath: '',
  parent: '',
  places: [],
  drives: [],
  entries: [],
  segments: [],
  filterText: '',
  error: '',
})

let resolveFn = null

function browseKind(mode) {
  return mode === 'directory' ? 'directory' : 'all'
}

function applyBrowse(data) {
  state.currentPath = data.path || ''
  state.selectedPath = data.path || ''
  state.parent = data.parent || ''
  state.segments = Array.isArray(data.segments) ? data.segments : []
  state.entries = Array.isArray(data.entries) ? data.entries : []
}

async function loadBrowse(path) {
  const data = await browseWorkspace(path, { kind: browseKind(state.mode) })
  applyBrowse(data)
}

export function usePathPickerState() {
  return state
}

export function visibleEntries() {
  return filterPickerEntries(state.entries, state.filterText)
}

export async function openPathPicker(options = {}) {
  if (state.visible && resolveFn) {
    resolveFn(null)
    resolveFn = null
  }
  return new Promise((resolve) => {
    resolveFn = resolve
    state.visible = true
    state.loading = true
    state.mode = options.mode || 'directory'
    state.title = options.title || '选择文件夹'
    state.confirmLabel = options.confirmLabel || '选择文件夹'
    state.hint = options.hint || ''
    state.filterText = ''
    state.error = ''
    const initial = normalizeWorkspacePath(
      options.initialPath
      || (typeof localStorage !== 'undefined' ? localStorage.getItem(LS_LAST_BROWSE) : '')
      || '',
    )
    void bootstrap(initial)
  })
}

async function bootstrap(initialPath) {
  state.loading = true
  state.error = ''
  try {
    const placesData = await fetchWorkspacePlaces()
    state.places = Array.isArray(placesData.places) ? placesData.places : []
    state.drives = Array.isArray(placesData.drives) ? placesData.drives : []
    await loadBrowse(initialPath || undefined)
  } catch (e) {
    state.error = e instanceof Error ? e.message : String(e)
  } finally {
    state.loading = false
  }
}

export async function navigatePathPicker(path) {
  const target = normalizeWorkspacePath(path)
  if (!target) return
  state.loading = true
  state.error = ''
  try {
    await loadBrowse(target)
  } catch (e) {
    state.error = e instanceof Error ? e.message : String(e)
  } finally {
    state.loading = false
  }
}

export async function goParentPathPicker() {
  const parent = normalizeWorkspacePath(state.parent)
  const current = normalizeWorkspacePath(state.currentPath)
  if (!parent || parent === current) return
  await navigatePathPicker(parent)
}

export function selectPathPickerEntry(entry) {
  if (!entry?.path) return
  state.selectedPath = entry.path
}

export async function enterPathPickerEntry(entry) {
  if (!entry?.path || entry.kind !== 'directory') return
  await navigatePathPicker(entry.path)
}

export function confirmPathPicker() {
  const path = normalizeWorkspacePath(state.selectedPath || state.currentPath)
  if (!path) return
  try {
    localStorage.setItem(LS_LAST_BROWSE, path)
  } catch (_) {}
  closePathPicker(path)
}

export function cancelPathPicker() {
  closePathPicker(null)
}

function closePathPicker(result) {
  state.visible = false
  if (resolveFn) {
    resolveFn(result)
    resolveFn = null
  }
}
