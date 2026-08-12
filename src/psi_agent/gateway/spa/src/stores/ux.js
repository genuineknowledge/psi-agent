import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  createUxTurnRecord,
  deriveUxTurn,
  finalizeUxTurn,
  isUxDebugEnabled,
  markUxTimestamp,
  recordUxIssue,
  recordUxRouterStatus,
  recordUxSseEvent,
  summarizeUxTurns,
} from '../uxMetrics.js'

const MAX_COMPLETED_TURNS = 200

function monotonicNow() {
  return globalThis.performance?.now?.() ?? Date.now()
}

export const useUxStore = defineStore('ux', () => {
  const enabled = ref(isUxDebugEnabled(globalThis.location?.search ?? ''))
  const turns = ref([])
  const activeTurns = ref({})
  const traceBySession = new Map()
  const summary = computed(() => summarizeUxTurns(turns.value))
  const activeCount = computed(() => Object.keys(activeTurns.value).length)

  function startTurn({ traceId, sessionKey }) {
    if (!enabled.value || !traceId) return null
    if (!activeTurns.value[traceId]) {
      activeTurns.value[traceId] = createUxTurnRecord({
        traceId,
        at: monotonicNow(),
        wallTime: new Date().toISOString(),
      })
    }
    if (sessionKey) traceBySession.set(sessionKey, traceId)
    return activeTurns.value[traceId]
  }

  function moveTurn(traceId, oldSessionKey, newSessionKey) {
    if (!enabled.value || !activeTurns.value[traceId]) return
    if (oldSessionKey && traceBySession.get(oldSessionKey) === traceId) {
      traceBySession.delete(oldSessionKey)
    }
    if (newSessionKey) traceBySession.set(newSessionKey, traceId)
  }

  function mark(traceId, name) {
    if (!enabled.value) return
    markUxTimestamp(activeTurns.value[traceId], name, monotonicNow())
  }

  function recordSse(traceId, eventTraceId) {
    if (!enabled.value) return
    recordUxSseEvent(activeTurns.value[traceId], {
      tracePresent: eventTraceId !== undefined,
      traceMatches: eventTraceId === traceId,
    })
  }

  function recordRouterStatus(traceId, status) {
    if (!enabled.value) return
    recordUxRouterStatus(activeTurns.value[traceId], status, monotonicNow())
  }

  function recordIssue(traceId, issue) {
    if (!enabled.value) return
    recordUxIssue(activeTurns.value[traceId], issue)
  }

  function markStopForSession(sessionKey) {
    if (!enabled.value) return
    const traceId = traceBySession.get(sessionKey)
    if (traceId) mark(traceId, 'stop_clicked')
  }

  function finishTurn(traceId, { outcome, statusCleared }) {
    if (!enabled.value) return null
    const record = activeTurns.value[traceId]
    if (!record) return null
    finalizeUxTurn(record, {
      outcome,
      statusCleared,
      at: monotonicNow(),
    })
    const completed = deriveUxTurn(record)
    turns.value = [completed, ...turns.value].slice(0, MAX_COMPLETED_TURNS)
    delete activeTurns.value[traceId]
    for (const [sessionKey, activeTraceId] of traceBySession.entries()) {
      if (activeTraceId === traceId) traceBySession.delete(sessionKey)
    }
    return completed
  }

  function clear() {
    turns.value = []
  }

  function exportSnapshot() {
    return {
      schema_version: 1,
      generated_at: new Date().toISOString(),
      collection: 'spa-v1-browser-observed',
      privacy: 'content-free-memory-only',
      summary: summary.value,
      turns: turns.value,
    }
  }

  return {
    enabled,
    turns,
    activeCount,
    summary,
    startTurn,
    moveTurn,
    mark,
    recordSse,
    recordRouterStatus,
    recordIssue,
    markStopForSession,
    finishTurn,
    clear,
    exportSnapshot,
  }
})
