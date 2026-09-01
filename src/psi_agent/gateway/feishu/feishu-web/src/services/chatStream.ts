/**
 * ``POST /sessions/{id}/chat`` 的流式收发。
 *
 * 后端返回 SSE (``data:`` 行 + 空行分隔), 事件 type 有 text / reasoning / blob / error,
 * 结束标记是 ``[DONE]``。单独放一个文件而不塞进 api.ts: 那边是 JSON 请求-响应, 这里是
 * 长连接 + 回调, 两种错误处理方式不一样 (这里 abort 算正常结束)。
 */

export interface StreamHandlers {
  onText: (delta: string) => void;
  onReasoning?: (delta: string, kind?: string) => void;
  onFile?: (name: string, path?: string) => void;
  onDone: () => void;
  onError: (error: Error) => void;
}

interface StreamEvent {
  type?: string;
  text?: string;
  error?: string;
  kind?: string;
  name?: string;
  path?: string;
}

function buildBody(text: string, files: File[]): { headers: Record<string, string>; body: BodyInit } {
  const chunks = JSON.stringify([{ type: "text", text }]);
  if (!files.length) {
    return { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ chunks: [{ type: "text", text }] }) };
  }
  // 带附件时用 multipart —— Content-Type 交给浏览器填 (要带 boundary)。
  const form = new FormData();
  form.append("chunks", chunks);
  for (const file of files) form.append("file", file, file.name);
  return { headers: {}, body: form };
}

export async function streamChat(
  sessionId: string,
  text: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
  files: File[] = [],
): Promise<void> {
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    handlers.onDone();
  };

  try {
    const { headers, body } = buildBody(text, files);
    const resp = await fetch(`/sessions/${encodeURIComponent(sessionId)}/chat`, {
      method: "POST",
      headers,
      body,
      signal,
    });
    if (!resp.ok || !resp.body) {
      const data = (await resp.json().catch(() => ({}))) as { error?: string };
      throw new Error(data.error || `HTTP ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
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
          let evt: StreamEvent;
          try {
            evt = JSON.parse(payload) as StreamEvent;
          } catch {
            continue; // keepalive / 半截帧, 忽略
          }
          if (evt.type === "text") handlers.onText(evt.text || "");
          else if (evt.type === "reasoning") handlers.onReasoning?.(evt.text || "", evt.kind);
          else if (evt.type === "blob") handlers.onFile?.(evt.name || "", evt.path);
          else if (evt.type === "error") {
            handlers.onError(new Error(evt.error || "对话出错"));
            finish();
            return;
          }
        }
      }
    }
    finish();
  } catch (err) {
    // 用户点「停止」走的是 abort, 不是错误。
    if (err instanceof DOMException && err.name === "AbortError") {
      finish();
      return;
    }
    handlers.onError(err instanceof Error ? err : new Error(String(err)));
  }
}
