<template>
  <BaseDialog :show="show" width="680px" @close="emit('close')">
    <template #title>启动智能路由服务</template>
    <div v-if="!hasBackends" class="empty">
      <span class="material-symbols-outlined">route</span>
      <p>请先连接普通大模型，再配置路由服务。</p>
      <button class="ok" @click="emit('connect-ai')">先连接大模型</button>
    </div>
    <div v-else class="router-form">
      <label class="field">路由服务名称<input v-model="routerForm.name" placeholder="智能任务路由"></label>
      <label class="field">路由模式
        <select v-model="routerForm.mode">
          <option value="">请选择</option>
          <option value="routing">分流模式</option>
          <option value="aggregation">聚合模式</option>
          <option value="fallback">Fallback 容灾模式</option>
        </select>
      </label>
      <label v-if="routerForm.mode !== 'fallback'" class="field">{{ routerAiRole(routerForm.mode) }} 模型
        <select v-model="routerForm.router_ai_id"><option value="">请选择</option><option v-for="a in ais" :key="a.id" :value="a.id">{{ a.model || a.id }}</option></select>
      </label>
      <div class="section-title">候选服务</div>
      <div v-for="(item, index) in routerForm.upstreams" :key="index" class="upstream-row">
        <select v-model="item.backend_type" @change="item.backend_id = ''">
          <option value="ai">AI</option>
          <option value="router">Router</option>
        </select>
        <select v-model="item.backend_id">
          <option value="">请选择服务</option>
          <option v-for="backend in backendValues(item.backend_type)" :key="backend.id" :value="backend.id">{{ backendLabel(item.backend_type, backend.id) }}</option>
        </select>
        <input v-model="item.description" placeholder="该服务擅长的任务">
        <button class="icon-btn" title="删除候选" @click="removeUpstream(index)"><span class="material-symbols-outlined">delete</span></button>
      </div>
      <button class="add-btn" @click="addUpstream">+ 添加候选服务</button>
      <div class="advanced">
        <label v-if="routerForm.mode !== 'fallback'" class="field">Router 超时（秒）<input v-model="routerForm.router_timeout" type="number" min="0.001" step="any" placeholder="不限制"></label>
        <label class="field">候选服务超时（秒）<input v-model="routerForm.target_timeout" type="number" min="0.001" step="any" placeholder="不限制；嵌套 Router 请按深度放大"></label>
        <label v-if="routerForm.mode !== 'fallback'" class="field">最大上下文字符数<input v-model="routerForm.max_context_chars" type="number" min="1" step="1"></label>
      </div>
    </div>
    <template #actions>
      <button class="cancel" @click="emit('close')">取消</button>
      <button v-if="hasBackends" class="ok" :disabled="submitting" @click="submit">{{ submitting ? '启动中…' : '启动路由服务' }}</button>
    </template>
  </BaseDialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useAiStore } from '../stores/ai.js'
import { useRouterStore } from '../stores/router.js'
import { backendValues as valuesForType, getBackendLabel } from '../backendOptions.js'
import { buildRouterPayload, routerAiRole, validateRouterForm } from '../routerConfig.js'
import { useUiStore } from '../stores/ui.js'
import BaseDialog from './BaseDialog.vue'

defineProps({ show: { type: Boolean, default: false } })
const emit = defineEmits(['close', 'submit', 'connect-ai'])
const { ais } = storeToRefs(useAiStore())
const router = useRouterStore()
const { routerForm, routers } = storeToRefs(router)
const ui = useUiStore()
const submitting = ref(false)

const hasBackends = computed(() => ais.value.length > 0 || routers.value.length > 0)
function backendValues(type) { return valuesForType(type, ais.value, routers.value) }
function backendLabel(type, id) { return getBackendLabel(type, id, ais.value, routers.value) }
function addUpstream() {
  routerForm.value.upstreams.push({ backend_type: 'ai', backend_id: '', description: '' })
}
function removeUpstream(index) { routerForm.value.upstreams.splice(index, 1) }
watch(() => routerForm.value.mode, mode => {
  if (mode === 'fallback') routerForm.value.router_ai_id = ''
})
async function submit() {
  const error = validateRouterForm(routerForm.value, ais.value, routers.value)
  if (error) return ui.showAlert(error)
  submitting.value = true
  try { await emit('submit', buildRouterPayload(routerForm.value)) } finally { submitting.value = false }
}
</script>

<style scoped>
.router-form { display: flex; flex-direction: column; gap: 14px; }
.field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--md-text-secondary); }
.field input, .field select, .upstream-row input, .upstream-row select { padding: 10px 12px; border: 1px solid var(--md-outline-variant); border-radius: 10px; background: var(--md-surface-container); color: var(--md-text-primary); }
.section-title { font-size: 13px; font-weight: 600; }
.upstream-row { display: grid; grid-template-columns: 100px 180px 1fr auto; gap: 8px; }
.icon-btn, .add-btn { border: none; background: transparent; color: var(--md-primary); cursor: pointer; }
.advanced { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.empty { text-align: center; padding: 28px; }
.empty .material-symbols-outlined { font-size: 44px; color: var(--md-primary); }
@media (max-width: 600px) { .upstream-row, .advanced { grid-template-columns: 1fr; } }
</style>
