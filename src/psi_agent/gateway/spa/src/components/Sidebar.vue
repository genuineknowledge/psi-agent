<template>
  <div id="sidebar" :class="{ collapsed: isSidebarCollapsed, 'mobile-open': isMobileSidebarOpen }">
    <div class="col">
      <div class="sb-top">
        <div class="sb-header">
          <div class="sb-brand">
            <div class="sb-logo"></div>
            <span class="sb-brand-name">HaiTun</span>
          </div>
        </div>
        <button class="open-workspace" @click="$emit('open-workspace')">
          <span class="material-symbols-outlined">folder_open</span>
          <span class="label">打开工作区</span>
        </button>
        <div class="session-search">
          <span class="material-symbols-outlined">search</span>
          <input
            ref="searchInputRef"
            v-model="sessionSearchText"
            type="search"
            placeholder="搜索工作区或会话"
            aria-label="搜索工作区或会话"
          >
          <span v-if="!sessionSearchText" class="shortcut search-shortcut">Ctrl+Shift+K</span>
          <button
            v-if="sessionSearchText"
            class="clear-search"
            type="button"
            title="清空搜索"
            @click="sessionSearchText = ''"
          >
            <span class="material-symbols-outlined">close</span>
          </button>
        </div>
      </div>
      <div class="sb-scroll">
        <div class="recent-label">工作区</div>
        <div v-if="workspaceGroups.length === 0" class="session-empty">
          点击「打开工作区」添加文件夹
        </div>
        <div v-for="group in workspaceGroups" :key="group.path" class="ws-group">
          <div
            class="ws-header item"
            :class="{
              selected: group.path === selectedWorkspacePath && !selectedSessionId,
            }"
            @click="onSelectWorkspace(group.path)"
          >
            <button
              class="ws-toggle"
              type="button"
              :title="isWorkspaceExpanded(group.path) ? '折叠' : '展开'"
              @click.stop="toggleWorkspaceExpanded(group.path)"
            >
              <span class="material-symbols-outlined">
                {{ isWorkspaceExpanded(group.path) ? 'expand_more' : 'chevron_right' }}
              </span>
            </button>
            <span class="material-symbols-outlined ws-folder">folder</span>
            <span class="info">
              <div class="name ws-name" :title="group.path">{{ group.label }}</div>
            </span>
            <button
              class="ws-add"
              type="button"
              title="在此工作区发起新对话"
              @click.stop="onNewSession(group.path)"
            >
              <span class="material-symbols-outlined">add</span>
            </button>
            <button
              class="del ws-remove"
              type="button"
              title="从列表移除工作区"
              @click.stop="confirmRemoveWorkspace(group)"
            >
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>
          <div v-show="isWorkspaceExpanded(group.path)" class="ws-sessions">
            <div
              v-for="s in group.sessions"
              :key="s.id"
              class="item session-item"
              :class="{ selected: s.id === selectedSessionId }"
              @click="selectSession(s.id)"
            >
              <button
                class="pin"
                :class="{ pinned: isSessionPinned(s.id) }"
                @click.stop="toggleSessionPin(s.id)"
                :title="isSessionPinned(s.id) ? '取消置顶' : '置顶会话'"
              >
                <span class="material-symbols-outlined">keep</span>
              </button>
              <span class="info">
                <input
                  v-if="editingSessionId === s.id"
                  v-model="editingWorkspaceText"
                  class="edit-input"
                  v-focus
                  @blur="saveSessionTitle(s)"
                  @keydown.enter="saveSessionTitle(s)"
                  @click.stop
                >
                <div
                  v-else
                  class="name"
                  :title="displaySessionName(s)"
                  @dblclick.stop="startEditTitle(s)"
                >
                  {{ displaySessionName(s) }}
                </div>
              </span>
              <button class="del" type="button" @click.stop="confirmDeleteSession(s.id)">
                <span class="material-symbols-outlined">delete</span>
              </button>
            </div>
            <div
              v-if="group.sessions.length === 0 && !sessionSearchText.trim()"
              class="ws-empty-sessions"
            >
              暂无会话
            </div>
            <button
              v-if="!sessionSearchText.trim()"
              class="ws-new-session"
              type="button"
              title="在此工作区发起新对话"
              @click.stop="onNewSession(group.path)"
            >
              <span class="material-symbols-outlined">add</span>
              <span>新对话</span>
            </button>
          </div>
        </div>
        <div
          v-if="sessions.length && workspaceGroups.length === 0 && sessionSearchText.trim()"
          class="session-empty"
        >
          没有匹配的工作区或会话
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, watch, ref, nextTick } from 'vue'
import { storeToRefs } from 'pinia'
import { useSessionStore } from '../stores/session.js'
import { useUiStore } from '../stores/ui.js'
import { api } from '../api.js'
import { selectSession, selectWorkspace } from '../composables/useSession.js'
import {
  buildSessionTitlePayload,
  buildWorkspaceGroups,
  getSessionDisplayName,
  savePinnedSessionIds,
  sessionsForWorkspace,
  togglePinnedSessionId,
} from '../sessionList.js'

const session = useSessionStore()
const {
  sessions,
  selectedSessionId,
  selectedWorkspacePath,
  gatewayCwd,
  registeredWorkspaces,
  sessionTitles,
  editingSessionId,
  editingWorkspaceText,
  sessionSearchText,
  pinnedSessionIds,
} = storeToRefs(session)

const ui = useUiStore()
const { isSidebarCollapsed, isMobileSidebarOpen, dlgConfirm, sessionSearchFocusToken } = storeToRefs(ui)

const emit = defineEmits(['new-session', 'open-workspace'])

function onNewSession(workspacePath) {
  emit('new-session', workspacePath)
}

const searchInputRef = ref(null)
watch(sessionSearchFocusToken, () => {
  nextTick(() => searchInputRef.value?.focus())
})

const workspaceGroups = computed(() => buildWorkspaceGroups(sessions.value, {
  registered: registeredWorkspaces.value,
  defaultCwd: gatewayCwd.value,
  titles: sessionTitles.value,
  query: sessionSearchText.value,
  pinnedIds: pinnedSessionIds.value,
}))

function displaySessionName(sess) {
  return getSessionDisplayName(sess, sessionTitles.value)
}

function isSessionPinned(id) {
  return pinnedSessionIds.value.includes(id)
}

function toggleSessionPin(id) {
  pinnedSessionIds.value = togglePinnedSessionId(pinnedSessionIds.value, id)
  savePinnedSessionIds(window.localStorage, pinnedSessionIds.value)
}

function isWorkspaceExpanded(path) {
  return session.isWorkspaceExpanded(path)
}

function toggleWorkspaceExpanded(path) {
  session.toggleWorkspaceExpanded(path)
}

function onSelectWorkspace(path) {
  selectWorkspace(path)
}

function startEditTitle(sess) {
  editingSessionId.value = sess.id
  editingWorkspaceText.value = displaySessionName(sess)
}

async function saveSessionTitle(sess) {
  if (editingSessionId.value === null) return
  const targetText = editingWorkspaceText.value.trim()
  const oldText = displaySessionName(sess)
  editingSessionId.value = null

  if (!targetText || targetText === oldText) return
  try {
    await api('POST', '/titles', buildSessionTitlePayload(sess, targetText))
    sessionTitles.value[sess.id] = targetText
  } catch (e) {
    ui.showAlert('更新会话名称失败: ' + e.message)
  }
}

function confirmDeleteSession(id) {
  dlgConfirm.value.message = `确认删除会话 ${id}? 删除后将无法恢复。`
  dlgConfirm.value.actionType = 'session'
  dlgConfirm.value.actionArgs = id
  dlgConfirm.value.show = true
}

function confirmRemoveWorkspace(group) {
  const count = sessionsForWorkspace(sessions.value, group.path, gatewayCwd.value).length
  if (count > 0) {
    dlgConfirm.value.message =
      `工作区「${group.label}」下有 ${count} 个会话。确认移除工作区并删除其下全部会话？`
    dlgConfirm.value.actionType = 'workspace'
    dlgConfirm.value.actionArgs = group.path
  } else {
    dlgConfirm.value.message = `确认从列表移除工作区「${group.label}」？`
    dlgConfirm.value.actionType = 'workspace-remove'
    dlgConfirm.value.actionArgs = group.path
  }
  dlgConfirm.value.show = true
}

watch(
  () => sessions.value.map(sess => sess.id),
  (sessionIds) => {
    if (!sessionIds.length) return
    const activeIds = new Set(sessionIds)
    const prunedPinnedIds = pinnedSessionIds.value.filter(id => activeIds.has(id))
    if (prunedPinnedIds.length === pinnedSessionIds.value.length) return
    pinnedSessionIds.value = prunedPinnedIds
    savePinnedSessionIds(window.localStorage, prunedPinnedIds)
  },
  { immediate: true },
)
</script>

<style scoped>
#sidebar {
  width: 280px;
  display: flex;
  background: var(--md-surface-container);
  border-right: 1px solid var(--md-outline-variant);
  flex-shrink: 0;
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1), background 0.25s, border-color 0.25s;
  overflow: hidden;
}
#sidebar.collapsed {
  width: 0;
  border-right: 0 solid transparent;
}
#sidebar .col {
  width: 280px;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}
.sb-top { flex-shrink: 0; }
.sb-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  -webkit-mask-image: linear-gradient(to bottom,
    transparent 0, #000 12px, #000 calc(100% - 12px), transparent 100%);
          mask-image: linear-gradient(to bottom,
    transparent 0, #000 12px, #000 calc(100% - 12px), transparent 100%);
}
.sb-scroll {
  scrollbar-width: thin;
  scrollbar-color: transparent transparent;
}
.sb-scroll:hover { scrollbar-color: var(--md-outline-variant) transparent; }
.sb-scroll::-webkit-scrollbar { width: 6px; }
.sb-scroll::-webkit-scrollbar-track { background: transparent; }
.sb-scroll::-webkit-scrollbar-thumb { background: transparent; border-radius: 10px; transition: background 0.2s; }
.sb-scroll:hover::-webkit-scrollbar-thumb { background: var(--md-outline-variant); }
.sb-header { display: flex; align-items: center; padding: 12px 12px 8px; }
.sb-brand { display: flex; align-items: center; gap: 10px; }
.sb-logo {
  width: 26px; height: 26px; border-radius: var(--md-shape-full);
  background-image: url('/spa/haitun-logo.png');
  background-size: cover;
  background-position: center;
}
.sb-brand-name { font-size: 20px; font-weight: 500; color: var(--md-text-primary); }
.open-workspace {
  display: flex; align-items: center; gap: 12px;
  width: calc(100% - 16px); box-sizing: border-box;
  margin: 0 8px 4px; padding: 10px 16px;
  background: transparent; color: var(--md-text-secondary);
  border: none; border-radius: var(--md-shape-full); cursor: pointer;
  font-size: 14px; text-align: left; transition: background 0.2s;
}
.open-workspace:hover { background: var(--md-nav-hover); }
.open-workspace .material-symbols-outlined { font-size: 20px; flex-shrink: 0; }
.recent-label {
  padding: 12px 16px 6px; font-size: 13px; color: var(--md-text-secondary);
}
.session-search {
  margin: 0 8px 8px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: transparent;
  border: none;
  border-radius: var(--md-shape-full);
  color: var(--md-text-secondary);
  transition: background 0.2s;
}
.session-search:hover,
.session-search:focus-within {
  background: var(--md-nav-hover);
}
.session-search .material-symbols-outlined { font-size: 20px; flex-shrink: 0; }
.session-search input {
  min-width: 0;
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  color: var(--md-text-primary);
  font: inherit;
  font-size: 14px;
}
.session-search input::placeholder { color: var(--md-text-secondary); }
.session-search input::-webkit-search-cancel-button { appearance: none; }
.session-search .clear-search {
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--md-text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
}
.session-search .clear-search:hover {
  background: rgba(128,128,128,var(--md-state-hover));
  color: var(--md-primary);
}
.ws-group { margin-bottom: 2px; }
.ws-header { padding-left: 4px; }
.ws-toggle {
  color: var(--md-text-secondary);
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.ws-toggle .material-symbols-outlined { font-size: 20px; }
.ws-folder {
  font-size: 18px;
  color: var(--md-primary);
  flex-shrink: 0;
}
.ws-name { font-weight: 600; }
.ws-add {
  color: var(--md-text-secondary);
  background: none;
  border: none;
  cursor: pointer;
  padding: 6px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  visibility: hidden;
  flex-shrink: 0;
  transition: all 0.2s;
}
.ws-add .material-symbols-outlined { font-size: 18px; }
.ws-header:hover .ws-add,
.ws-header .ws-add:focus-visible { visibility: visible; }
.ws-add:hover {
  background: rgba(128,128,128,var(--md-state-hover));
  color: var(--md-primary);
}
.ws-remove { visibility: visible; opacity: 0.5; }
.ws-header:hover .ws-remove { opacity: 1; }
.ws-sessions { padding-left: 12px; }
.ws-new-session {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 2px 8px 6px 16px;
  padding: 6px 12px;
  border: none;
  border-radius: var(--md-shape-full);
  background: transparent;
  color: var(--md-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.2s;
}
.ws-new-session .material-symbols-outlined { font-size: 18px; }
.ws-new-session:hover {
  background: var(--md-nav-hover);
  color: var(--md-primary);
}
.session-item { margin-left: 4px; }
.ws-empty-sessions {
  margin: 4px 16px 8px 24px;
  font-size: 12px;
  color: var(--md-text-secondary);
}
.item {
  padding: 8px 8px;
  border-radius: var(--md-shape-full);
  cursor: pointer;
  font-size: 13px;
  margin: 0 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: background 0.2s;
}
.item:hover { background: var(--md-nav-hover); }
.item.selected { background: var(--md-nav-hover); }
.item .pin {
  color: var(--md-text-secondary);
  background: none;
  border: none;
  cursor: pointer;
  padding: 6px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  visibility: hidden;
  transition: all 0.2s;
  flex-shrink: 0;
}
.item:hover .pin,
.item .pin.pinned { visibility: visible; }
.item .pin:hover {
  background: rgba(128,128,128,var(--md-state-hover));
  color: var(--md-primary);
}
.item .pin.pinned {
  color: var(--md-primary);
  font-variation-settings: 'FILL' 1;
}
.item .pin .material-symbols-outlined { font-size: 18px; }
.item .info { flex: 1; overflow: hidden; display: flex; align-items: center; min-width: 0; }
.item .info .name {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl;
  text-align: left;
  width: 100%;
}
.item .info .edit-input {
  width: 100%;
  background: var(--md-bg);
  color: var(--md-text-primary);
  border: 1px solid var(--md-primary);
  border-radius: 6px;
  padding: 2px 6px;
  font-size: 13px;
  outline: none;
}
.item .del {
  color: var(--md-text-secondary);
  background: none;
  border: none;
  cursor: pointer;
  padding: 6px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  visibility: hidden;
  transition: all 0.2s;
}
.item:hover .del { visibility: visible; }
.item .del:hover {
  background: rgba(255,180,171,0.15);
  color: var(--md-text-error);
}
.item .del .material-symbols-outlined { font-size: 18px; }
.session-empty {
  margin: 20px 12px;
  color: var(--md-text-secondary);
  font-size: 13px;
  text-align: center;
}
.shortcut {
  margin-left: auto;
  font-size: 12px;
  color: var(--md-text-secondary);
  opacity: 0;
  transition: opacity 0.15s;
  white-space: nowrap;
  flex-shrink: 0;
}
.open-workspace:hover .shortcut,
.session-search:hover .shortcut,
.session-search:focus-within .shortcut { opacity: 1; }
.search-shortcut { margin-left: 4px; }

@media (hover: none) {
  .item .del,
  .item .pin { visibility: visible; }
  .ws-add { visibility: visible; }
  .ws-remove { opacity: 1; }
}

@media (max-width: 768px) {
  #sidebar {
    position: fixed;
    left: 0; top: 52px; bottom: 0;
    z-index: 28;
    width: 280px !important;
    transform: translateX(-100%);
    transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.3s;
    border-right: 1px solid var(--md-outline-variant);
    box-shadow: none;
  }
  #sidebar.mobile-open {
    transform: translateX(0);
    box-shadow: 4px 0 24px rgba(0,0,0,0.3);
  }
  #sidebar.collapsed {
    transform: translateX(-100%);
    width: 280px !important;
  }
}
</style>
