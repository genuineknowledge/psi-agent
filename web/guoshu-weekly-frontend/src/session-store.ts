/** Local registry of every session this browser created. */

export interface SessionEntry {
  id: string;
  title: string;
  createdAt: string;
}

const SESSIONS_KEY = "guoshu_weekly_sessions";

function readAll(): SessionEntry[] {
  try {
    const raw = localStorage.getItem(SESSIONS_KEY);
    const parsed = raw ? (JSON.parse(raw) as SessionEntry[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function listSessions(): SessionEntry[] {
  return readAll().sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
}

/** Register a session (once), using the first question as its title. */
export function registerSession(id: string, title: string): void {
  const sessions = readAll();
  if (sessions.some((entry) => entry.id === id)) return;
  sessions.unshift({ id, title: title.slice(0, 24) || "新对话", createdAt: new Date().toISOString() });
  // Keep a bounded registry — old entries stay in the Gateway, this is an index.
  localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions.slice(0, 60)));
}
