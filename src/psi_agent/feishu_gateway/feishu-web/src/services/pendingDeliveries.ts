/**
 * Local-only record of deliverable basenames that arrived but were not yet
 * saved to 成果库. Survives refresh; cleared only by explicit acceptance.
 */
const LS_KEY = "feishu-web-pending-deliveries";

type PendingMap = Record<string, string[]>;

function parsePending(raw: string | null): PendingMap {
  if (!raw) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: PendingMap = {};
    for (const [taskId, names] of Object.entries(parsed as Record<string, unknown>)) {
      if (!taskId || !Array.isArray(names)) continue;
      const clean = [
        ...new Set(
          names
            .filter((n): n is string => typeof n === "string" && !!n.trim())
            .map((n) => {
              const base = n.replace(/\\/g, "/").split("/").pop() || n;
              return base.trim();
            })
            .filter(Boolean),
        ),
      ];
      if (clean.length) out[taskId] = clean;
    }
    return out;
  } catch {
    return {};
  }
}

export function readPendingDeliveries(): PendingMap {
  try {
    return parsePending(localStorage.getItem(LS_KEY));
  } catch {
    return {};
  }
}

function writePendingDeliveries(map: PendingMap): void {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(map));
  } catch {
    // ignore quota / private mode
  }
}

export function pendingDeliveriesFor(taskId: string): string[] {
  return readPendingDeliveries()[taskId] ?? [];
}

export function addPendingDeliveries(taskId: string, names: string[]): void {
  const clean = [...new Set(names.filter((n) => typeof n === "string" && !!n.trim()))];
  if (!taskId || !clean.length) return;
  const all = readPendingDeliveries();
  const current = all[taskId] ?? [];
  const next = [...new Set([...current, ...clean])];
  if (next.length === current.length && next.every((n, i) => n === current[i])) return;
  writePendingDeliveries({ ...all, [taskId]: next });
}

export function clearPendingDeliveries(taskId: string): void {
  const all = readPendingDeliveries();
  if (!all[taskId]) return;
  const next = { ...all };
  delete next[taskId];
  writePendingDeliveries(next);
}
