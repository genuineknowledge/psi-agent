<template>
  <div id="sidebar" :class="{ collapsed: store.isSidebarCollapsed, 'mobile-open': store.isMobileSidebarOpen }">
    <div class="col">
      <div class="col-header">
        会话
        <button @click="$emit('new-session')">
          <span class="material-symbols-outlined">add</span>
          新建
        </button>
      </div>
      <div class="session-search">
        <span class="material-symbols-outlined">search</span>
        <input
          v-model="store.sessionSearchText"
          type="search"
          placeholder="搜索会话"
          aria-label="搜索会话"
        >
        <button
          v-if="store.sessionSearchText"
          class="clear-search"
          type="button"
          title="清空搜索"
          @click="store.sessionSearchText = ''"
        >
          <span class="material-symbols-outlined">close</span>
        </button>
      </div>
      <div
        v-for="s in visibleSessions"
        :key="s.id"
        class="item"
        :class="{ selected: s.id === store.selectedSessionId }"
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
            v-if="store.editingSessionId === s.id"
            v-model="store.editingWorkspaceText"
            class="edit-input"
            v-focus
            @blur="saveSessionWorkspace(s)"
            @keydown.enter="saveSessionWorkspace(s)"
            @click.stop
          >
          <div
            v-else
            class="name"
            :title="displaySessionName(s)"
            @dblclick.stop="startEditWorkspace(s)"
          >
            {{ displaySessionName(s) }}
          </div>
        </span>
        <button class="del" @click.stop="confirmDeleteSession(s.id)">
          <span class="material-symbols-outlined">delete</span>
        </button>
      </div>
      <div v-if="store.sessions.length && visibleSessions.length === 0" class="session-empty">
        没有匹配的会话
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'
import { store } from '../store.js'
import { api } from '../api.js'
import { selectSession } from '../composables/useSession.js'
import {
  buildSessionTitlePayload,
  buildVisibleSessions,
  getSessionDisplayName,
  savePinnedSessionIds,
  togglePinnedSessionId,
} from '../sessionList.js'

defineEmits(['new-session'])

const visibleSessions = computed(() => buildVisibleSessions(store.sessions, {
  titles: store.sessionTitles,
  query: store.sessionSearchText,
  pinnedIds: store.pinnedSessionIds,
}))

function displaySessionName(session) {
  return getSessionDisplayName(session, store.sessionTitles)
}

function isSessionPinned(id) {
  return store.pinnedSessionIds.includes(id)
}

function toggleSessionPin(id) {
  store.pinnedSessionIds = togglePinnedSessionId(store.pinnedSessionIds, id)
  savePinnedSessionIds(window.localStorage, store.pinnedSessionIds)
}

function startEditWorkspace(session) {
  store.editingSessionId = session.id
  store.editingWorkspaceText = displaySessionName(session)
}

async function saveSessionWorkspace(session) {
  if (store.editingSessionId === null) return
  const targetText = store.editingWorkspaceText.trim()
  const oldText = displaySessionName(session)
  store.editingSessionId = null

  if (!targetText || targetText === oldText) return
  try {
    await api('POST', '/titles', buildSessionTitlePayload(session, targetText))
    store.sessionTitles[session.id] = targetText
  } catch (e) {
    store.snackbar.message = '更新会话名称失败: ' + e.message
    store.snackbar.show = true
    setTimeout(() => { if (store.snackbar.message.includes('更新会话名称失败')) store.snackbar.show = false }, 4000)
  }
}

function confirmDeleteSession(id) {
  store.dlgConfirm.message = `确认删除会话 ${id}? 删除后将无法恢复。`
  store.dlgConfirm.actionType = 'session'
  store.dlgConfirm.actionArgs = id
  store.dlgConfirm.show = true
}

watch(
  () => store.sessions.map(session => session.id),
  (sessionIds) => {
    if (!sessionIds.length) return
    const activeIds = new Set(sessionIds)
    const prunedPinnedIds = store.pinnedSessionIds.filter(id => activeIds.has(id))
    if (prunedPinnedIds.length === store.pinnedSessionIds.length) return
    store.pinnedSessionIds = prunedPinnedIds
    savePinnedSessionIds(window.localStorage, prunedPinnedIds)
  },
  { immediate: true },
)

</script>

<style scoped>
#sidebar {
  width: 280px;
  display: flex;
  background: var(--md-surface);
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
  overflow-y: auto;
}
.col-header {
  font-size: 14px;
  font-weight: 500;
  letter-spacing: .5px;
  color: var(--md-primary);
  padding: 16px 16px 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.col-header button {
  background: var(--md-primary-container);
  color: var(--md-on-primary-container);
  border: none;
  border-radius: var(--md-shape-full);
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
  transition: box-shadow .2s;
  box-shadow: var(--md-elevation-1);
}
.col-header button:hover { box-shadow: var(--md-elevation-2); filter: brightness(1.05); }
.col-header button .material-symbols-outlined { font-size: 16px; }
.session-search {
  margin: 0 12px 8px;
  height: 38px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 8px 0 12px;
  background: var(--md-surface-container-high);
  border: 1px solid var(--md-outline-variant);
  border-radius: 8px;
  color: var(--md-text-secondary);
}
.session-search .material-symbols-outlined { font-size: 18px; flex-shrink: 0; }
.session-search input {
  min-width: 0;
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  color: var(--md-text-primary);
  font: inherit;
  font-size: 13px;
}
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
.item {
  padding: 8px 8px;
  border-radius: 12px;
  cursor: pointer;
  font-size: 13px;
  margin: 4px 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: background 0.2s;
}
.item:hover { background: var(--md-surface-variant); }
.item.selected {
  background: var(--md-secondary-container);
  color: var(--md-on-secondary-container);
}
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
.item .info { flex: 1; overflow: hidden; display: flex; align-items: center; }
.item .info .name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl; text-align: left; width: 100%; }
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

@media (hover: none) {
  .item .del,
  .item .pin { visibility: visible; }
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
