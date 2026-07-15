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
      <label class="btn" for="file-upload"><span class="material-symbols-outlined">attach_file</span></label>
      <input type="file" id="file-upload" multiple @change="onFileSelected">

      <textarea
        id="chat-input"
        v-model="inputText"
        rows="1"
        placeholder="问问 HaiTun"
        @keydown.enter.exact.prevent="sendMessage"
        @input="autoResizeInput"
      ></textarea>

      <ModelPanel
        @select-ai="$emit('select-ai', $event)"
        @delete-ai="$emit('delete-ai', $event)"
      />

      <button v-if="streaming" class="send stop" @click="stopMessage" title="停止生成">
        <span class="material-symbols-outlined">stop</span>
      </button>
      <button v-else class="send" @click="sendMessage" title="发送消息">
        <span class="material-symbols-outlined">send</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { watch, nextTick } from 'vue'
import { storeToRefs } from 'pinia'
import { useChatStore } from '../stores/chat.js'
import { sendMessage, stopMessage } from '../composables/useChat.js'
import ModelPanel from './ModelPanel.vue'

const chat = useChatStore()
const { selectedFiles, inputText, uploadResetToken, streaming } = storeToRefs(chat)

defineEmits(['select-ai', 'delete-ai'])

function onFileSelected(e) {
  const files = Array.from(e.target.files || [])
  selectedFiles.value.push(...files)
}

function autoResizeInput() {
  const el = document.getElementById('chat-input')
  if (!el) return
  el.style.height = 'auto'
  const borders = el.offsetHeight - el.clientHeight
  el.style.height = el.scrollHeight + borders + 'px'
}

watch(inputText, () => nextTick(autoResizeInput))

watch(uploadResetToken, () => {
  const el = document.getElementById('file-upload')
  if (el) el.value = ''
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
