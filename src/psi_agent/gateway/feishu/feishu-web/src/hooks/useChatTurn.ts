import { useCallback, useRef, useState } from "react";
import type { ChatMessage } from "../types";
import { streamChat } from "../services/chatStream";

/**
 * 一轮对话的流式状态机。
 *
 * 只管「发出去 → 增量进来 → 收尾」这一件事: 消息数组、正在输入、停止、附件路径表。
 * 会话切换与历史加载在 useSessions, 两者通过 messages 的 setter 对接。
 */
export function useChatTurn() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  // 交付物名 → workspace 路径。流式 blob 事件里带 path, 预览和「在文件夹中显示」都要用。
  const [filePaths, setFilePaths] = useState<Record<string, string>>({});
  const abortRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const send = useCallback(
    async (sessionId: string, text: string, files: File[] = []) => {
      if (!sessionId || sending) return;
      const trimmed = text.trim();
      if (!trimmed && !files.length) return;

      setError("");
      setSending(true);
      const controller = new AbortController();
      abortRef.current = controller;

      setMessages((prev) => [
        ...prev,
        { role: "user", text: trimmed, ...(files.length ? { files: files.map((f) => f.name) } : {}) },
        { role: "assistant", text: "" },
      ]);

      // 增量直接改最后一条 assistant 消息。
      const patchLast = (fn: (m: ChatMessage) => ChatMessage) =>
        setMessages((prev) => {
          if (!prev.length) return prev;
          const out = prev.slice();
          out[out.length - 1] = fn(out[out.length - 1]);
          return out;
        });

      await streamChat(
        sessionId,
        trimmed,
        {
          onText: (delta) => patchLast((m) => ({ ...m, text: m.text + delta })),
          onReasoning: (delta) => patchLast((m) => ({ ...m, reasoning: (m.reasoning || "") + delta })),
          onFile: (name, path) => {
            if (!name) return;
            if (path) setFilePaths((prev) => ({ ...prev, [name]: path }));
            patchLast((m) => ({ ...m, files: [...(m.files || []), name] }));
          },
          onDone: () => {
            setSending(false);
            abortRef.current = null;
            patchLast((m) => {
              const stopped = controller.signal.aborted;
              const empty = !m.text.trim() && !(m.files || []).length;
              if (stopped) return { ...m, stopped: true, ...(empty ? { failed: true, failedReason: "stopped" as const } : {}) };
              if (empty) return { ...m, failed: true, failedReason: "incomplete" as const };
              return m;
            });
          },
          onError: (err) => {
            setError(err.message);
            patchLast((m) => ({ ...m, failed: true, failedReason: "error" as const }));
          },
        },
        controller.signal,
        files,
      );
    },
    [sending],
  );

  const filePathOf = useCallback((name: string) => filePaths[name], [filePaths]);

  return { messages, setMessages, sending, error, setError, send, stop, filePathOf, setFilePaths };
}
