<template>
  <div id="sidebar" :class="{ collapsed: isSidebarCollapsed, 'mobile-open': isMobileSidebarOpen }">
    <div class="col">
      <div class="sb-header">
        <div class="sb-brand">
          <div class="sb-logo"></div>
          <span class="sb-brand-name">Dolphin</span>
        </div>
      </div>
      <button class="new-chat" @click="$emit('new-session')">
        <span class="material-symbols-outlined">edit_square</span>
        <span>发起新对话</span>
      </button>
      <div class="session-search">
        <span class="material-symbols-outlined">search</span>
        <input
          v-model="sessionSearchText"
          type="search"
          placeholder="搜索会话"
          aria-label="搜索会话"
        >
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
      <div class="recent-label">最近</div>
      <div
        v-for="s in visibleSessions"
        :key="s.id"
        class="item"
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
      <div v-if="sessions.length && visibleSessions.length === 0" class="session-empty">
        没有匹配的会话
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useSessionStore } from '../stores/session.js'
import { useUiStore } from '../stores/ui.js'
import { api } from '../api.js'
import { selectSession } from '../composables/useSession.js'
import {
  buildSessionTitlePayload,
  buildVisibleSessions,
  getSessionDisplayName,
  savePinnedSessionIds,
  togglePinnedSessionId,
} from '../sessionList.js'

const session = useSessionStore()
const {
  sessions,
  selectedSessionId,
  sessionTitles,
  editingSessionId,
  editingWorkspaceText,
  sessionSearchText,
  pinnedSessionIds,
} = storeToRefs(session)

const ui = useUiStore()
const { isSidebarCollapsed, isMobileSidebarOpen, dlgConfirm } = storeToRefs(ui)

defineEmits(['new-session'])

const visibleSessions = computed(() => buildVisibleSessions(sessions.value, {
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

function startEditWorkspace(sess) {
  editingSessionId.value = sess.id
  editingWorkspaceText.value = displaySessionName(sess)
}

async function saveSessionWorkspace(sess) {
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
.sb-header { display: flex; align-items: center; padding: 12px 12px 8px; }
.sb-brand { display: flex; align-items: center; gap: 10px; }
.sb-logo {
  width: 26px; height: 26px; border-radius: var(--md-shape-full);
  background: var(--g-spark);
}
.sb-brand-name { font-size: 20px; font-weight: 500; color: var(--md-text-primary); }
.new-chat {
  display: inline-flex; align-items: center; gap: 12px;
  margin: 4px 12px 8px; padding: 10px 16px;
  background: var(--md-surface-container-high); color: var(--md-text-secondary);
  border: none; border-radius: var(--md-shape-full); cursor: pointer;
  font-size: 14px; align-self: flex-start; transition: background 0.2s;
}
.new-chat:hover { background: var(--md-surface-variant); }
.new-chat .material-symbols-outlined { font-size: 20px; }
.recent-label {
  padding: 12px 16px 6px; font-size: 13px; color: var(--md-text-secondary);
}
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
  border-radius: var(--md-shape-full);
  cursor: pointer;
  font-size: 13px;
  margin: 0 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: background 0.2s;
}
.item:hover { background: var(--md-surface-container-high); }
.item.selected { background: var(--md-surface-container-high); }
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
