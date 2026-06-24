<script setup>
import { MODULES } from '../lib/modules.js'

const props = defineProps({
  modules: { type: Object, required: true },
  // 'off' (all modules off / Hermes) | 'on' (all on) | 'mid' (partial)
  masterState: { type: String, default: 'on' },
  restarting: { type: Boolean, default: false },
})
const emit = defineEmits(['toggle-module', 'set-all', 'open-drawer', 'restart-session'])

// Clicking the master switch: from full-on go to all-off, otherwise turn all on.
function clickMaster() {
  emit('set-all', props.masterState !== 'on')
}

// Drawer cards open a side panel instead of toggling the switch.
function clickModule(m) {
  if (m.drawer) emit('open-drawer', m.key)
  else emit('toggle-module', m.key)
}
</script>

<template>
  <aside class="panel" :class="{ hermes: masterState === 'off' }">
    <div class="panel-head">
      <div class="head-row">
        <div class="p-title">框架能力开关</div>
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
      <div class="p-sub">
        {{ masterState === 'off' ? '已切换到 Hermes 原生模式，开启任一模块即可返回' : '实时切换模块，观察 agent 行为差异' }}
      </div>
    </div>

    <div class="toggle-list">
      <div
        v-for="m in MODULES"
        :key="m.key"
        class="toggle"
        :class="{ on: modules[m.key], 'has-drawer': m.drawer }"
        @click="clickModule(m)"
      >
        <div class="t-icon">{{ m.icon }}</div>
        <div class="t-text">
          <div class="t-name">{{ m.name }}</div>
          <div class="t-desc">{{ m.desc }}</div>
        </div>
        <div v-if="m.drawer" class="drawer-cue" title="展开演示面板">›</div>
        <div v-else class="switch"><div class="knob"></div></div>
      </div>
    </div>

    <div class="hermes-section">
      <div class="divider"></div>
      <div class="sec-label">总控制</div>
      <div
        class="toggle master"
        :class="'state-' + masterState"
        @click="clickMaster"
      >
        <div class="t-icon">⊜</div>
        <div class="t-text">
          <div class="t-name">模块总开关</div>
          <div class="t-desc">
            {{ masterState === 'off' ? '当前：全部关闭（Hermes 原生对照）'
              : masterState === 'on' ? '当前：全部启用'
              : '当前：部分启用' }}
          </div>
        </div>
        <div class="switch master-switch"><div class="knob"></div></div>
      </div>
    </div>

    <div class="foot">
      <div class="hint">
        <b>提示</b>
        <span>关闭模块会立即影响下一轮对话的能力边界。</span>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.panel {
  display: flex; flex-direction: column; gap: 14px; border-radius: var(--r-2xl);
  background: var(--surface); border: 1px solid var(--line); padding: 18px; overflow-y: auto;
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

.toggle-list { display: flex; flex-direction: column; gap: 10px; }
.toggle {
  display: flex; align-items: center; gap: 10px; padding: 12px;
  border-radius: var(--r-xl); background: var(--surface-inset);
  border: 1px solid var(--line-soft); cursor: pointer; user-select: none;
}
.t-icon {
  width: 34px; height: 34px; border-radius: var(--r-lg); flex: 0 0 auto;
  background: var(--accent-soft); display: grid; place-items: center; color: var(--accent); font-size: 16px;
}
.t-text { flex: 1 1 auto; min-width: 0; }
.t-name { font-weight: 600; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.t-desc { font-size: 11px; color: var(--muted); margin-top: 2px; line-height: 1.4; }

.switch {
  width: 38px; height: 22px; border-radius: var(--r-full); background: var(--line);
  flex: 0 0 auto; padding: 3px; display: flex; transition: background .16s ease;
}
.knob {
  width: 16px; height: 16px; border-radius: var(--r-full); background: var(--surface);
  transition: transform .16s ease, background .16s ease;
}
.toggle.on .switch { background: var(--accent); }
.toggle.on .knob { transform: translateX(16px); background: #fff; }

/* Drawer card: opens a side panel; shows a chevron cue instead of a switch. */
.toggle.has-drawer { border-color: var(--accent); }
.drawer-cue {
  width: 22px; height: 22px; border-radius: var(--r-full); flex: 0 0 auto;
  display: grid; place-items: center; font-size: 16px; font-weight: 700;
  color: var(--accent); background: var(--accent-soft);
  transition: transform .16s ease;
}
.toggle.has-drawer:hover .drawer-cue { transform: translateX(2px); }

/* Hermes mode: upper modules are all off and tinted gold instead of grey.
   In direct-entry they're also locked (.disabled); in manual-entry they stay
   clickable so the user can turn one back on to leave. */
.toggle.disabled { cursor: default; }
.panel.hermes .toggle-list .toggle .t-icon { background: var(--hermes-soft); color: var(--hermes); }
.panel.hermes .toggle-list .toggle .switch { background: rgba(230, 168, 23, .35); }
.panel.hermes .toggle-list .toggle .knob { background: #fff; }

.hermes-section { display: flex; flex-direction: column; gap: 10px; }
.divider { height: 1px; background: var(--line-soft); }
.sec-label { font-size: 11px; font-weight: 600; color: var(--muted); }

/* master switch: three states (off=gold / on=blue / mid=grey, knob centered) */
.toggle.master .t-icon { background: var(--surface-2); color: var(--muted); font-weight: 800; }
.master-switch { background: var(--line); }
.master-switch .knob { transform: translateX(8px); background: #fff; }   /* mid */
.toggle.master.state-on .master-switch { background: var(--accent); }
.toggle.master.state-on .master-switch .knob { transform: translateX(16px); }
.toggle.master.state-on .t-icon { background: var(--accent-soft); color: var(--accent); }
.toggle.master.state-off .master-switch { background: var(--hermes); }
.toggle.master.state-off .master-switch .knob { transform: translateX(0); background: var(--on-hermes); }
.toggle.master.state-off .t-icon { background: var(--hermes-soft); color: var(--hermes); }
.toggle.master.state-off { border-color: rgba(230, 168, 23, .4); }

.foot { margin-top: auto; }
.hint { padding: 12px; border-radius: var(--r-lg); background: var(--accent-soft); }
.hint b { font-size: 11px; color: var(--accent); display: block; margin-bottom: 4px; }
.hint span { font-size: 11px; color: var(--muted); }
</style>
