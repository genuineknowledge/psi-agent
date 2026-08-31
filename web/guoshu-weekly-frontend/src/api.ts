/** BFF client. The frontend knows only /api — never the Gateway, never a key. */

export interface SessionInfo {
  id: string;
  workspace: string;
  agent: string;
}

export interface ChatEvent {
  type: string;
  text?: string;
  kind?: string;
  error?: string;
  [key: string]: unknown;
}

export async function login(username: string, password: string): Promise<boolean> {
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return response.ok;
}

export async function logout(): Promise<void> {
  await fetch("/api/logout", { method: "POST" });
}

export async function createSession(): Promise<SessionInfo> {
  const response = await fetch("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  if (response.status === 401) throw new NotLoggedInError();
  if (!response.ok) throw new Error(`create session failed: ${response.status}`);
  return (await response.json()) as SessionInfo;
}

export class NotLoggedInError extends Error {
  constructor() {
    super("not logged in");
  }
}

export interface HistoryMessage {
  role: string;
  text: string;
  /** Tool calls the turn made (the Gateway keeps them per message). */
  tools?: { name: string; arguments?: string }[];
}

const INJECTION_MARKER = "【本次回答要求】";
const INJECTION_SEPARATOR = "\n\n问题:";

/**
 * The BFF prefixes each forwarded question with an answer-organisation
 * instruction, and the Gateway records that prefix in the transcript.
 * Strip it before showing or exporting history — the viewer wants the
 * question they actually asked.
 */
export function cleanHistoryText(text: string): string {
  const separator = text.indexOf(INJECTION_SEPARATOR);
  if (text.startsWith(INJECTION_MARKER) && separator > 0) {
    return text.slice(separator + INJECTION_SEPARATOR.length);
  }
  return text;
}

/**
 * The agent loop saves each model turn's interim text as its own assistant
 * message ("我先读取…" / "文档已读取…" before the real answer). Collapse
 * consecutive same-role messages so one question renders as one bubble —
 * otherwise restoring a transcript shows a stack of "已结束" replies.
 */
export function mergeConsecutiveMessages(messages: HistoryMessage[]): HistoryMessage[] {
  const merged: HistoryMessage[] = [];
  for (const message of messages) {
    const last = merged[merged.length - 1];
    if (last && last.role === message.role) {
      last.text = [last.text, message.text].filter(Boolean).join("\n\n");
    } else {
      merged.push({ ...message });
    }
  }
  return merged;
}

/** The Gateway's transcript for one session (a JSON array of messages). */
export async function fetchHistory(sessionId: string): Promise<HistoryMessage[]> {
  const response = await fetch(`/api/sessions/${sessionId}/history`);
  if (response.status === 401) throw new NotLoggedInError();
  if (!response.ok) throw new Error(`load history failed: ${response.status}`);
  const payload = (await response.json()) as HistoryMessage[];
  if (!Array.isArray(payload)) return [];
  return mergeConsecutiveMessages(
    payload.map((message) =>
      message.role === "user" ? { ...message, text: cleanHistoryText(message.text) } : message,
    ),
  );
}

/** A user-uploaded attachment (P2-1): bare base64, no data-URL prefix. */
export interface UploadFile {
  name: string;
  data: string;
}

/** File → {name, data} where data is bare base64 (BFF/Gateway speak blob chunks). */
export function fileToUploadFile(file: File): Promise<UploadFile> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? "");
      resolve({ name: file.name, data: result.includes(",") ? result.split(",")[1]! : result });
    };
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

/**
 * POST /api/sessions/{id}/chat and consume the SSE stream.
 *
 * fetch + ReadableStream (not EventSource): EventSource cannot send POST
 * bodies, and the chat endpoint needs one. Lines arrive one at a time —
 * each callback fires the moment a line lands, which is what makes the
 * first token visible immediately (plan 6.1 / 7.1).
 */
export async function chatStream(
  sessionId: string,
  messages: { role: string; content: string }[],
  onEvent: (event: ChatEvent) => void,
  options: { identity?: string; preference?: string; files?: UploadFile[] } = {},
): Promise<void> {
  const { files, ...rest } = options;
  const response = await fetch(`/api/sessions/${sessionId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, ...rest, ...(files?.length ? { files } : {}) }),
  });
  if (response.status === 401) throw new NotLoggedInError();
  if (!response.ok || !response.body) throw new Error(`chat failed: ${response.status}`);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const payload = trimmed.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      try {
        onEvent(JSON.parse(payload) as ChatEvent);
      } catch {
        // A malformed line must not kill the stream.
      }
    }
  }
}
