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

      <textarea v-model="store.inputText" rows="1" placeholder="发送消息..." @keydown.enter.exact.prevent="$emit('send')"></textarea>

      <div class="model-zone">
        <ModelPanel />
      </div>

      <button v-if="store.streaming" class="send stop" @click="$emit('stop')" title="停止生成"><span class="material-symbols-outlined">stop</span></button>
      <button v-else class="send" @click="$emit('send')" title="发送消息"><span class="material-symbols-outlined">send</span></button>
    </div>
  </div>
</template>

<script setup>
import { store } from '../store.js'
import ModelPanel from './ModelPanel.vue'

defineEmits(['send', 'stop'])

function onFileSelected(e) {
  store.selectedFile = e.target.files[0] || null
}

function clearSelectedFile() {
  store.selectedFile = null
  document.getElementById('file-upload').value = ''
}
</script>
