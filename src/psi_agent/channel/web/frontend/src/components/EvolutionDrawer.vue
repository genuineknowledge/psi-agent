<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  // URL of the embedded demo (proxied through the web channel at /demo/).
  src: { type: String, default: '/demo/' },
})
const emit = defineEmits(['close'])

// Lazy-mount the iframe: only load the demo once the drawer is first opened.
const mounted = ref(false)
const loading = ref(true)
watch(
  () => props.open,
  (open) => {
    if (open && !mounted.value) {
      mounted.value = true
      loading.value = true
    }
  },
)

function onLoaded() {
  loading.value = false
}
</script>

<template>
  <transition name="drawer-fade">
    <div v-if="open" class="overlay" @click.self="emit('close')">
      <transition name="drawer-slide" appear>
        <aside v-if="open" class="drawer">
          <header class="d-head">
            <div class="d-title">
              <span class="d-icon">⟳</span>
              <div>
                <div class="d-name">自进化</div>
                <div class="d-sub">自我反思与能力进化 · 演示</div>
              </div>
            </div>
            <button class="d-close" title="关闭" @click="emit('close')">✕</button>
          </header>
          <div class="d-body">
            <div v-if="loading" class="d-loading">
              <div class="typing"><span></span><span></span><span></span></div>
              <span>正在加载演示模块…</span>
            </div>
            <iframe
              v-if="mounted"
              class="d-frame"
              :class="{ ready: !loading }"
              :src="src"
              title="自进化演示"
              @load="onLoaded"
            ></iframe>
          </div>
        </aside>
      </transition>
    </div>
  </transition>
</template>

<style scoped>
.overlay {
  position: fixed; inset: 0; z-index: 50;
  background: rgba(15, 23, 42, .42);
  display: flex; justify-content: flex-start;
}
.drawer {
  height: 100vh; width: min(960px, 92vw);
  display: flex; flex-direction: column;
  background: var(--surface); border-right: 1px solid var(--line);
  box-shadow: 8px 0 40px rgba(15, 23, 42, .22);
}
.d-head {
  flex: 0 0 auto; height: 60px; display: flex; align-items: center; justify-content: space-between;
  padding: 0 20px; border-bottom: 1px solid var(--line-soft);
}
.d-title { display: flex; align-items: center; gap: 12px; }
.d-icon {
  width: 36px; height: 36px; border-radius: var(--r-lg); flex: 0 0 auto;
  background: var(--accent-soft); color: var(--accent);
  display: grid; place-items: center; font-size: 18px;
}
.d-name { font-weight: 700; font-size: 15px; }
.d-sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
.d-close {
  width: 32px; height: 32px; border: 1px solid var(--line); border-radius: var(--r-lg);
  background: var(--surface-2); color: var(--muted); font-size: 14px; cursor: pointer;
}
.d-close:hover { color: var(--text); border-color: var(--accent); }

.d-body { flex: 1 1 auto; position: relative; min-height: 0; background: var(--surface-inset); }
.d-loading {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 12px; color: var(--muted); font-size: 13px;
}
.d-frame {
  width: 100%; height: 100%; border: 0; display: block;
  opacity: 0; transition: opacity .2s ease;
}
.d-frame.ready { opacity: 1; }

/* overlay fade + drawer slide-in from the left */
.drawer-fade-enter-active, .drawer-fade-leave-active { transition: opacity .2s ease; }
.drawer-fade-enter-from, .drawer-fade-leave-to { opacity: 0; }
.drawer-slide-enter-active, .drawer-slide-leave-active { transition: transform .24s ease; }
.drawer-slide-enter-from, .drawer-slide-leave-to { transform: translateX(-100%); }
</style>
