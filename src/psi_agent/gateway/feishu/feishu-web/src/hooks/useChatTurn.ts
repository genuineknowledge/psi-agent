import { useCallback, useMemo, useRef, useState } from "react";
import type { ChatMessage } from "../types";
import { streamChat } from "../services/chatStream";
import {
  stripToolMarkersFromReasoning,
  toolSummariesFromReasoning,
} from "../services/reasoningDisplay";
import { applyProgressEvent, progressLogStart } from "../services/turnProgress";
import { addPendingDeliveries } from "../services/pendingDeliveries";

/**
 * 每轮对话的流式状态机 —— 状态按 session 隔离, 不再是一份全局消息数组。
 *
 * C 端的逻辑是多任务各自持有卡片/会话状态, 切走不丢、旧流不污染新会话;
 * 旧版这里只有一份 ``messages``, 切换到另一个任务时上一个任务的流式增量
 * 还会 patchLast 当前会话, 正是「回答跑到我的位置」的来源。
 */

interface SessionTurnState {
  messages: ChatMessage[];
  sending: boolean;
  error: string;
  filePaths: Record<string, string>;
}

const EMPTY_STATE: SessionTurnState = {
  messages: [],
  sending: false,
  error: "",
  filePaths: {},
};

function emptyState(): SessionTurnState {
  return { ...EMPTY_STATE };
}

function patchState(
  prev: Record<string, SessionTurnState>,
  sessionId: string,
  fn: (state: SessionTurnState) => SessionTurnState,
): Record<string, SessionTurnState> {
  return { ...prev, [sessionId]: fn(prev[sessionId] || emptyState()) };
}

/**
 * ``activeSessionId`` 只决定「当前渲染哪一个 session 的状态」;
 * 每个 session 的收发状态都独立存在于 map 里。
 */
export function useChatTurn(activeSessionId: string) {
  const [turns, setTurns] = useState<Record<string, SessionTurnState>>({});
  const abortRef = useRef<Record<string, AbortController | null>>({});

  const active = turns[activeSessionId] || EMPTY_STATE;

  const patch = useCallback((sessionId: string, fn: (s: SessionTurnState) => SessionTurnState) => {
    setTurns((prev) => patchState(prev, sessionId, fn));
  }, []);

  const setMessages = useCallback(
    (sessionId: string, value: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => {
      patch(sessionId, (state) => ({
        ...state,
        messages:
          typeof value === "function"
            ? (value as (prev: ChatMessage[]) => ChatMessage[])(state.messages)
            : value,
      }));
    },
    [patch],
  );

  const setError = useCallback(
    (sessionId: string, error: string) => {
      patch(sessionId, (state) => ({ ...state, error }));
    },
    [patch],
  );

  const setFilePaths = useCallback(
    (sessionId: string, fn: (prev: Record<string, string>) => Record<string, string>) => {
      patch(sessionId, (state) => ({ ...state, filePaths: fn(state.filePaths) }));
    },
    [patch],
  );

  const stop = useCallback(() => {
    const controller = activeSessionId ? abortRef.current[activeSessionId] : null;
    if (!controller) return;
    controller.abort();
    abortRef.current[activeSessionId] = null;
  }, [activeSessionId]);

  const send = useCallback(
    async (sessionId: string, text: string, files: File[] = []) => {
      if (!sessionId) return "";
      const trimmed = text.trim();
      if (!trimmed && !files.length) return "";

      const controller = new AbortController();
      abortRef.current[sessionId] = controller;

      // 本回合墙钟起点。SSE 不下发 thinking_ms, 所以刚跑完的这一条由前端自己量 ——
      // 不量的话它要等下次拉历史才有耗时(见 services/messageTiming.ts 的模块头)。
      const turnT0 = Date.now();

      let rawReasoning = "";
      let toolLines: string[] = [];
      let progress = progressLogStart();
      let assistantText = "";

      patch(sessionId, (state) => ({
        ...state,
        error: "",
        sending: true,
        messages: [
          ...state.messages,
          { role: "user", text: trimmed, ...(files.length ? { files: files.map((f) => f.name) } : {}) },
          { role: "assistant", text: "" },
        ],
      }));

      // 增量只改该 session 的最后一条 assistant 消息。
      const patchLast = (fn: (m: ChatMessage) => ChatMessage) => {
        patch(sessionId, (state) => {
          if (!state.messages.length) return state;
          const out = state.messages.slice();
          out[out.length - 1] = fn(out[out.length - 1]);
          return { ...state, messages: out };
        });
      };

      await streamChat(
        sessionId,
        trimmed,
        {
          onText: (delta) => {
            assistantText += delta;
            patchLast((m) => ({ ...m, text: m.text + delta }));
          },
          onReasoning: (delta, kind) => {
            rawReasoning += delta;
            toolLines = toolSummariesFromReasoning(rawReasoning);
            progress = applyProgressEvent(progress, kind, delta);
            patchLast((m) => ({
              ...m,
              progress: progress.lines.length ? progress.lines : undefined,
              tools: toolLines.length ? toolLines : undefined,
            }));
          },
          onFile: (name, path) => {
            if (!name) return;
            addPendingDeliveries(sessionId, [name]);
            if (path) {
              setFilePaths(sessionId, (prev) => ({ ...prev, [name]: path }));
            }
            patchLast((m) => ({ ...m, files: [...(m.files || []), name] }));
          },
          onDone: () => {
            if (abortRef.current[sessionId] === controller) abortRef.current[sessionId] = null;
            patchLast((m) => {
              const stopped = controller.signal.aborted;
              const empty = !m.text.trim() && !(m.files || []).length;
              const reasoning = stripToolMarkersFromReasoning(rawReasoning);
              const next: ChatMessage = {
                ...m,
                ...(reasoning ? { reasoning } : {}),
                tools: toolLines.length ? toolLines : undefined,
                progress: undefined,
                // 整回合(含排队等待与全部工具轮), 与后端 thinking_ms 同一口径。
                thinkingMs: Date.now() - turnT0,
              };
              if (stopped) {
                return { ...next, stopped: true, ...(empty ? { failed: true, failedReason: "stopped" as const } : {}) };
              }
              if (empty) return { ...next, failed: true, failedReason: "incomplete" as const };
              return next;
            });
            patch(sessionId, (state) => ({ ...state, sending: false }));
          },
          onError: (err) => {
            if (abortRef.current[sessionId] === controller) abortRef.current[sessionId] = null;
            patchLast((m) => ({
              ...m,
              reasoning: stripToolMarkersFromReasoning(rawReasoning),
              tools: toolLines.length ? toolLines : undefined,
              failed: true,
              failedReason: "error" as const,
            }));
            patch(sessionId, (state) => ({ ...state, sending: false, error: err.message }));
          },
        },
        controller.signal,
        files,
      );
      return assistantText;
    },
    [patch, setFilePaths],
  );

  const filePathOf = useCallback(
    (name: string) => (activeSessionId ? (turns[activeSessionId]?.filePaths || {})[name] : undefined),
    [activeSessionId, turns],
  );

  return useMemo(
    () => ({
      messages: active.messages,
      setMessages,
      sending: active.sending,
      error: active.error,
      setError,
      send,
      stop,
      filePathOf,
      setFilePaths,
    }),
    [
      active.messages,
      active.sending,
      active.error,
      activeSessionId,
      filePathOf,
      send,
      setError,
      setFilePaths,
      setMessages,
      stop,
    ],
  );
}
