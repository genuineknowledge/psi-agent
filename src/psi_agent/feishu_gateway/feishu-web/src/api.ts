export interface Identity {
  open_id: string;
  name: string;
  dev?: boolean;
}

export interface AiInfo {
  id: string;
  provider: string;
  model: string;
  api_key: string;
  base_url: string;
}

export interface SessionInfo {
  id: string;
  backend_type?: string;
  backend_id?: string;
  workspace?: string;
  agent?: string;
  ai_id?: string;
}

interface ApiError {
  error?: string;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  const data = (await resp.json().catch(() => ({}))) as T & ApiError;
  if (!resp.ok) {
    throw new Error((data as ApiError).error || `HTTP ${resp.status}`);
  }
  return data;
}

function asList<T>(data: T[] | { value?: T[] }): T[] {
  return Array.isArray(data) ? data : (data.value || []);
}

export async function loginDev(): Promise<Identity> {
  return requestJson<Identity>("/auth/feishu", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dev_open_id: "ou_b23dbe79e4c5e98516e26ce937cb7976" }),
  });
}

export async function loginWithCode(code: string): Promise<Identity> {
  return requestJson<Identity>("/auth/feishu", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
}

export async function listAis(): Promise<AiInfo[]> {
  const data = await requestJson<AiInfo[] | { value?: AiInfo[] }>("/ais");
  return asList(data);
}

export async function listSessions(): Promise<SessionInfo[]> {
  const data = await requestJson<SessionInfo[] | { value?: SessionInfo[] }>("/sessions");
  return asList(data);
}

export interface HistoryMessage {
  role: string;
  text: string;
  reasoning?: string;
  tools?: Array<{ name: string; arguments?: string }>;
  sends?: string[];
}

export async function getSessionHistory(id: string): Promise<HistoryMessage[]> {
  return requestJson<HistoryMessage[]>(`/sessions/${encodeURIComponent(id)}/history`);
}

export async function listTitles(): Promise<Record<string, string>> {
  return requestJson<Record<string, string>>("/titles");
}

export async function generateTitle(
  id: string,
  userText: string,
  assistantText: string,
): Promise<{ id: string; title: string }> {
  return requestJson<{ id: string; title: string }>("/titles/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, user_text: userText, assistant_text: assistantText }),
  });
}

export async function createSession(backendId: string, openId = ""): Promise<SessionInfo> {
  return requestJson<SessionInfo>("/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ backend_type: "ai", backend_id: backendId, workspace: "", open_id: openId }),
  });
}

export async function deleteSession(id: string): Promise<void> {
  await requestJson<unknown>(`/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export interface SessionTodo {
  id: string;
  content: string;
  status: string;
}

export interface SessionTodosResponse {
  todos: SessionTodo[];
  summary: {
    total: number;
    pending: number;
    in_progress: number;
    completed: number;
    cancelled: number;
  };
}

export async function getSessionTodos(sessionId: string): Promise<SessionTodosResponse> {
  return requestJson<SessionTodosResponse>(`/sessions/${encodeURIComponent(sessionId)}/todos`);
}

export interface TodoSegmentSummary {
  id: string;
  label: string;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  source: string;
  summary: SessionTodosResponse["summary"];
}

export interface TodoSegmentDetail extends TodoSegmentSummary {
  todos: SessionTodo[];
}

export async function listTodoSegments(sessionId: string): Promise<TodoSegmentSummary[]> {
  return requestJson<TodoSegmentSummary[]>(`/sessions/${encodeURIComponent(sessionId)}/todo-segments`);
}

export async function getTodoSegment(sessionId: string, segmentId: string): Promise<TodoSegmentDetail> {
  return requestJson<TodoSegmentDetail>(
    `/sessions/${encodeURIComponent(sessionId)}/todo-segments/${encodeURIComponent(segmentId)}`
  );
}

export async function listSummaries(): Promise<Record<string, string>> {
  return requestJson<Record<string, string>>("/summaries");
}

export interface WorkspaceFile {
  name: string;
  data: string;
  path: string;
}

export async function readWorkspaceFile(path: string): Promise<WorkspaceFile> {
  const params = new URLSearchParams({ path });
  return requestJson<WorkspaceFile>(`/workspace/file?${params.toString()}`);
}

export async function revealWorkspacePath(path: string): Promise<{ path: string }> {
  return requestJson<{ path: string }>("/workspace/reveal", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

export interface StreamHandlers {
  onText: (text: string) => void;
  onReasoning?: (text: string, kind?: string) => void;
  onFile?: (name: string, path?: string, data?: string) => void;
  onDone: () => void;
  onError: (error: Error) => void;
}

export async function streamChat(
  sessionId: string,
  text: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
  files: File[] = []
): Promise<void> {
  try {
    let headers: Record<string, string> = {};
    let body: BodyInit;
    if (files.length) {
      const form = new FormData();
      form.append("chunks", JSON.stringify([{ type: "text", text }]));
      for (const file of files) form.append("file", file, file.name);
      body = form;
    } else {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify({ chunks: [{ type: "text", text }] });
    }
    const resp = await fetch(`/sessions/${encodeURIComponent(sessionId)}/chat`, {
      method: "POST",
      headers,
      body,
      signal,
    });
    if (!resp.ok || !resp.body) {
      const data = (await resp.json().catch(() => ({}))) as ApiError;
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;
    const finish = () => {
      if (!finished) {
        finished = true;
        handlers.onDone();
      }
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) {
        finish();
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        for (const line of block.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;
          if (payload === "[DONE]") {
            finish();
            return;
          }
          try {
            const evt = JSON.parse(payload) as {
              type?: string;
              text?: string;
              error?: string;
              kind?: string;
              name?: string;
              path?: string;
              data?: string;
            };
            if (evt.type === "text") handlers.onText(evt.text || "");
            else if (evt.type === "reasoning") handlers.onReasoning?.(evt.text || "", evt.kind);
            else if (evt.type === "blob") handlers.onFile?.(evt.name || "", evt.path || "", evt.data || "");
            else if (evt.type === "error") {
              handlers.onError(new Error(evt.error || "对话出错"));
              finish();
              return;
            }
          } catch {
            // ignore malformed keepalive frames
          }
        }
      }
    }
    finish();
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      handlers.onDone();
      return;
    }
    handlers.onError(err instanceof Error ? err : new Error(String(err)));
  }
}
