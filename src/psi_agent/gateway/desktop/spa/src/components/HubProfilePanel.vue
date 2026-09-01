<template>
  <BaseDialog :show="show" width="440px" @close="emit('close')">
    <template #title>我的资料</template>

    <div class="avatar-section">
      <div class="avatar-preview" aria-hidden="true">
        <img v-if="draftAvatar" :src="draftAvatar" alt="">
        <span v-else-if="previewInitial" class="avatar-initial">{{ previewInitial }}</span>
        <span v-else class="material-symbols-outlined">person</span>
      </div>
      <div class="avatar-actions">
        <label class="upload-btn">
          <input type="file" accept="image/*" hidden @change="onAvatarSelected">
          <span class="material-symbols-outlined">upload</span>
          上传头像
        </label>
        <button v-if="draftAvatar" type="button" class="remove-btn" @click="draftAvatar = ''">
          移除头像
        </button>
      </div>
    </div>

    <div class="field">
      <label for="hub-profile-name">称呼</label>
      <input
        id="hub-profile-name"
        v-model="draftName"
        placeholder="希望 HaiTun 怎么称呼你？"
        @keydown.enter.prevent="save"
      >
    </div>

    <template #actions>
      <button class="cancel" @click="emit('close')">取消</button>
      <button class="ok" @click="save">保存</button>
    </template>
  </BaseDialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useStorage } from '@vueuse/core'
import { useUiStore } from '../stores/ui.js'
import { LS_USER_AVATAR, LS_USER_NAME, readAvatarDataUrl } from '../userProfile.js'
import BaseDialog from './BaseDialog.vue'

const props = defineProps({
  show: { type: Boolean, default: false },
})

const emit = defineEmits(['close'])

const ui = useUiStore()
const userName = useStorage(LS_USER_NAME, '')
const userAvatar = useStorage(LS_USER_AVATAR, '')

const draftName = ref('')
const draftAvatar = ref('')

const previewInitial = computed(() =>
  draftName.value.trim() ? draftName.value.trim().charAt(0).toUpperCase() : ''
)

function syncDrafts() {
  draftName.value = userName.value
  draftAvatar.value = userAvatar.value
}

watch(
  () => props.show,
  (open) => { if (open) syncDrafts() },
)

async function onAvatarSelected(e) {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file) return
  try {
    draftAvatar.value = await readAvatarDataUrl(file)
  } catch (err) {
    ui.showAlert(err.message || '上传失败')
  }
}

function save() {
  userName.value = draftName.value.trim()
  userAvatar.value = draftAvatar.value
  emit('close')
}
</script>

<style scoped>
.avatar-section {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
}

.avatar-preview {
  width: 72px;
  height: 72px;
  border-radius: var(--md-shape-full);
  background: var(--md-primary-container);
  color: var(--md-on-primary-container);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  flex-shrink: 0;
}

.avatar-preview img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatar-preview .material-symbols-outlined {
  font-size: 36px;
}

.avatar-initial {
  font-size: 28px;
  font-weight: 600;
  line-height: 1;
}

.avatar-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.upload-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: var(--md-shape-full);
  border: 1px solid var(--md-outline);
  background: var(--md-surface-container);
  color: var(--md-primary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}

.upload-btn:hover {
  background: var(--md-surface-container-high);
}

.upload-btn .material-symbols-outlined {
  font-size: 18px;
}

.remove-btn {
  align-self: flex-start;
  padding: 4px 8px;
  border: none;
  background: none;
  color: var(--md-text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.remove-btn:hover {
  color: var(--md-text-error);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field label {
  font-size: 13px;
  font-weight: 600;
  color: var(--md-text-secondary);
}

.field input {
  width: 100%;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid var(--md-outline-variant);
  background: var(--md-surface-container);
  color: var(--md-text-primary);
  font: inherit;
}
</style>
