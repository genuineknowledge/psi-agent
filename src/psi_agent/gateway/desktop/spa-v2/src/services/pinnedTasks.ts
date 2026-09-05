/** Sidebar task pin — client-only, localStorage (same idea as spa v1 session pin). */

export const PINNED_TASKS_KEY = "gw-v2-pinned-task-ids";

export function normalizePinnedIds(ids: unknown): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  if (!Array.isArray(ids)) return result;
  for (const id of ids) {
    if (typeof id !== "string") continue;
    const trimmed = id.trim();
    if (!trimmed || seen.has(trimmed)) continue;
    seen.add(trimmed);
    result.push(trimmed);
  }
  return result;
}

export function loadPinnedTaskIds(storage: Storage = window.localStorage): string[] {
  try {
    return normalizePinnedIds(JSON.parse(storage.getItem(PINNED_TASKS_KEY) || "[]"));
  } catch {
    return [];
  }
}

export function savePinnedTaskIds(storage: Storage, ids: string[]): void {
  storage.setItem(PINNED_TASKS_KEY, JSON.stringify(normalizePinnedIds(ids)));
}

export function togglePinnedTaskId(ids: string[], id: string): string[] {
  const normalized = normalizePinnedIds(ids);
  const clean = id.trim();
  if (!clean) return normalized;
  if (normalized.includes(clean)) {
    return normalized.filter((existing) => existing !== clean);
  }
  return [...normalized, clean];
}

/** Drop pins whose sessions no longer exist. */
export function prunePinnedTaskIds(ids: string[], activeIds: Iterable<string>): string[] {
  const active = new Set(
    [...activeIds].map((id) => id.trim()).filter(Boolean),
  );
  return normalizePinnedIds(ids).filter((id) => active.has(id));
}

/** Stable sort: pinned first (pin order preserved), then original list order. */
export function sortTasksByPin<T extends { id: string }>(
  tasks: T[],
  pinnedIds: string[],
): T[] {
  const pinned = normalizePinnedIds(pinnedIds);
  if (pinned.length === 0) return tasks;
  const pinRank = new Map(pinned.map((id, index) => [id, index]));
  return tasks
    .map((task, index) => ({ task, index, pin: pinRank.get(task.id) }))
    .sort((a, b) => {
      const aPinned = a.pin !== undefined;
      const bPinned = b.pin !== undefined;
      if (aPinned !== bPinned) return aPinned ? -1 : 1;
      if (aPinned && bPinned) return (a.pin ?? 0) - (b.pin ?? 0);
      return a.index - b.index;
    })
    .map((item) => item.task);
}
