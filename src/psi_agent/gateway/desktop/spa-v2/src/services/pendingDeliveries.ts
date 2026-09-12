/**
 * Local-only record of deliverable basenames that arrived but were not yet
 * viewed in the treasure chest. Survives refresh; cleared when the user opens
 * the chest (or otherwise acknowledges new deliverables).
 * Keys are AppData-scoped (see ``appdataScope``).
 */
import { readScopedItem, writeScopedItem } from './appdataScope'

type PendingMap = Record<string, string[]>

function parsePending(raw: string | null): PendingMap {
  if (!raw) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const out: PendingMap = {}
    for (const [taskId, names] of Object.entries(parsed as Record<string, unknown>)) {
      if (!taskId || !Array.isArray(names)) continue
      const clean = [...new Set(names.filter((n): n is string => typeof n === 'string' && !!n.trim()))]
      if (clean.length) out[taskId] = clean
    }
    return out
  } catch {
    return {}
  }
}

export function readPendingDeliveries(): PendingMap {
  try {
    return parsePending(readScopedItem(window.localStorage, 'pending'))
  } catch {
    return {}
  }
}

function writePendingDeliveries(map: PendingMap): void {
  writeScopedItem(window.localStorage, 'pending', JSON.stringify(map))
}

export function pendingDeliveriesFor(taskId: string): string[] {
  return readPendingDeliveries()[taskId] ?? []
}

export function addPendingDeliveries(taskId: string, names: string[]): void {
  const clean = [...new Set(names.filter((n) => typeof n === 'string' && !!n.trim()))]
  if (!taskId || !clean.length) return
  const all = readPendingDeliveries()
  const current = all[taskId] ?? []
  const next = [...new Set([...current, ...clean])]
  if (next.length === current.length && next.every((n, i) => n === current[i])) return
  writePendingDeliveries({ ...all, [taskId]: next })
}

export function clearPendingDeliveries(taskId: string): void {
  const all = readPendingDeliveries()
  if (!all[taskId]) return
  const next = { ...all }
  delete next[taskId]
  writePendingDeliveries(next)
}

/** Move pending-delivery keys when a Session id changes (relocate). */
export function remapPendingDeliveries(oldTaskId: string, newTaskId: string): void {
  if (!oldTaskId || !newTaskId || oldTaskId === newTaskId) return
  const all = readPendingDeliveries()
  const names = all[oldTaskId]
  if (!names?.length) return
  const next = { ...all }
  delete next[oldTaskId]
  const merged = [...new Set([...(next[newTaskId] ?? []), ...names])]
  next[newTaskId] = merged
  writePendingDeliveries(next)
}
