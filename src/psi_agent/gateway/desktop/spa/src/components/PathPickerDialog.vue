<template>
  <div v-if="picker.visible" class="picker-overlay" @click.self="cancelPathPicker">
    <div class="picker-dialog" role="dialog" aria-modal="true" :aria-label="picker.title">
      <header class="picker-header">
        <h3>{{ picker.title }}</h3>
        <button class="icon-btn" type="button" title="关闭" @click="cancelPathPicker">
          <span class="material-symbols-outlined">close</span>
        </button>
      </header>
      <p v-if="picker.hint" class="picker-hint">{{ picker.hint }}</p>

      <div class="picker-body">
        <aside class="picker-nav">
          <div v-if="picker.drives.length" class="nav-section">
            <div class="nav-label">此电脑</div>
            <button
              v-for="d in picker.drives"
              :key="d.path"
              class="nav-item"
              type="button"
              @click="navigatePathPicker(d.path)"
            >
              <span class="material-symbols-outlined">hard_drive</span>
              <span>{{ d.label }}</span>
            </button>
          </div>
          <div class="nav-section">
            <div class="nav-label">快捷位置</div>
            <button
              v-for="r in picker.places"
              :key="r.id"
              class="nav-item"
              type="button"
              @click="navigatePathPicker(r.path)"
            >
              <span class="material-symbols-outlined">{{ rootIcon(r.id) }}</span>
              <span>{{ r.label }}</span>
            </button>
          </div>
        </aside>

        <section class="picker-main">
          <div class="toolbar">
            <button
              class="icon-btn"
              type="button"
              title="上级目录"
              :disabled="!canGoUp"
              @click="goParentPathPicker"
            >
              <span class="material-symbols-outlined">arrow_upward</span>
            </button>
            <div class="breadcrumbs">
              <template v-for="(seg, i) in picker.segments" :key="seg.path">
                <button
                  class="crumb"
                  type="button"
                  @click="navigatePathPicker(seg.path)"
                >
                  {{ seg.name }}
                </button>
                <span v-if="i < picker.segments.length - 1" class="crumb-sep">›</span>
              </template>
            </div>
          </div>
          <input
            v-model="picker.currentPath"
            class="address-input"
            type="text"
            aria-label="路径"
            @keydown.enter.prevent="submitAddress"
          >

          <div class="filter-row">
            <span class="material-symbols-outlined">search</span>
            <input
              v-model="picker.filterText"
              type="search"
              placeholder="筛选当前文件夹"
              aria-label="筛选"
            >
          </div>

          <div v-if="picker.error" class="picker-error">{{ picker.error }}</div>
          <div v-else-if="picker.loading" class="picker-loading">加载中…</div>
          <div v-else class="listing" role="listbox">
            <div
              v-for="entry in entries"
              :key="entry.path"
              class="listing-item"
              :class="{
                selected: entry.path === picker.selectedPath,
                disabled: entry.kind !== 'directory' && picker.mode === 'directory',
              }"
              role="option"
              @click="onEntryClick(entry)"
              @dblclick="onEntryDblClick(entry)"
            >
              <span class="material-symbols-outlined entry-icon">
                {{ entry.kind === 'directory' ? 'folder' : 'description' }}
              </span>
              <span class="entry-name">{{ entry.name }}</span>
            </div>
            <div v-if="entries.length === 0" class="listing-empty">此文件夹为空</div>
          </div>
        </section>
      </div>

      <footer class="picker-footer">
        <label class="footer-label">文件夹:</label>
        <input
          v-model="picker.selectedPath"
          class="footer-path"
          type="text"
          aria-label="选中的文件夹路径"
          @keydown.enter.prevent="confirmPathPicker"
        >
        <div class="footer-actions">
          <button class="cancel" type="button" @click="cancelPathPicker">取消</button>
          <button class="ok" type="button" :disabled="!picker.selectedPath.trim()" @click="confirmPathPicker">
            {{ picker.confirmLabel }}
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useEventListener } from '@vueuse/core'
import {
  cancelPathPicker,
  confirmPathPicker,
  enterPathPickerEntry,
  goParentPathPicker,
  navigatePathPicker,
  selectPathPickerEntry,
  usePathPickerState,
  visibleEntries,
} from '../composables/usePathPicker.js'
import { normalizeWorkspacePath } from '../sessionList.js'

const picker = usePathPickerState()

const entries = computed(() => visibleEntries())

const canGoUp = computed(() => {
  const parent = normalizeWorkspacePath(picker.parent)
  const current = normalizeWorkspacePath(picker.currentPath)
  return !!parent && parent !== current
})

function rootIcon(id) {
  if (id === 'cwd') return 'terminal'
  if (id === 'home') return 'home'
  if (id === 'desktop') return 'desktop_windows'
  if (id === 'documents') return 'description'
  if (id === 'downloads') return 'download'
  return 'folder'
}

function submitAddress() {
  const path = normalizeWorkspacePath(picker.currentPath)
  if (path) navigatePathPicker(path)
}

function onEntryClick(entry) {
  if (entry.kind === 'directory') {
    selectPathPickerEntry(entry)
    return
  }
  if (picker.mode !== 'directory') selectPathPickerEntry(entry)
}

function onEntryDblClick(entry) {
  if (entry.kind === 'directory') enterPathPickerEntry(entry)
}

useEventListener(window, 'keydown', (e) => {
  if (!picker.visible) return
  if (e.key === 'Escape') {
    e.preventDefault()
    cancelPathPicker()
  }
})
</script>

<style scoped>
.picker-overlay {
  position: fixed;
  inset: 0;
  z-index: 110;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}
.picker-dialog {
  width: min(760px, 96vw);
  height: min(560px, 88vh);
  background: var(--md-surface-container-high);
  border: 1px solid var(--md-outline-variant);
  border-radius: 20px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  overflow: hidden;
}
.picker-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px 8px;
}
.picker-header h3 {
  margin: 0;
  font-size: 20px;
  font-weight: 500;
  color: var(--md-text-primary);
}
.picker-hint {
  margin: 0 20px 8px;
  font-size: 13px;
  color: var(--md-text-secondary);
  line-height: 1.5;
}
.picker-body {
  flex: 1;
  min-height: 0;
  display: flex;
  border-top: 1px solid var(--md-outline-variant);
  border-bottom: 1px solid var(--md-outline-variant);
}
.picker-nav {
  width: 168px;
  flex-shrink: 0;
  overflow-y: auto;
  padding: 8px;
  border-right: 1px solid var(--md-outline-variant);
  background: var(--md-surface-container);
}
.nav-section { margin-bottom: 12px; }
.nav-label {
  font-size: 11px;
  color: var(--md-text-secondary);
  padding: 4px 8px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.nav-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: none;
  border-radius: var(--md-shape-small);
  background: transparent;
  color: var(--md-text-primary);
  font-size: 13px;
  cursor: pointer;
  text-align: left;
}
.nav-item:hover { background: var(--md-nav-hover); }
.nav-item .material-symbols-outlined { font-size: 18px; color: var(--md-primary); flex-shrink: 0; }
.picker-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: 8px 12px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
.breadcrumbs {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.crumb {
  border: none;
  background: transparent;
  color: var(--md-primary);
  font-size: 12px;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.crumb:hover { background: var(--md-nav-hover); }
.crumb-sep { color: var(--md-text-secondary); font-size: 12px; }
.address-input {
  width: 100%;
  margin-bottom: 6px;
  padding: 6px 10px;
  border: 1px solid var(--md-outline);
  border-radius: var(--md-shape-small);
  background: var(--md-surface-variant);
  color: var(--md-text-primary);
  font-size: 12px;
  outline: none;
}
.address-input:focus { border-color: var(--md-primary); }
.filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  margin-bottom: 6px;
  border-radius: var(--md-shape-full);
  background: var(--md-surface-variant);
}
.filter-row .material-symbols-outlined { font-size: 18px; color: var(--md-text-secondary); }
.filter-row input {
  flex: 1;
  border: none;
  background: transparent;
  color: var(--md-text-primary);
  font-size: 13px;
  outline: none;
}
.listing {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  border: 1px solid var(--md-outline-variant);
  border-radius: var(--md-shape-small);
  background: var(--md-bg);
}
.listing-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  cursor: pointer;
  font-size: 13px;
  border-bottom: 1px solid var(--md-outline-variant);
}
.listing-item:last-child { border-bottom: none; }
.listing-item:hover { background: var(--md-nav-hover); }
.listing-item.selected {
  background: var(--md-nav-hover);
  outline: 1px solid var(--md-primary);
  outline-offset: -1px;
}
.listing-item.disabled { opacity: 0.45; cursor: default; }
.entry-icon { font-size: 20px; color: var(--md-primary); flex-shrink: 0; }
.entry-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.listing-empty,
.picker-loading,
.picker-error {
  padding: 24px;
  text-align: center;
  font-size: 13px;
  color: var(--md-text-secondary);
}
.picker-error { color: var(--md-text-error); }
.picker-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  flex-wrap: wrap;
}
.footer-label {
  font-size: 13px;
  color: var(--md-text-secondary);
  flex-shrink: 0;
}
.footer-path {
  flex: 1;
  min-width: 160px;
  padding: 8px 12px;
  border: 1px solid var(--md-outline);
  border-radius: var(--md-shape-small);
  background: var(--md-surface-variant);
  color: var(--md-text-primary);
  font-size: 13px;
  outline: none;
}
.footer-path:focus { border-color: var(--md-primary); }
.footer-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.footer-actions button {
  padding: 10px 20px;
  border-radius: var(--md-shape-full);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border: none;
}
.footer-actions .ok {
  background: var(--md-primary);
  color: var(--md-on-primary);
}
.footer-actions .ok:disabled { opacity: 0.45; cursor: not-allowed; }
.footer-actions .cancel {
  background: transparent;
  color: var(--md-primary);
  border: 1px solid var(--md-outline);
}
.icon-btn {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--md-text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
}
.icon-btn:hover:not(:disabled) { background: var(--md-nav-hover); color: var(--md-primary); }
.icon-btn:disabled { opacity: 0.35; cursor: not-allowed; }

@media (max-width: 640px) {
  .picker-nav { display: none; }
  .picker-dialog { height: min(620px, 92vh); }
}
</style>
