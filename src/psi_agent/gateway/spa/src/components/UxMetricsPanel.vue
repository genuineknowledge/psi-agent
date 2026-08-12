<template>
  <aside
    v-if="enabled"
    class="ux-panel"
    :class="{ collapsed }"
    aria-label="SPA v1 用户体验指标"
  >
    <header class="ux-header">
      <div class="ux-title">
        <span class="material-symbols-outlined" aria-hidden="true">monitoring</span>
        <span>UX 指标</span>
        <span v-if="activeCount" class="live-dot" :title="`${activeCount} 个回合进行中`"></span>
      </div>
      <button
        class="icon-button"
        type="button"
        :aria-label="collapsed ? '展开 UX 指标' : '收起 UX 指标'"
        @click="collapsed = !collapsed"
      >
        <span class="material-symbols-outlined" aria-hidden="true">
          {{ collapsed ? 'expand_less' : 'expand_more' }}
        </span>
      </button>
    </header>

    <div v-if="!collapsed" class="ux-body">
      <p class="ux-note">浏览器观测 · 仅内存 · 不采集对话内容</p>

      <div class="metric-grid" aria-live="polite">
        <div class="metric-card">
          <span>完成样本</span>
          <strong>{{ summary.sample_size }}</strong>
        </div>
        <div class="metric-card">
          <span>成功率</span>
          <strong>{{ formatRate(summary.rates.success) }}</strong>
        </div>
        <div class="metric-card">
          <span>UX TTFT P50</span>
          <strong>{{ formatMs(summary.timings.ux_ttft_ms.p50) }}</strong>
        </div>
        <div class="metric-card">
          <span>UX TTFT P95</span>
          <strong>{{ formatMs(summary.timings.ux_ttft_ms.p95) }}</strong>
        </div>
        <div class="metric-card">
          <span>总耗时 P50</span>
          <strong>{{ formatMs(summary.timings.total_ms.p50) }}</strong>
        </div>
        <div class="metric-card">
          <span>首状态 P50</span>
          <strong>{{ formatMs(summary.timings.first_status_ms.p50) }}</strong>
        </div>
        <div class="metric-card">
          <span>Fallback 恢复</span>
          <strong>{{ formatRate(summary.rates.fallback_recovery) }}</strong>
        </div>
        <div class="metric-card">
          <span>聚合降级</span>
          <strong>{{ formatRate(summary.rates.aggregation_degraded) }}</strong>
        </div>
        <div class="metric-card">
          <span>Stop P95</span>
          <strong>{{ formatMs(summary.timings.stop_ms.p95) }}</strong>
        </div>
        <div class="metric-card">
          <span>Trace 完整</span>
          <strong>{{ formatRate(summary.rates.trace_complete) }}</strong>
        </div>
      </div>

      <section v-if="latest" class="latest-turn">
        <div class="section-title">最近回合</div>
        <div class="latest-line">
          <span class="outcome" :class="latest.outcome">{{ latest.outcome }}</span>
          <span>{{ latest.router_modes.join(' + ') || 'direct' }}</span>
          <span>TTFT {{ formatMs(latest.timings.ux_ttft_ms) }}</span>
          <span>总计 {{ formatMs(latest.timings.total_ms) }}</span>
        </div>
        <code :title="latest.trace_id">trace {{ shortTrace(latest.trace_id) }}</code>
      </section>

      <div v-else class="empty-state">发送一条消息后开始生成样本。</div>

      <footer class="ux-actions">
        <button class="action-button" type="button" :disabled="!turns.length" @click="downloadMetrics">
          <span class="material-symbols-outlined" aria-hidden="true">download</span>
          导出 JSON
        </button>
        <button class="action-button" type="button" :disabled="!turns.length" @click="ux.clear">
          <span class="material-symbols-outlined" aria-hidden="true">delete_sweep</span>
          清空
        </button>
      </footer>
    </div>
  </aside>
</template>

<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { useUxStore } from '../stores/ux.js'

const ux = useUxStore()
const { activeCount, enabled, summary, turns } = storeToRefs(ux)
const collapsed = ref(false)
const latest = computed(() => turns.value[0] ?? null)

function formatMs(value) {
  if (!Number.isFinite(value)) return '—'
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}s`
  return `${Math.round(value)}ms`
}

function formatRate(value) {
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : '—'
}

function shortTrace(traceId) {
  return typeof traceId === 'string' ? `${traceId.slice(0, 8)}…` : '—'
}

function downloadMetrics() {
  const snapshot = ux.exportSnapshot()
  const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  const stamp = snapshot.generated_at.replace(/[:.]/g, '-').replace('T', '_').replace('Z', '')
  link.href = url
  link.download = `psi-spa-v1-ux-${stamp}.json`
  link.click()
  URL.revokeObjectURL(url)
}
</script>

<style scoped>
.ux-panel {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 40;
  width: min(380px, calc(100vw - 32px));
  max-height: calc(100dvh - 32px);
  overflow: auto;
  color: var(--md-text-primary);
  background: var(--md-surface-container);
  border: 1px solid var(--md-outline-variant);
  border-radius: var(--md-shape-large);
  box-shadow: var(--md-elevation-3);
}

.ux-panel.collapsed { width: auto; }

.ux-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 48px;
  padding: 0 8px 0 14px;
}

.ux-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
}

.ux-title .material-symbols-outlined { color: var(--md-primary); }

.live-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--md-shape-full);
  background: var(--md-primary);
  animation: ux-pulse 1.2s ease-in-out infinite;
}

.icon-button,
.action-button {
  border: 0;
  color: var(--md-text-secondary);
  background: transparent;
  cursor: pointer;
}

.icon-button {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: var(--md-shape-full);
}

.icon-button:hover,
.action-button:hover:not(:disabled) { background: var(--md-surface-container-high); }

.ux-body { padding: 0 14px 14px; }

.ux-note {
  margin: 0 0 10px;
  color: var(--md-text-secondary);
  font-size: 11px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.metric-card {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  padding: 9px 10px;
  background: var(--md-surface-container-high);
  border-radius: var(--md-shape-medium);
}

.metric-card span {
  overflow: hidden;
  color: var(--md-text-secondary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-card strong {
  color: var(--md-text-primary);
  font-size: 17px;
  font-variant-numeric: tabular-nums;
}

.latest-turn {
  margin-top: 12px;
  padding: 10px;
  background: var(--md-surface-container-low);
  border-radius: var(--md-shape-medium);
}

.section-title {
  margin-bottom: 6px;
  color: var(--md-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.latest-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  font-size: 12px;
}

.outcome { text-transform: uppercase; }
.outcome.ok { color: var(--md-primary); }
.outcome.error,
.outcome.incomplete { color: var(--md-text-error); }
.outcome.stopped { color: var(--md-text-secondary); }

.latest-turn code {
  display: block;
  margin-top: 7px;
  overflow: hidden;
  color: var(--md-text-secondary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-state {
  padding: 18px 4px 8px;
  color: var(--md-text-secondary);
  font-size: 12px;
  text-align: center;
}

.ux-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 10px;
}

.action-button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 36px;
  padding: 0 10px;
  border-radius: var(--md-shape-full);
  font-size: 12px;
}

.action-button .material-symbols-outlined { font-size: 18px; }
.action-button:disabled { cursor: default; opacity: 0.45; }

@keyframes ux-pulse {
  50% { opacity: 0.35; transform: scale(0.8); }
}

@media (max-width: 768px) {
  .ux-panel {
    right: 8px;
    bottom: 8px;
    width: calc(100vw - 16px);
    max-height: min(70dvh, 620px);
  }

  .ux-panel.collapsed { width: auto; }
}

@media (prefers-reduced-motion: reduce) {
  .live-dot { animation: none; }
}
</style>
