<template>
  <div ref="rootRef" class="user-hub" :class="{ compact }">
    <button
      type="button"
      class="hub-avatar"
      :class="{ compact }"
      :title="menuTitle"
      aria-haspopup="menu"
      :aria-expanded="hubMenuOpen"
      @click="ui.toggleHubMenu()"
    >
      <img v-if="userAvatar" class="avatar-img" :src="userAvatar" alt="">
      <span v-else-if="avatarInitial" class="avatar-text">{{ avatarInitial }}</span>
      <span v-else class="material-symbols-outlined">person</span>
    </button>

    <div v-if="hubMenuOpen" class="hub-menu" role="menu">
      <button type="button" class="hub-menu-item" role="menuitem" @click="ui.openHubPanel('profile')">
        <span class="material-symbols-outlined">badge</span>
        <span>我的资料</span>
      </button>
      <button type="button" class="hub-menu-item" role="menuitem" @click="ui.openHubPanel('models')">
        <span class="material-symbols-outlined">smart_toy</span>
        <span>大模型</span>
        <span v-if="ais.length" class="menu-badge">{{ ais.length }}</span>
      </button>
      <button type="button" class="hub-menu-item" role="menuitem" @click="ui.openHubPanel('login')">
        <span class="material-symbols-outlined">login</span>
        <span>登录</span>
        <span class="menu-muted">本地</span>
      </button>
      <button type="button" class="hub-menu-item" role="menuitem" @click="ui.openHubPanel('settings')">
        <span class="material-symbols-outlined">settings</span>
        <span>设置</span>
      </button>
    </div>

    <HubProfilePanel :show="hubPanel === 'profile'" @close="ui.closeHubPanel()" />
    <HubModelsPanel :show="hubPanel === 'models'" @close="ui.closeHubPanel()" />
    <HubLoginPanel :show="hubPanel === 'login'" @close="ui.closeHubPanel()" />
    <HubSettingsPanel :show="hubPanel === 'settings'" @close="ui.closeHubPanel()" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { onClickOutside } from '@vueuse/core'
import { useStorage } from '@vueuse/core'
import { useAiStore } from '../stores/ai.js'
import { useUiStore } from '../stores/ui.js'
import { LS_USER_AVATAR, LS_USER_NAME } from '../userProfile.js'
import HubProfilePanel from './HubProfilePanel.vue'
import HubModelsPanel from './HubModelsPanel.vue'
import HubLoginPanel from './HubLoginPanel.vue'
import HubSettingsPanel from './HubSettingsPanel.vue'

defineProps({
  compact: { type: Boolean, default: false },
})

const ui = useUiStore()
const { hubMenuOpen, hubPanel } = storeToRefs(ui)
const { ais } = storeToRefs(useAiStore())
const userName = useStorage(LS_USER_NAME, '')
const userAvatar = useStorage(LS_USER_AVATAR, '')

const rootRef = ref(null)

onClickOutside(rootRef, () => {
  if (hubMenuOpen.value) ui.closeHubMenu()
})

const avatarInitial = computed(() =>
  userName.value ? userName.value.trim().charAt(0).toUpperCase() : ''
)

const menuTitle = computed(() =>
  userName.value ? `${userName.value} — 用户菜单` : '用户菜单'
)
</script>

<style scoped>
.user-hub {
  position: relative;
  flex-shrink: 0;
}

.hub-avatar {
  width: 32px;
  height: 32px;
  border-radius: var(--md-shape-full);
  background: var(--md-primary);
  color: var(--md-on-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  font-weight: 500;
  border: none;
  cursor: pointer;
  padding: 0;
  transition: filter 0.2s;
  overflow: hidden;
}

.avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.hub-avatar.compact {
  width: 36px;
  height: 36px;
}

.hub-avatar:hover {
  filter: brightness(1.08);
}

.hub-avatar .material-symbols-outlined {
  font-size: 20px;
}

.avatar-text {
  line-height: 1;
}

.hub-menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  min-width: 200px;
  padding: 6px;
  border-radius: 16px;
  border: 1px solid var(--md-outline-variant);
  background: var(--md-surface-container-high);
  box-shadow: var(--md-elevation-2);
  z-index: 120;
}

.user-hub.compact .hub-menu {
  right: 0;
}

.hub-menu-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--md-text-primary);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
  text-align: left;
}

.hub-menu-item:hover {
  background: rgba(128, 128, 128, var(--md-state-hover));
}

.hub-menu-item .material-symbols-outlined {
  font-size: 20px;
  color: var(--md-text-secondary);
}

.menu-badge {
  margin-left: auto;
  font-size: 11px;
  font-weight: 600;
  color: var(--md-primary);
  background: color-mix(in srgb, var(--md-primary) 12%, transparent);
  padding: 2px 8px;
  border-radius: 999px;
}

.menu-muted {
  margin-left: auto;
  font-size: 11px;
  color: var(--md-text-secondary);
}
</style>
