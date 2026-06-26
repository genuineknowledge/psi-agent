<script setup>
import { ref, computed } from 'vue'
import { WORKSPACES } from '../lib/workspaces.js'

const props = defineProps({
  workspace: { type: String, required: true },
  restarting: { type: Boolean, default: false },
})
const emit = defineEmits(['select-workspace', 'restart-session'])

const open = ref(false)
const current = computed(
  () => WORKSPACES.find((w) => w.name === props.workspace) || WORKSPACES[0],
)

function toggle() {
  open.value = !open.value
}

function pick(name) {
  open.value = false
  if (name !== props.workspace) emit('select-workspace', name)
}
</script>

<template>
  <aside class="panel">
    <div class="panel-head">
      <div class="head-row">
        <div class="p-title">工作区</div>
        <button
          class="refresh-btn"
          type="button"
          title="重启当前 Session 并清理历史"
          :disabled="restarting"
          @click.stop="emit('restart-session')"
        >
          <span :class="{ spinning: restarting }">⟳</span>
        </button>
      </div>
      <div class="p-sub">选择 workspace，对应后端的 agent 服务</div>
    </div>

    <div class="dropdown" :class="{ open }">
      <button class="dd-trigger" type="button" @click.stop="toggle">
        <span class="dd-left">
          <span class="dd-icon">{{ current.icon }}</span>
          <span class="dd-value">{{ current.name }}</span>
        </span>
        <span class="dd-caret">▾</span>
      </button>
      <ul v-if="open" class="dd-menu">
        <li
          v-for="w in WORKSPACES"
          :key="w.name"
          class="dd-item"
          :class="{ active: w.name === workspace }"
          @click="pick(w.name)"
        >
          <span class="dd-icon">{{ w.icon }}</span>
          <span class="dd-item-text">
            <span class="dd-item-name">{{ w.name }}</span>
            <span class="dd-item-desc">{{ w.desc }}</span>
          </span>
        </li>
      </ul>
    </div>

    <div class="ws-info">
      <div class="info-row">
        <div class="info-mark">{{ current.icon }}</div>
        <div class="info-text">
          <div class="ws-name">{{ current.name }}</div>
          <div class="ws-desc">{{ current.desc }}</div>
        </div>
      </div>
    </div>

    <div class="ws-status"><i></i><span>agent 服务已连接</span></div>
  </aside>
</template>

<style scoped>
.panel {
  display: flex; flex-direction: column; gap: 14px; border-radius: var(--r-2xl);
  background: var(--surface); border: 1px solid var(--line); padding: 18px; overflow: visible;
}
.head-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.panel-head .p-title { font-weight: 700; font-size: 15px; }
.panel-head .p-sub { font-size: 12px; color: var(--muted); margin-top: 4px; }
.refresh-btn {
  width: 30px; height: 30px; border-radius: var(--r-lg); border: 1px solid var(--line);
  background: var(--surface-2); color: var(--muted); display: grid; place-items: center;
  cursor: pointer; font: inherit; font-size: 16px; line-height: 1; flex: 0 0 auto;
}
.refresh-btn:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.refresh-btn:disabled { cursor: wait; opacity: .7; }
.refresh-btn span { display: inline-block; }
.refresh-btn .spinning { animation: refresh-spin .8s linear infinite; }
@keyframes refresh-spin { to { transform: rotate(360deg); } }

/* workspace dropdown */
.dropdown { position: relative; }
.dd-trigger {
  width: 100%; height: 44px; display: flex; align-items: center; justify-content: space-between;
  padding: 0 14px; border-radius: var(--r-lg); background: var(--surface-inset);
  border: 1px solid var(--line); cursor: pointer; font: inherit; color: var(--text);
}
.dropdown.open .dd-trigger { border-color: var(--accent); }
.dd-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
.dd-icon { color: var(--accent); font-size: 14px; flex: 0 0 auto; }
.dd-value { font-weight: 600; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dd-caret { color: var(--muted); font-size: 12px; flex: 0 0 auto; }
.dd-menu {
  position: absolute; top: calc(100% + 6px); left: 0; right: 0; z-index: 20;
  margin: 0; padding: 6px; list-style: none; border-radius: var(--r-lg);
  background: var(--surface); border: 1px solid var(--line);
  box-shadow: 0 12px 32px rgba(0, 0, 0, .14);
}
.dd-item {
  display: flex; align-items: center; gap: 10px; padding: 9px 10px;
  border-radius: var(--r-lg); cursor: pointer;
}
.dd-item:hover { background: var(--surface-inset); }
.dd-item.active { background: var(--accent-soft); }
.dd-item-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.dd-item-name { font-weight: 600; font-size: 13px; }
.dd-item-desc { font-size: 11px; color: var(--muted); line-height: 1.4; }

/* selected workspace info card */
.ws-info {
  border-radius: var(--r-xl); background: var(--surface-inset);
  border: 1px solid var(--line-soft); padding: 14px;
}
.info-row { display: flex; align-items: center; gap: 10px; }
.info-mark {
  width: 36px; height: 36px; border-radius: var(--r-lg); flex: 0 0 auto;
  background: var(--accent-soft); display: grid; place-items: center;
  color: var(--accent); font-size: 16px; font-weight: 800;
}
.info-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.ws-name { font-weight: 600; font-size: 13px; }
.ws-desc { font-size: 11px; color: var(--muted); line-height: 1.4; }

.ws-status {
  display: inline-flex; align-items: center; gap: 7px; align-self: flex-start;
  padding: 8px 12px; border-radius: var(--r-full); background: var(--surface-2);
  font-size: 12px; color: var(--muted);
}
.ws-status i { width: 7px; height: 7px; border-radius: var(--r-full); background: var(--accent); }
</style>
