<template>
  <div id="input-wrapper" v-show="store.selectedSessionId">
    <div id="file-preview-bar" v-if="store.selectedFile">
      <div class="preview-chip">
        <span class="material-symbols-outlined" style="font-size:16px;">attach_file</span>
        <span>{{ store.selectedFile.name }}</span>
        <button class="close-btn" @click="clearSelectedFile" title="移除附件">
          <span class="material-symbols-outlined" style="font-size:16px;">close</span>
        </button>
      </div>
    </div>

    <div id="input-area">
      <label class="btn" for="file-upload"><span class="material-symbols-outlined">attach_file</span></label>
      <input type="file" id="file-upload" @change="onFileSelected">

      <textarea
        ref="textareaRef"
        v-model="store.inputText"
        rows="1"
        placeholder="发送消息...（Enter 发送，Ctrl+Enter 换行）"
        @keydown.enter.exact.prevent="$emit('send')"
        @keydown.enter.ctrl.prevent="insertNewline"
        @input="autoResize"
      ></textarea>

      <div class="model-zone">
        <ModelPanel />
      </div>

      <button class="send" :disabled="store.streaming" @click="$emit('send')"><span class="material-symbols-outlined">send</span></button>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, watch } from 'vue'
import { store } from '../store.js'
import ModelPanel from './ModelPanel.vue'

defineEmits(['send'])

const textareaRef = ref(null)

// 让文本框高度随内容自动增长（CSS 已限制 max-height: 120px，超过出现滚动条）
function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = el.scrollHeight + 'px'
}

function insertNewline() {
  const el = textareaRef.value
  if (!el) return
  const start = el.selectionStart
  const end = el.selectionEnd
  const text = store.inputText ?? ''
  store.inputText = text.slice(0, start) + '\n' + text.slice(end)
  // 等 DOM 更新后把光标移到插入的换行符之后，并重新计算高度
  nextTick(() => {
    el.selectionStart = el.selectionEnd = start + 1
    autoResize()
  })
}

// 发送后 store.inputText 被清空，需要把高度复位到单行
watch(
  () => store.inputText,
  (val) => {
    if (!val) nextTick(autoResize)
  }
)

function onFileSelected(e) {
  store.selectedFile = e.target.files[0] || null
}

function clearSelectedFile() {
  store.selectedFile = null
  document.getElementById('file-upload').value = ''
}
</script>
