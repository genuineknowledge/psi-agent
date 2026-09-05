import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bell,
  CalendarDays,
  FileText,
  Folder,
  HelpCircle,
  ListTodo,
  LayoutGrid,
  MessageCircle,
  Search,
  Settings,
  Users,
  Video,
  Zap,
} from "lucide-react";
import {
  createSession,
  deleteSession,
  generateTitle,
  getSessionHistory,
  getSessionTodos,
  getTodoSegment,
  listAis,
  listSessions,
  listSummaries,
  listTodoSegments,
  listTitles,
  loginDev,
  loginWithCode,
  revealWorkspacePath,
  streamChat,
  type SessionTodo,
  type TodoSegmentSummary,
} from "./api";
import { plainTextFromMarkdown } from "./services/assistantDisplay";
import { ChatView } from "./components/chat-view";
import { ArtifactDrawer } from "./components/artifact-drawer";
import { DeliveryPreviewModal } from "./components/delivery-preview-modal";
import { NewDeliveriesPanel } from "./components/new-deliveries-panel";
import { TasksView } from "./components/tasks-view";
import { historyToChat, historyToDeliverables } from "./cend/services/sessionBridge";
import type { HistoryMessage as CendHistoryMessage } from "./cend/services/api";
import {
  appendContentSegment,
  contentSegmentsStart,
  sealContentBeforeTools,
  settleContentSegments,
  streamSegmentBodies,
} from "./services/contentSegments";
import { isCompleteAgent, normalizeFailedTurns } from "./services/messageTurn";
import {
  addPendingDeliveries,
  clearPendingDeliveries,
  readPendingDeliveries,
} from "./services/pendingDeliveries";
import { stripToolMarkersFromReasoning } from "./services/reasoningDisplay";
import { resolveTaskProgress } from "./services/taskProgress";
import { applyProgressEvent, progressLogStart, summarizeToolCallText } from "./services/turnProgress";
import type { ChatMessage, Task } from "./types";


export default function App() {
  const [identity, setIdentity] = useState<{ name: string; open_id?: string } | null>(null);
  const [sessions, setSessions] = useState<Array<{ id: string; backend_id?: string }>>([]);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [summaries, setSummaries] = useState<Record<string, string>>({});
  const [todosBySession, setTodosBySession] = useState<Record<string, SessionTodo[]>>({});
  const [segmentsBySession, setSegmentsBySession] = useState<Record<string, TodoSegmentSummary[]>>({});
  const [segmentTodosBySession, setSegmentTodosBySession] = useState<Record<string, SessionTodo[]>>({});
  const [segmentSelection, setSegmentSelection] = useState<Record<string, string>>({});
  const [filePaths, setFilePaths] = useState<Record<string, Record<string, string>>>({});
  const [fileDataBySession, setFileDataBySession] = useState<Record<string, Record<string, string>>>({});
  const [newDeliverablesBySession, setNewDeliverablesBySession] = useState<Record<string, string[]>>(() => readPendingDeliveries());
  const [turnSettledBySession, setTurnSettledBySession] = useState<Record<string, boolean>>({});
  const [liveThinkingBySession, setLiveThinkingBySession] = useState<Record<string, string>>({});
  const [liveProgressBySession, setLiveProgressBySession] = useState<Record<string, { lines: string[]; current: string }>>({});
  const [activeId, setActiveId] = useState("");
  const [chats, setChats] = useState<Record<string, ChatMessage[]>>({});
  const [input, setInput] = useState("");
  const [booting, setBooting] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [nav, setNav] = useState<"tasks" | "chat">("tasks");
  const [cendMainView, setCendMainView] = useState<"workspace" | "new-task">("workspace");
  const [newDeliveriesOpen, setNewDeliveriesOpen] = useState(false);
  const [artifact, setArtifact] = useState<{ taskId: string; mode: "new" | "history"; file?: string } | null>(null);
  const [toast, setToast] = useState("");
  const [contextCollapsed, setContextCollapsed] = useState(false);
  const [previewFile, setPreviewFile] = useState<{ name: string; task: string; path?: string; data?: string } | null>(null);
  const [taskFilter, setTaskFilter] = useState("all");
  const [taskSearch, setTaskSearch] = useState("");
  const [newDraft, setNewDraft] = useState("");
  const [pendingFiles, setPendingFiles] = useState<Record<string, File[]>>({});
  const abortRef = useRef<AbortController | null>(null);
  const stopRequestedRef = useRef(false);
  const failedRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let ident: { name: string; open_id?: string } | null = null;
      try {
        const feishu = (window as unknown as { h5?: { getAuthCode?: (opts: object) => Promise<{ code?: string }> } }).h5;
        if (feishu?.getAuthCode) {
          const res = await feishu.getAuthCode({});
          if (res?.code) ident = await loginWithCode(res.code);
        }
      } catch (err) {
        console.warn("飞书身份登录失败，回退开发身份", err);
      }
      if (!ident) {
        try {
          ident = await loginDev();
        } catch (err) {
          if (!cancelled) setError(`身份识别失败：${(err as Error).message}`);
        }
      }
      if (!cancelled && ident) {
        setIdentity(ident);
        try {
          window.localStorage.setItem("gw-user-name", ident.name || "");
        } catch {
          // ignore private mode
        }
      }
      try {
        const ais = await listAis();
        const aiId = (ais.find((a) => a.id === "bot-ai-1") || ais[0])?.id || "";
        const all = await listSessions();
        const list = all.filter(
          (s) => s.backend_id === aiId && !String(s.id).startsWith("feishu-")
        );
        if (!cancelled) {
          setSessions(list);
          if (list.length) setActiveId(list[0].id);
        }
        const historyMap: Record<string, ChatMessage[]> = {};
        const filePathRows: Record<string, Record<string, string>> = {};
        await Promise.all(
          list.map(async (session) => {
            try {
              const rows = await getSessionHistory(session.id);
              const cendRows = rows as unknown as CendHistoryMessage[];
              const chatRows = historyToChat(cendRows);
              const mapped: ChatMessage[] = chatRows.map((m) => ({
                role: m.role === "user" ? "user" : "assistant",
                text: m.text,
                reasoning: m.reasoning ? stripToolMarkersFromReasoning(m.reasoning) || undefined : undefined,
                tools: m.tools,
                files: (m.files || []).map((f) => f.name),
              }));
              historyMap[session.id] = normalizeFailedTurns(mapped);
              const dels = historyToDeliverables(cendRows);
              if (Object.keys(dels.paths).length) {
                filePathRows[session.id] = { ...(filePathRows[session.id] || {}), ...dels.paths };
              }
            } catch {
              // no history yet
            }
          })
        );
        const settledRows: Record<string, boolean> = {};
        for (const [sid, msgs] of Object.entries(historyMap)) {
          settledRows[sid] = msgs.some(isCompleteAgent);
        }
        if (!cancelled) {
          setChats(historyMap);
          setFilePaths(filePathRows);
          setTurnSettledBySession(settledRows);
        }
        try {
          const map = await listTitles();
          if (!cancelled) setTitles(map);
        } catch {
          // titles are optional
        }
        try {
          const map = await listSummaries();
          if (!cancelled) setSummaries(map);
        } catch {
          // summaries are optional
        }
        const todoRows: Record<string, SessionTodo[]> = {};
        const segRows: Record<string, TodoSegmentSummary[]> = {};
        const segTodoRows: Record<string, SessionTodo[]> = {};
        await Promise.all(
          list.map(async (session) => {
            try {
              const resp = await getSessionTodos(session.id);
              todoRows[session.id] = resp.todos;
            } catch {
              // no todos yet
            }
            try {
              const segs = await listTodoSegments(session.id);
              segRows[session.id] = segs;
              await Promise.all(
                segs.map(async (seg) => {
                  try {
                    const detail = await getTodoSegment(session.id, seg.id);
                    segTodoRows[`${session.id}::${seg.id}`] = detail.todos;
                  } catch {
                    // segment detail optional
                  }
                })
              );
            } catch {
              // no todo segments yet
            }
          })
        );
        if (!cancelled) {
          setTodosBySession(todoRows);
          setSegmentsBySession(segRows);
          setSegmentTodosBySession(segTodoRows);
        }
      } catch (err) {
        if (!cancelled) setError(`连接 Feishu Gateway 失败：${(err as Error).message}`);
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const tasks = useMemo<Task[]>(
    () =>
      sessions.map((s, i) => {
        const title = titles[s.id] || `任务 ${String(i + 1).padStart(2, "0")}`;
        const msgs = chats[s.id] || [];
        const started = msgs.length > 0;
        const todos = todosBySession[s.id] || [];
        const sel = segmentSelection[s.id] || "live";
        const activeTodos = sel !== "live" ? segmentTodosBySession[`${s.id}::${sel}`] || [] : todos;
        const files = Array.from(new Set(msgs.flatMap((m) => m.files || [])));
        const newDels = newDeliverablesBySession[s.id] || [];
        const projection = resolveTaskProgress({
          streaming: sending && activeId === s.id,
          turnSettled: !!turnSettledBySession[s.id],
          todos: activeTodos,
          hasDeliverables: files.length > 0,
        });
        const status =
          !started && !projection.hasTodoTrack && projection.phase === "advance"
            ? "待开始"
            : projection.phase === "done"
              ? projection.hasTodoTrack && projection.steps.length > 0 && projection.steps.every((x) => x.state === "done")
                ? "已完成"
                : "已回复"
              : projection.phase === "deliver"
                ? "产出中"
                : projection.indeterminate
                  ? "进行中"
                  : started
                    ? "待继续"
                    : "待开始";
        return {
          id: s.id,
          title,
          summary: plainTextFromMarkdown(summaries[s.id] || ""),
          newDeliverables: newDels,
          deliveryState: newDels.length ? "ready" : "none",
          status,
          progress: projection.progress,
          indeterminate: projection.indeterminate,
          progressLabel: projection.progressLabel,
          hasTodoTrack: projection.hasTodoTrack,
          phase: projection.phase,
          phaseLabel: projection.phaseLabel,
          sop: "自动流程",
          owner: "海豚",
          updated: projection.updated,
          files,
          steps: projection.steps.map((st) => ({ t: st.label, s: st.state, detail: st.detail })),
        };
      }),
    [sessions, titles, chats, todosBySession, segmentSelection, segmentTodosBySession, summaries, sending, activeId, turnSettledBySession, newDeliverablesBySession]
  );

  const counts = useMemo(
    () => ({
      all: tasks.length,
      working: tasks.filter((t) => ["进行中", "产出中", "已回复", "待继续"].includes(t.status)).length,
      attention: tasks.filter((t) => t.status === "待确认" || t.status === "待我处理").length,
      done: tasks.filter((t) => t.status === "已完成").length,
    }),
    [tasks]
  );

  const q = taskSearch.trim().toLowerCase();
  const filtered = tasks.filter((t) => {
    const fok =
      taskFilter === "all" ||
      (taskFilter === "working" && t.status === "进行中") ||
      (taskFilter === "attention" && (t.status === "待确认" || t.status === "待我处理")) ||
      (taskFilter === "done" && t.status === "已完成");
    const qok = !q || (t.title + t.sop + t.owner + t.files.join("")).toLowerCase().includes(q);
    return fok && qok;
  });

  const activeTask = tasks.find((t) => t.id === activeId) || tasks[0];
  const activeMessages = activeId ? chats[activeId] || [] : [];

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activeMessages.length, activeId]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const inField = !!target && ["INPUT", "TEXTAREA"].includes(target.tagName);
      if (event.key === "Escape") {
        if (previewFile) setPreviewFile(null);
        else if (newDeliveriesOpen) setNewDeliveriesOpen(false);
        return;
      }
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      if (inField && !event.altKey) return;
      if (nav !== "chat") return;
      if (tasks.length <= 1) return;
      event.preventDefault();
      const idx = tasks.findIndex((t) => t.id === activeId);
      const next = event.key === "ArrowLeft" ? idx - 1 : idx + 1;
      if (next >= 0 && next < tasks.length) {
        setActiveId(tasks[next].id);
        setNav("chat");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [nav, tasks, activeId, newDeliveriesOpen, previewFile]);

  const streamIntoSession = async (sessionId: string, text: string, includeUser = true, files: File[] = []) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    stopRequestedRef.current = false;
    failedRef.current = false;
    const userMsg: ChatMessage | null = includeUser ? { role: "user", text } : null;
    const assistantMsg: ChatMessage = { role: "assistant", text: "" };
    setChats((prev) => ({
      ...prev,
      [sessionId]: [...(prev[sessionId] || []), ...(userMsg ? [userMsg] : []), assistantMsg],
    }));

    let buffer = "";
    let timer: number | undefined;
    const tools: string[] = [];
    const fileNames: string[] = [];
    const filePathRows: Record<string, string> = {};
    const fileDataRows: Record<string, string> = {};
    let thinkingBuf = "";
    let liveThinking = "";
    let fullText = "";
    let segments = contentSegmentsStart();
    let progress = progressLogStart();
    const updateProgress = (kind: string | undefined, text: string) => {
      progress = applyProgressEvent(progress, kind, text);
      setLiveProgressBySession((prev) => ({ ...prev, [sessionId]: progress }));
      if (!progress.lines.length) return;
      setChats((prev) => {
        const list = [...(prev[sessionId] || [])];
        const last = list[list.length - 1];
        if (!last || last.role !== "assistant") return prev;
        list[list.length - 1] = { ...last, progress: progress.lines.slice() };
        return { ...prev, [sessionId]: list };
      });
    };
    const appendFiles = (names: string[]) => {
      if (!names.length) return;
      setChats((prev) => {
        const list = [...(prev[sessionId] || [])];
        const last = list[list.length - 1];
        if (!last || last.role !== "assistant") return prev;
        const merged = Array.from(new Set([...(last.files || []), ...names]));
        list[list.length - 1] = { ...last, files: merged };
        return { ...prev, [sessionId]: list };
      });
      addPendingDeliveries(sessionId, names);
      setNewDeliverablesBySession((prev) => {
        const current = prev[sessionId] || [];
        const next = Array.from(new Set([...current, ...names]));
        return { ...prev, [sessionId]: next };
      });
    };
    const flush = () => {
      if (!buffer) return;
      const chunk = buffer;
      buffer = "";
      segments = appendContentSegment(segments, chunk);
      const bodies = streamSegmentBodies(segments);
      setChats((prev) => {
        const list = [...(prev[sessionId] || [])];
        const last = list[list.length - 1];
        list[list.length - 1] = { ...last, interimText: bodies.interimText || undefined, text: bodies.text };
        return { ...prev, [sessionId]: list };
      });
    };

    await streamChat(sessionId, text, {
      onText: (chunk) => {
        fullText += chunk;
        updateProgress("content", chunk);
        const sendRe = /\[\s*SEND\s*:([^\]]*)\]/gi;
        let m: RegExpExecArray | null;
        while ((m = sendRe.exec(chunk))) {
          const raw = m[1].trim();
          if (!raw) continue;
          const name = raw.split(/[\\/]/).pop() || raw;
          fileNames.push(name);
          filePathRows[name] = raw;
          appendFiles([name]);
        }
        buffer += chunk;
        if (!timer) {
          timer = window.setTimeout(() => {
            timer = undefined;
            flush();
          }, 100);
        }
      },
      onReasoning: (text, kind) => {
        updateProgress(kind, text);
        if (kind === "tool_call" || kind === "tool_result") {
          if (kind === "tool_call") {
            segments = sealContentBeforeTools(segments);
            const bodies = streamSegmentBodies(segments);
            setChats((prev) => {
              const list = [...(prev[sessionId] || [])];
              const last = list[list.length - 1];
              if (!last || last.role !== "assistant") return prev;
              list[list.length - 1] = { ...last, interimText: bodies.interimText || undefined, text: bodies.text };
              return { ...prev, [sessionId]: list };
            });
          }
          tools.push(summarizeToolCallText(text) || text);
        } else {
          thinkingBuf += text;
          liveThinking += text;
          setLiveThinkingBySession((prev) => ({ ...prev, [sessionId]: liveThinking }));
        }
      },
      onFile: (name, path, data) => {
        fileNames.push(name);
        if (path) filePathRows[name] = path;
        if (data) fileDataRows[name] = data;
        appendFiles([name]);
      },
      onDone: () => {
        if (abortRef.current === controller) abortRef.current = null;
        if (timer) window.clearTimeout(timer);
        timer = undefined;
        flush();
        setLiveThinkingBySession((prev) => {
          const next = { ...prev };
          delete next[sessionId];
          return next;
        });
        setLiveProgressBySession((prev) => {
          const next = { ...prev };
          delete next[sessionId];
          return next;
        });
        const final = settleContentSegments(segments);
        if (tools.length || fileNames.length) {
          setChats((prev) => {
            const list = [...(prev[sessionId] || [])];
            const last = list[list.length - 1];
            const failedReason = stopRequestedRef.current ? "stopped" : failedRef.current ? "error" : undefined;
            list[list.length - 1] = {
              ...last,
              text: final.finalText,
              interimText: undefined,
              tools: tools.slice(),
              reasoning: stripToolMarkersFromReasoning(thinkingBuf) || undefined,
              progress: undefined,
              files: Array.from(new Set([...(last.files || []), ...fileNames])),
              ...(failedReason ? { failed: true, failedReason } : {}),
            };
            return { ...prev, [sessionId]: list };
          });
        }
        if (Object.keys(filePathRows).length) {
          setFilePaths((prev) => ({ ...prev, [sessionId]: { ...(prev[sessionId] || {}), ...filePathRows } }));
        }
        if (Object.keys(fileDataRows).length) {
          setFileDataBySession((prev) => ({ ...prev, [sessionId]: { ...(prev[sessionId] || {}), ...fileDataRows } }));
        }
        if (!stopRequestedRef.current && !failedRef.current) {
          setTurnSettledBySession((prev) => ({ ...prev, [sessionId]: true }));
          if (!titles[sessionId]) {
            void generateTitle(sessionId, text, fullText)
              .then((res) => {
                if (res?.title) setTitles((prev) => ({ ...prev, [sessionId]: res.title }));
              })
              .catch(() => {});
          }
        }
      },
      onError: (err) => {
        failedRef.current = true;
        if (abortRef.current === controller) abortRef.current = null;
        if (timer) window.clearTimeout(timer);
        timer = undefined;
        flush();
        setChats((prev) => {
          const list = [...(prev[sessionId] || [])];
          const last = list[list.length - 1];
          if (!last || last.role !== "assistant") return prev;
          list[list.length - 1] = { ...last, text: last.text || `请求失败：${err.message}`, failed: true };
          return { ...prev, [sessionId]: list };
        });
        setError(err.message);
      },
    }, controller.signal, files);
  };

  const openNewTask = () => {
    setCendMainView("new-task");
    setNav("chat");
  };

  const createTaskFromDraft = async () => {
    const draft = newDraft.trim();
    if (!draft) return;
    setError("");
    setSending(true);
    try {
      const ais = await listAis();
      const aiId = (ais.find((a) => a.id === "bot-ai-1") || ais[0])?.id || "";
      if (!aiId) throw new Error("没有可用 AI，请先配置 bot-ai-1");
      const created = await createSession(aiId, identity?.open_id || "");
      const firstLine = draft.split("\n")[0].trim();
      const title = firstLine.length > 24 ? `${firstLine.slice(0, 24)}…` : firstLine;
      setTitles((prev) => ({ ...prev, [created.id]: title }));
      setSessions((prev) => [created, ...prev]);
      setActiveId(created.id);
      setChats((prev) => ({ ...prev, [created.id]: [] }));
      setNewDraft("");
      setCendMainView("workspace");
      setNav("chat");
      await streamIntoSession(created.id, draft);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSending(false);
    }
  };

  const removeSession = async (id: string) => {
    try {
      await deleteSession(id);
      setSessions((prev) => {
        const next = prev.filter((s) => s.id !== id);
        if (activeId === id && next.length) setActiveId(next[0].id);
        return next;
      });
      setChats((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const stopStream = () => {
    stopRequestedRef.current = true;
    abortRef.current?.abort();
  };

  const revealFile = async (path: string) => {
    try {
      await revealWorkspacePath(path);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const resolveFilePath = (taskId: string, name: string): string | undefined => {
    const map = filePaths[taskId] || {};
    if (map[name]) return map[name];
    const exact = Object.keys(map).find((k) => k.toLowerCase() === name.toLowerCase());
    if (exact) return map[exact];
    return Object.values(map).find((p) => {
      const base = p.replace(/\\/g, "/").split("/").pop() || "";
      return base.toLowerCase() === name.toLowerCase();
    });
  };

  const openArtifact = (taskId: string, mode: "new" | "history", file?: string) => {
    setArtifact({ taskId, mode, file });
  };

  const saveArtifact = (task: Task) => {
    clearPendingDeliveries(task.id);
    setNewDeliverablesBySession((prev) => ({ ...prev, [task.id]: [] }));
    setChats((prev) => ({
      ...prev,
      [task.id]: [
        ...(prev[task.id] || []),
        { role: "assistant" as const, text: "交付物已保存到成果库。本会话历史交付物仍保留，您仍可基于本次成果继续迭代。" },
      ],
    }));
    setArtifact(null);
    setToast("交付物已保存到成果库");
    window.setTimeout(() => setToast(""), 2600);
  };

  const addPendingFiles = (files: File[]) => {
    if (!activeId || !files.length) return;
    setPendingFiles((prev) => ({
      ...prev,
      [activeId]: [...(prev[activeId] || []), ...Array.from(files)],
    }));
  };

  const removePendingFile = (index: number) => {
    if (!activeId) return;
    setPendingFiles((prev) => ({
      ...prev,
      [activeId]: (prev[activeId] || []).filter((_, i) => i !== index),
    }));
  };

  const setMessageFeedback = (sessionId: string, index: number, kind: "up" | "down") => {
    setChats((prev) => {
      const list = [...(prev[sessionId] || [])];
      const message = list[index];
      if (!message) return prev;
      list[index] = { ...message, feedback: message.feedback === kind ? undefined : kind };
      return { ...prev, [sessionId]: list };
    });
  };

  const regenerateMessage = async (sessionId: string, index: number) => {
    const list = chats[sessionId] || [];
    const target = list[index];
    if (!target || target.role !== "assistant") return;
    const previousUser = [...list.slice(0, index)].reverse().find((message) => message.role === "user");
    const prompt = previousUser?.text || "请重新生成上一次回复";
    setChats((prev) => ({ ...prev, [sessionId]: (prev[sessionId] || []).slice(0, index) }));
    setSending(true);
    try {
      await streamIntoSession(sessionId, prompt, false);
    } finally {
      setSending(false);
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || !activeId || sending) return;
    const files = pendingFiles[activeId] || [];
    setInput("");
    setSending(true);
    setError("");
    try {
      await streamIntoSession(activeId, text, true, files);
    } finally {
      setSending(false);
      setPendingFiles((prev) => ({ ...prev, [activeId]: [] }));
    }
  };

  const switchTask = (dir: 1 | -1) => {
    if (!tasks.length) return;
    const idx = tasks.findIndex((t) => t.id === activeId);
    const next = tasks[Math.min(tasks.length - 1, Math.max(0, idx + dir))];
    if (next) setActiveId(next.id);
  };

  const taskIndex = tasks.findIndex((t) => t.id === activeId);

  return (
    <div className="ht-desktop">
      <div className="ht-dt-top">
        <span className="ht-dt-fsmark"><Zap size={14} /></span>
        <span className="ht-dt-breadcrumb">海豚 Agent · 企业管理</span>
        <span className="ht-dt-search"><Search size={13} />搜索飞书内容</span>
        <span className="ht-dt-top-icons"><Bell size={15} /><HelpCircle size={15} /></span>
        <span className="ht-avatars"><span className="ht-avatar">我</span><span className="ht-avatar gold">林</span></span>
      </div>

      <div className="ht-dt-body">
        <nav className="ht-dt-rail" aria-label="飞书导航">
          <button className="ht-iconbtn" aria-label="消息" aria-current={nav === "chat" ? "page" : undefined} onClick={() => setNav("chat")}><MessageCircle size={17} /></button>
          <button className="ht-iconbtn" aria-label="工作台"><LayoutGrid size={17} /></button>
          <button className="ht-iconbtn" aria-label="会议"><Video size={17} /></button>
          <button className="ht-iconbtn" aria-label="日历"><CalendarDays size={17} /></button>
          <button className="ht-iconbtn" aria-label="文档"><FileText size={17} /></button>
          <button className="ht-iconbtn" aria-label="云盘"><Folder size={17} /></button>
          <span className="ht-rail-spacer" />
          <button className="ht-iconbtn" aria-label="用户"><Users size={17} /></button>
        </nav>

        <div className="ht-dt-app">
          <nav className="ht-dt-nav" aria-label="海豚应用导航">
            <div className="ht-dt-brand"><span className="ht-app-mark" aria-hidden="true" /><div><strong>海豚 Agent</strong><em>企业版 · 云服务器部署</em></div></div>
            <button type="button" className={nav === "tasks" ? "active" : ""} onClick={() => setNav("tasks")}><ListTodo size={16} /><span>任务总览</span></button>
            <button type="button" className={nav === "chat" ? "active" : ""} onClick={() => setNav("chat")}><MessageCircle size={16} /><span>对话</span></button>
            <div className="ht-dt-nav-foot"><span className="ht-avatar">{identity?.name?.slice(0, 1) || "我"}</span><span>{identity?.name || "我"} · 管理员</span><Settings size={15} /></div>
          </nav>

          <main className="ht-dt-main">
            {booting ? (
              <div className="ht-loading">正在连接 Feishu Gateway…</div>
            ) : nav === "tasks" ? (
              <TasksView
                tasks={tasks}
                filtered={filtered}
                counts={counts}
                selected={activeTask}
                filter={taskFilter}
                search={taskSearch}
                onFilter={setTaskFilter}
                onSearch={setTaskSearch}
                onSelect={(id) => setActiveId(id)}
                onDelete={(id) => void removeSession(id)}
                onOpenChat={(id) => {
                  setActiveId(id);
                  setNav("chat");
                }}
                onOpenNewDeliverables={() => setNewDeliveriesOpen(true)}
                newDeliveryCount={tasks.filter((t) => t.newDeliverables.length > 0).length}
                onNewTask={openNewTask}
              />
            ) : (
              <ChatView
                tasks={tasks}
                activeTask={activeTask}
                taskIndex={taskIndex}
                messages={activeMessages}
                userName={identity?.name || ""}
                liveThinking={activeId ? liveThinkingBySession[activeId] || "" : ""}
                progressLog={sending && activeId ? liveProgressBySession[activeId] || null : null}
                input={input}
                sending={sending}
                error={error}
                mainView={cendMainView}
                newDraft={newDraft}
                onInput={setInput}
                onNewDraft={setNewDraft}
                onSend={() => void send()}
                onPrev={() => switchTask(-1)}
                onNext={() => switchTask(1)}
                onNewTask={openNewTask}
                onBackNew={() => setCendMainView("workspace")}
                onCreateTask={() => void createTaskFromDraft()}
                onOpenTaskDeliverables={(taskId, mode) => {
                  const t = tasks.find((x) => x.id === taskId);
                  if (!t || t.newDeliverables.length === 0) {
                    setToast("暂无新交付物");
                    window.setTimeout(() => setToast(""), 1600);
                    return;
                  }
                  openArtifact(taskId, mode);
                }}
                contextCollapsed={contextCollapsed}
                onToggleContext={() => setContextCollapsed((v) => !v)}
                pendingFiles={pendingFiles[activeId] || []}
                onAddFiles={addPendingFiles}
                onRemoveFile={removePendingFile}
                onStop={stopStream}
                onFeedback={(index, kind) => setMessageFeedback(activeId, index, kind)}
                onRegenerate={(index) => void regenerateMessage(activeId, index)}
                segments={activeId ? segmentsBySession[activeId] || [] : []}
                selectedHistory={activeId ? segmentSelection[activeId] || "live" : "live"}
                onSelectHistory={(id) => setSegmentSelection((prev) => ({ ...prev, [activeId]: id }))}
                filePathOf={(name) => (activeId ? resolveFilePath(activeId, name) : undefined)}
                fileDataOf={(name) => (activeId ? fileDataBySession[activeId]?.[name] : undefined)}
                onRevealFile={(path) => void revealFile(path)}
                onOpenFile={(name) =>
                  setPreviewFile({
                    name,
                    task: activeTask?.title || "未命名任务",
                    path: activeId ? resolveFilePath(activeId, name) : undefined,
                    data: activeId ? fileDataBySession[activeId]?.[name] : undefined,
                  })
                }
              />
            )}
          </main>
        </div>
      </div>

      {newDeliveriesOpen && (
        <NewDeliveriesPanel
          tasks={tasks.filter((t) => t.newDeliverables.length > 0)}
          onClose={() => setNewDeliveriesOpen(false)}
          onOpen={(id) => {
            setNewDeliveriesOpen(false);
            openArtifact(id, "new");
          }}
        />
      )}
      {previewFile && (
        <DeliveryPreviewModal
          name={previewFile.name}
          task={previewFile.task}
          path={previewFile.path}
          data={previewFile.data}
          onClose={() => setPreviewFile(null)}
        />
      )}
      {artifact && (() => {
        const t = tasks.find((x) => x.id === artifact.taskId);
        return t ? (
          <ArtifactDrawer
            task={t}
            listMode={artifact.mode}
            initialFile={artifact.file}
            onClose={() => setArtifact(null)}
            onSave={saveArtifact}
            filePathOf={(name) => resolveFilePath(t.id, name)}
            fileDataOf={(name) => fileDataBySession[t.id]?.[name]}
          />
        ) : null;
      })()}
      {toast && <div className="ht-toast" role="status">{toast}</div>}
    </div>
  );
}
