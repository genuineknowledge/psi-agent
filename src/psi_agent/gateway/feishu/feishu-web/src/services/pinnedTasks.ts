/**
 * 任务置顶 —— 纯前端, localStorage。
 *
 * **刻意不走 ToC 那套 ``appdataScope``**: 它的指纹来自 ``GET /defaults`` 下发的 appdata
 * 路径, 而 ToB 的 ``/feishu/defaults`` **只回 ``{ai_id}``** —— 见 ``_routes.py`` 的说明:
 * 「端点只下发 id, 不给 api_key/base_url/provider/model」, appdata 同理不在下发范围内。
 * 拿不到指纹就不做按 AppData 分桶, 而这个顾虑在 ToB 侧本来也不成立: 网页应用的 origin
 * 是飞书里配的部署域名(或固定端口的 127.0.0.1:8848), 不像 ToC 装机版那样每次启动换随机
 * 端口、把同一份记忆根拆成多个偏好桶。
 */

export const PINNED_TASKS_KEY = "feishu-web:pinned-task-ids";

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
  try {
    storage.setItem(PINNED_TASKS_KEY, JSON.stringify(normalizePinnedIds(ids)));
  } catch {
    /* 配额满 / 隐私模式: 置顶是纯偏好, 存不下就算了, 不该让页面报错 */
  }
}

export function togglePinnedTaskId(ids: string[], id: string): string[] {
  const normalized = normalizePinnedIds(ids);
  const clean = id.trim();
  if (!clean) return normalized;
  if (normalized.includes(clean)) return normalized.filter((existing) => existing !== clean);
  return [...normalized, clean];
}

/** 丢掉已经不存在的会话的置顶 —— 否则置顶表只增不减, 且会撑出一个越来越长的空排序键。 */
export function prunePinnedTaskIds(ids: string[], activeIds: Iterable<string>): string[] {
  const active = new Set([...activeIds].map((id) => id.trim()).filter(Boolean));
  return normalizePinnedIds(ids).filter((id) => active.has(id));
}

/** 稳定排序: 置顶的在前(按置顶顺序), 其余保持原顺序。 */
export function sortTasksByPin<T extends { id: string }>(tasks: T[], pinnedIds: string[]): T[] {
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
