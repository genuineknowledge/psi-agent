<template>
  <div id="input-wrapper">
    <div id="file-preview-bar" v-if="selectedFiles.length">
      <div class="preview-chip" v-for="(f, i) in selectedFiles" :key="i">
        <span class="material-symbols-outlined" style="font-size:16px;">attach_file</span>
        <span>{{ f.name }}</span>
        <button class="close-btn" @click="selectedFiles.splice(i, 1)" title="移除附件">
          <span class="material-symbols-outlined" style="font-size:16px;">close</span>
        </button>
      </div>
    </div>

    <div id="input-area">
      <div
        v-if="workflowMenuOpen"
        id="workflow-command-menu"
        class="workflow-menu"
        role="listbox"
        aria-label="可复用工作流"
      >
        <div v-if="loadingWorkflows" class="workflow-menu-state">正在加载工作流…</div>
        <div v-else-if="workflowLoadError" class="workflow-menu-state error">
          {{ workflowLoadError }}
        </div>
        <div v-else-if="!workflowOptions.length" class="workflow-menu-state">
          没有匹配的工作流
        </div>
        <template v-else>
          <button
            v-for="(workflow, index) in workflowOptions"
            :id="`workflow-option-${index}`"
            :key="workflow.name"
            type="button"
            class="workflow-option"
            :class="{ active: index === activeWorkflowIndex }"
            role="option"
            :aria-selected="index === activeWorkflowIndex"
            @mouseenter="activeWorkflowIndex = index"
            @mousedown.prevent="selectWorkflow(workflow)"
          >
            <span class="workflow-option-heading">
              <span class="workflow-command">/workflow:{{ workflow.name }}</span>
            </span>
          </button>
        </template>
      </div>

      <label class="btn" for="file-upload"><span class="material-symbols-outlined">attach_file</span></label>
      <input type="file" id="file-upload" multiple @change="onFileSelected">

      <textarea
        id="chat-input"
        v-model="inputText"
        rows="1"
        placeholder="问问 HaiTun"
        aria-autocomplete="list"
        :aria-expanded="workflowMenuOpen"
        :aria-controls="workflowMenuOpen ? 'workflow-command-menu' : undefined"
        :aria-activedescendant="activeWorkflowDescendant"
        @focus="restoreWorkflowMenu"
        @blur="dismissWorkflowMenu"
        @keydown="handleInputKeydown"
        @input="autoResizeInput"
      ></textarea>

      <ModelPanel
        @select-backend="$emit('select-backend', $event)"
        @delete-ai="$emit('delete-ai', $event)"
        @delete-router="$emit('delete-router', $event)"
      />

      <button v-if="streaming" class="send stop" @click="stopMessage" title="停止生成">
        <span class="material-symbols-outlined">stop</span>
      </button>
      <button v-else class="send" @click="submitMessage" title="发送消息">
        <span class="material-symbols-outlined">send</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { listWorkspaceWorkflows } from '../api.js'
import { useChatStore } from '../stores/chat.js'
import { useSessionStore } from '../stores/session.js'
import { useUiStore } from '../stores/ui.js'
import { sendMessage, stopMessage } from '../composables/useChat.js'
import { resolveSessionWorkspace } from '../sessionList.js'
import {
  filterWorkflowOptions,
  formatWorkflowCommand,
  getWorkflowCommandQuery,
  moveWorkflowSelection,
  parseWorkflowCommand,
} from '../workflowCommands.js'
import ModelPanel from './ModelPanel.vue'

const chat = useChatStore()
const { selectedFiles, inputText, uploadResetToken, streaming } = storeToRefs(chat)
const session = useSessionStore()
const {
  draftSession,
  gatewayCwd,
  selectedSessionId,
  selectedWorkspacePath,
  sessions,
} = storeToRefs(session)
const ui = useUiStore()

const workflows = ref([])
const loadingWorkflows = ref(false)
const workflowLoadError = ref('')
const activeWorkflowIndex = ref(0)
const dismissedWorkflowText = ref(null)
let workflowRequestVersion = 0

defineEmits(['select-backend', 'delete-ai', 'delete-router'])

const activeWorkspacePath = computed(() => {
  if (draftSession.value?.workspace) return draftSession.value.workspace
  if (selectedSessionId.value) {
    const selectedSession = sessions.value.find(item => item.id === selectedSessionId.value)
    if (!selectedSession) return ''
    return resolveSessionWorkspace(selectedSession, gatewayCwd.value)
  }
  return selectedWorkspacePath.value || gatewayCwd.value
})

const workflowQuery = computed(() => getWorkflowCommandQuery(inputText.value))
const workflowOptions = computed(() => filterWorkflowOptions(workflows.value, workflowQuery.value ?? ''))
const workflowMenuOpen = computed(() => (
  workflowQuery.value !== null && dismissedWorkflowText.value !== inputText.value
))
const activeWorkflowDescendant = computed(() => {
  if (!workflowMenuOpen.value || !workflowOptions.value.length) return undefined
  return `workflow-option-${activeWorkflowIndex.value}`
})

function onFileSelected(e) {
  const files = Array.from(e.target.files || [])
  selectedFiles.value.push(...files)
}

function selectWorkflow(workflow) {
  const command = formatWorkflowCommand(workflow)
  inputText.value = command
  dismissedWorkflowText.value = command
  activeWorkflowIndex.value = 0
  nextTick(() => document.getElementById('chat-input')?.focus())
}

function dismissWorkflowMenu() {
  dismissedWorkflowText.value = inputText.value
}

function restoreWorkflowMenu() {
  dismissedWorkflowText.value = null
}

async function submitMessage() {
  const parsed = parseWorkflowCommand(inputText.value)
  if (parsed.kind === 'invalid') {
    ui.showAlert(parsed.error)
    return
  }
  if (parsed.kind === 'workflow' && selectedFiles.value.length) {
    ui.showAlert('工作流指令暂不支持同时发送附件；请先移除附件')
    return
  }
  dismissedWorkflowText.value = inputText.value
  await sendMessage()
}

function handleInputKeydown(event) {
  if (event.isComposing || event.keyCode === 229) return

  if (workflowMenuOpen.value) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (workflowOptions.value.length) {
        event.preventDefault()
        const delta = event.key === 'ArrowDown' ? 1 : -1
        activeWorkflowIndex.value = moveWorkflowSelection(
          activeWorkflowIndex.value,
          workflowOptions.value.length,
          delta,
        )
      }
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      dismissWorkflowMenu()
      return
    }
    if (
      event.key === 'Enter'
      && !event.shiftKey
      && !event.altKey
      && !event.ctrlKey
      && !event.metaKey
      && workflowOptions.value.length
    ) {
      event.preventDefault()
      selectWorkflow(workflowOptions.value[activeWorkflowIndex.value] || workflowOptions.value[0])
      return
    }
  }

  if (
    event.key === 'Enter'
    && !event.shiftKey
    && !event.altKey
    && !event.ctrlKey
    && !event.metaKey
  ) {
    event.preventDefault()
    submitMessage()
  }
}

function autoResizeInput() {
  const el = document.getElementById('chat-input')
  if (!el) return
  el.style.height = 'auto'
  const borders = el.offsetHeight - el.clientHeight
  el.style.height = el.scrollHeight + borders + 'px'
}

watch(inputText, () => nextTick(autoResizeInput))

watch(workflowOptions, () => {
  activeWorkflowIndex.value = workflowOptions.value.length ? 0 : -1
})

watch(
  [workflowQuery, activeWorkspacePath],
  async ([query, workspacePath], [previousQuery, previousWorkspacePath] = []) => {
    if (query === null || !workspacePath) {
      workflowRequestVersion++
      loadingWorkflows.value = false
      workflowLoadError.value = ''
      workflows.value = []
      return
    }
    if (previousQuery !== null && previousWorkspacePath === workspacePath) return

    const requestVersion = ++workflowRequestVersion
    loadingWorkflows.value = true
    workflowLoadError.value = ''
    workflows.value = []
    try {
      const result = await listWorkspaceWorkflows(workspacePath)
      if (requestVersion === workflowRequestVersion) workflows.value = result
    } catch (error) {
      if (requestVersion === workflowRequestVersion) {
        workflowLoadError.value = error.message || '工作流加载失败'
      }
    } finally {
      if (requestVersion === workflowRequestVersion) loadingWorkflows.value = false
    }
  },
  { immediate: true },
)

watch(uploadResetToken, () => {
  const el = document.getElementById('file-upload')
  if (el) el.value = ''
})

onUnmounted(() => {
  workflowRequestVersion++
})
</script>

<style scoped>
#input-wrapper {
  background: transparent; border-top: none;
  display: flex; flex-direction: column;
  padding: 0 16px 16px; align-items: center;
  transition: background 0.25s, border-color 0.25s;
}
#file-preview-bar { display: flex; width: 100%; max-width: 820px; padding: 0 0 8px; }
.preview-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--md-surface-container-high);
  border: 1px solid var(--md-primary);
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 12px;
  color: var(--md-primary);
}
.preview-chip .close-btn {
  background: none; border: none; color: var(--md-text-secondary);
  cursor: pointer; display: flex; align-items: center; padding: 2px; border-radius: 50%;
}
.preview-chip .close-btn:hover { background: rgba(0,0,0,0.05); color: var(--md-text-error); }

#input-area {
  width: 100%; max-width: 820px;
  position: relative;
  display: flex; gap: 8px; align-items: center;
  background: var(--md-surface-container);
  border: 1px solid var(--md-outline-variant);
  border-radius: var(--g-pill-radius);
  padding: 8px 10px 8px 14px;
}
#input-area textarea {
  flex: 1; background: transparent; border: none; outline: none;
  color: var(--md-text-primary); font-size: 16px; font-family: inherit;
  resize: none; min-height: 28px; max-height: 160px; padding: 6px 4px;
}
#input-area textarea:focus { border: none; }
#input-area label.btn {
  background: transparent; color: var(--md-primary); border: none; border-radius: var(--md-shape-full);
  width: 40px; height: 40px; display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: background 0.2s;
}
#input-area label.btn:hover { background: var(--md-surface-variant); }
#input-area input[type=file] { display: none; }
#input-area button.send {
  background: var(--md-primary); color: var(--md-on-primary); border: none; border-radius: var(--md-shape-full);
  width: 40px; height: 40px; display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: all 0.2s; box-shadow: 0 1px 3px rgba(0,0,0,0.1); flex-shrink: 0;
}
#input-area button.send:hover:not(:disabled) { filter: brightness(1.1); transform: scale(1.05); }
#input-area button.send:disabled { opacity: .4; cursor: default; box-shadow: none; }
#input-area button.send.stop { background: var(--md-text-error, #d32f2f); color: #fff; }

.workflow-menu {
  position: absolute;
  left: 0;
  right: 0;
  bottom: calc(100% + 8px);
  z-index: 30;
  max-height: 320px;
  overflow-y: auto;
  padding: 6px;
  border: 1px solid var(--md-outline-variant);
  border-radius: var(--md-shape-large);
  background: var(--md-surface-container-high);
  box-shadow: var(--md-elevation-2);
}
.workflow-option {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px;
  border: none;
  border-radius: var(--md-shape-medium);
  background: transparent;
  color: var(--md-text-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.workflow-option:hover,
.workflow-option.active {
  background: var(--md-surface-variant);
}
.workflow-option-heading {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.workflow-command {
  color: var(--md-primary);
  font-family: monospace;
  font-weight: 600;
}
.workflow-menu-state {
  color: var(--md-text-secondary);
  font-size: 12px;
  padding: 14px 12px;
}
.workflow-menu-state.error {
  color: var(--md-text-error);
}

@media (max-width: 768px) {
  #input-wrapper {
    position: fixed;
    left: 0; right: 0;
    bottom: 0;
    z-index: 25;
    background: var(--md-surface-container);
    border-top: 1px solid var(--md-outline-variant);
  }
  #input-area {
    padding: 8px 12px;
    padding-bottom: calc(8px + env(safe-area-inset-bottom));
    gap: 8px;
  }
  #file-preview-bar { padding: 6px 12px 0; }
}

@media (max-width: 400px) {
  #input-area { gap: 6px; }
}
</style>
