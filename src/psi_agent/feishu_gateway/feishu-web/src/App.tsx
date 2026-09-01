import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Bell,
  BookOpen,
  CalendarDays,
  Check,
  ClipboardList,
  ChevronRight,
  Clock,
  Copy,
  Download,
  FileArchive,
  FileText,
  Folder,
  HelpCircle,
  History,
  ListTodo,
  LayoutGrid,
  Layers3,
  MessageCircle,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings,
  Sparkles,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Users,
  Video,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import { marked } from "marked";
import {
  createSession,
  deleteSession,
  getSessionHistory,
  listAis,
  listSessions,
  listTitles,
  loginDev,
  loginWithCode,
  streamChat,
} from "./api";

const NEW_TASK_PRESETS = [
  { label: "学习新知识", prompt: "请帮我学习以下新知识，提炼核心概念、关键要点和可验证的结论：", category: "知识学习", icon: BookOpen },
  { label: "管理工作SOP", prompt: "请帮我梳理并管理以下工作的 SOP，形成清晰可执行的流程步骤：", category: "流程管理", icon: ClipboardList },
  { label: "做一份领导汇报", prompt: "请帮我整理一份面向管理层的汇报，材料包括：", category: "内容整理", icon: Layers3 },
  { label: "研究市场或竞品", prompt: "请研究以下市场或竞品，核验公开来源并给出证据：", category: "深度研究", icon: Search },
  { label: "整理会议与行动项", prompt: "请把以下会议材料整理为结论和行动项：", category: "会议协作", icon: MessageCircle },
];

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  tools?: string[];
  files?: string[];
  feedback?: "up" | "down";
  failed?: boolean;
}

interface Task {
  id: string;
  title: string;
  status: string;
  progress: number;
  sop: string;
  owner: string;
  updated: string;
  files: string[];
  steps: Array<{ t: string; s: string }>;
}

function brandMark(size?: string) {
  return <span className={`cend2-brandmark${size ? ` ${size}` : ""}`} aria-hidden="true"><i /><i /><i /></span>;
}

function statCell(num: string, label: string) {
  return (
    <div className="ht-stat">
      <strong>{num}</strong>
      <em>{label}</em>
    </div>
  );
}

function statusPill(status: string) {
  const cls = status === "已完成" ? "done" : status === "进行中" ? "" : "warn";
  return <span className={`ht-pill ${cls}`}>{status}</span>;
}

function stepChip(step: { t: string; s: string }) {
  const cls = step.s || "waiting";
  return (
    <div className={`cend2-step ${cls}`}>
      <span className="cend2-step-marker">
        {cls === "done" ? <Check size={14} /> : <i className={`cend2-step-dot${cls === "working" ? "" : " off"}`} />}
      </span>
      <span className="cend2-step-label">{step.t}{cls === "working" ? <em>进行中</em> : ""}</span>
    </div>
  );
}

export default function App() {
  const [identity, setIdentity] = useState<{ name: string } | null>(null);
  const [sessions, setSessions] = useState<Array<{ id: string; backend_id?: string }>>([]);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [activeId, setActiveId] = useState("");
  const [chats, setChats] = useState<Record<string, ChatMessage[]>>({});
  const [input, setInput] = useState("");
  const [booting, setBooting] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [nav, setNav] = useState<"tasks" | "chat">("tasks");
  const [cendMainView, setCendMainView] = useState<"workspace" | "new-task">("workspace");
  const [deliverablesOpen, setDeliverablesOpen] = useState(false);
  const [contextCollapsed, setContextCollapsed] = useState(false);
  const [selectedHistory, setSelectedHistory] = useState("live");
  const [previewFile, setPreviewFile] = useState<{ name: string; task: string } | null>(null);
  const [taskFilter, setTaskFilter] = useState("all");
  const [taskSearch, setTaskSearch] = useState("");
  const [newDraft, setNewDraft] = useState("");
  const [pendingFiles, setPendingFiles] = useState<Record<string, File[]>>({});
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let ident: { name: string } | null = null;
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
      if (!cancelled && ident) setIdentity(ident);
      try {
        const ais = await listAis();
        const aiId = (ais.find((a) => a.id === "bot-ai-1") || ais[0])?.id || "";
        const all = await listSessions();
        let list = all.filter(
          (s) => s.backend_id === aiId && !String(s.id).startsWith("feishu-")
        );
        if (!list.length && aiId) {
          const created = await createSession(aiId);
          list = [created];
        }
        if (!cancelled) {
          setSessions(list);
          if (list.length) setActiveId(list[0].id);
        }
        const historyMap: Record<string, ChatMessage[]> = {};
        await Promise.all(
          list.map(async (session) => {
            try {
              const rows = await getSessionHistory(session.id);
              historyMap[session.id] = rows.map((row) => ({
                role: row.role === "user" ? "user" : "assistant",
                text: row.text,
              }));
            } catch {
              // no history yet
            }
          })
        );
        if (!cancelled) setChats(historyMap);
        try {
          const map = await listTitles();
          if (!cancelled) setTitles(map);
        } catch {
          // titles are optional
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
        const lastText = msgs[msgs.length - 1]?.text || "";
        const steps = started
          ? [
              { t: "理解任务目标", s: "done" },
              { t: "拆解执行计划", s: "working" },
              { t: "持续执行并同步", s: "waiting" },
            ]
          : [];
        const doneSteps = steps.filter((x) => x.s === "done").length;
        return {
          id: s.id,
          title,
          status: !started ? "待开始" : lastText.includes("完成") ? "已完成" : "进行中",
          progress: started ? Math.round((doneSteps / steps.length) * 100) : 0,
          sop: "自动流程",
          owner: "海豚",
          updated: "刚刚",
          files: [`${title.slice(0, 8)}.docx`, "证据表.xlsx"],
          steps,
        };
      }),
    [sessions, titles, chats]
  );

  const counts = useMemo(
    () => ({
      all: tasks.length,
      working: tasks.filter((t) => t.status === "进行中").length,
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

  const streamIntoSession = async (sessionId: string, text: string, includeUser = true, files: File[] = []) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const userMsg: ChatMessage | null = includeUser ? { role: "user", text } : null;
    const assistantMsg: ChatMessage = { role: "assistant", text: "" };
    setChats((prev) => ({
      ...prev,
      [sessionId]: [...(prev[sessionId] || []), ...(userMsg ? [userMsg] : []), assistantMsg],
    }));

    let buffer = "";
    let timer: number | undefined;
    const tools: string[] = [];
    const flush = () => {
      if (!buffer) return;
      const chunk = buffer;
      buffer = "";
      setChats((prev) => {
        const list = [...(prev[sessionId] || [])];
        const last = list[list.length - 1];
        list[list.length - 1] = { ...last, text: (last?.text || "") + chunk };
        return { ...prev, [sessionId]: list };
      });
    };

    await streamChat(sessionId, text, {
      onText: (chunk) => {
        buffer += chunk;
        if (!timer) {
          timer = window.setTimeout(() => {
            timer = undefined;
            flush();
          }, 100);
        }
      },
      onReasoning: (text, kind) => {
        if (kind === "tool_call" || kind === "tool_result") tools.push(text);
      },
      onDone: () => {
        if (abortRef.current === controller) abortRef.current = null;
        if (timer) window.clearTimeout(timer);
        timer = undefined;
        flush();
        if (tools.length) {
          setChats((prev) => {
            const list = [...(prev[sessionId] || [])];
            const last = list[list.length - 1];
            list[list.length - 1] = { ...last, tools: tools.slice() };
            return { ...prev, [sessionId]: list };
          });
        }
      },
      onError: (err) => {
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
      const created = await createSession(aiId);
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
    abortRef.current?.abort();
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
                onOpenDeliverables={() => setDeliverablesOpen(true)}
                onNewTask={openNewTask}
              />
            ) : (
              <ChatView
                tasks={tasks}
                activeTask={activeTask}
                taskIndex={taskIndex}
                messages={activeMessages}
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
                onOpenDeliverables={() => setDeliverablesOpen(true)}
                contextCollapsed={contextCollapsed}
                onToggleContext={() => setContextCollapsed((v) => !v)}
                pendingFiles={pendingFiles[activeId] || []}
                onAddFiles={addPendingFiles}
                onRemoveFile={removePendingFile}
                onStop={stopStream}
                onFeedback={(index, kind) => setMessageFeedback(activeId, index, kind)}
                onRegenerate={(index) => void regenerateMessage(activeId, index)}
                selectedHistory={selectedHistory}
                onSelectHistory={setSelectedHistory}
                onOpenFile={(name) => setPreviewFile({ name, task: activeTask?.title || "未命名任务" })}
              />
            )}
          </main>
        </div>
      </div>

      {deliverablesOpen && (
        <DeliverablesDrawer tasks={tasks} onClose={() => setDeliverablesOpen(false)} />
      )}
      {previewFile && (
        <DeliveryPreviewModal
          name={previewFile.name}
          task={previewFile.task}
          onClose={() => setPreviewFile(null)}
        />
      )}
    </div>
  );
}

interface TasksViewProps {
  tasks: Task[];
  filtered: Task[];
  counts: Record<string, number>;
  selected?: Task;
  filter: string;
  search: string;
  onFilter: (f: string) => void;
  onSearch: (v: string) => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onOpenChat: (id: string) => void;
  onOpenDeliverables: () => void;
  onNewTask: () => void;
}

function TasksView(props: TasksViewProps) {
  const { tasks, filtered, counts, selected, filter, search, onFilter, onSearch, onSelect, onDelete, onOpenChat, onOpenDeliverables, onNewTask } = props;
  const allFiles = tasks.flatMap((t) => t.files.map((f) => ({ task: t, file: f })));
  const filters = [["all", "全部"], ["working", "进行中"], ["attention", "待处理"], ["done", "已完成"]] as const;
  return (
    <>
      <div className="ht-dt-head">
        <div><h2>任务总览</h2><p>跨群任务与交付物统一管理</p></div>
        <div className="ht-actions">
          <button type="button" className="ht-btn"><Download size={14} />导出</button>
          <button type="button" className="ht-btn primary" onClick={onNewTask}><Plus size={14} />新建任务</button>
        </div>
      </div>
      <div className="ht-stat-row">
        {statCell(String(counts.working), "进行中")}
        {statCell(String(counts.attention), "待处理")}
        <button type="button" className="ht-stat ht-stat-action" onClick={onOpenDeliverables}>
          <Package size={16} /><strong>{allFiles.length}</strong><em>交付物</em>
        </button>
        {statCell("128", "本月执行")}
      </div>
      <div className="ht-task-toolbar">
        <div className="ht-filter-chips">
          {filters.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`ht-chip${filter === key ? " primary" : ""}`}
              aria-pressed={filter === key}
              onClick={() => onFilter(key)}
            >
              {label} {counts[key]}
            </button>
          ))}
        </div>
        <label className="ht-task-search"><Search size={13} /><input placeholder="搜索任务或交付物" value={search} onChange={(e) => onSearch(e.target.value)} /></label>
      </div>
      <div className="ht-dt-split">
        <div>
          <div className="ht-table-wrap">
            <table className="ht-table">
              <thead><tr><th>任务</th><th>状态</th><th>进度</th><th>流程</th><th>负责人</th><th>更新时间</th><th></th></tr></thead>
              <tbody>
                {filtered.length === 0 && <tr><td colSpan={7} className="ht-table-empty">没有找到匹配的任务</td></tr>}
                {filtered.map((t) => (
                  <tr key={t.id} onClick={() => onSelect(t.id)} aria-selected={selected?.id === t.id}>
                    <td><div className="ht-cell-main"><strong>{t.title}</strong><em>{t.sop}</em></div></td>
                    <td>{statusPill(t.status)}</td>
                    <td><div className="ht-cell-progress"><div className="ht-bar"><i style={{ width: `${t.progress}%` }} /></div><small>{t.progress}%</small></div></td>
                    <td><span className="ht-cell-sop"><Workflow size={12} />{t.sop}</span></td>
                    <td><span className="ht-avatars"><span className="ht-avatar navy">海</span></span></td>
                    <td className="ht-muted">{t.updated}</td>
                    <td>
                      <button type="button" className="ht-row-delete" aria-label="删除任务" title="删除任务" onClick={(e) => { e.stopPropagation(); onDelete(t.id); }}><Trash2 size={14} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <aside className="ht-detail-side ht-task-aside">
          {selected && (
            <>
              <div className="ht-card">
                <div className="ht-section-label"><span>当前任务</span><span className="ht-version">{selected.sop}</span></div>
                <h3>{selected.title}</h3>
                <p>{selected.owner} · {selected.updated}</p>
                <div className="ht-bar" role="progressbar" aria-valuenow={selected.progress} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${selected.progress}%` }} /></div>
                <div className="ht-steps">{selected.steps.map((s, i) => <div key={i} className={`ht-step ${s.s}`}><span>{s.s === "done" ? <Check size={12} /> : i + 1}</span><em>{s.t}</em></div>)}</div>
                <div className="ht-actions">
                  <button type="button" className="ht-btn primary" onClick={() => onOpenChat(selected.id)}><MessageCircle size={13} />继续对话</button>
                  <button type="button" className="ht-btn" onClick={() => onDelete(selected.id)}><Trash2 size={13} />删除</button>
                </div>
              </div>
              <div className="ht-card">
                <div className="ht-section-label"><span>交付物</span><em>{allFiles.length}</em></div>
                <p>点击统计数字或下方按钮，从右侧打开全部交付物。</p>
                <button type="button" className="ht-btn soft" onClick={onOpenDeliverables}><Package size={13} />打开交付物侧栏</button>
              </div>
            </>
          )}
        </aside>
      </div>
    </>
  );
}

interface ChatViewProps {
  tasks: Task[];
  activeTask?: Task;
  taskIndex: number;
  messages: ChatMessage[];
  input: string;
  sending: boolean;
  error: string;
  mainView: "workspace" | "new-task";
  newDraft: string;
  onInput: (v: string) => void;
  onNewDraft: (v: string) => void;
  onSend: () => void;
  onPrev: () => void;
  onNext: () => void;
  onNewTask: () => void;
  onBackNew: () => void;
  onCreateTask: () => void;
  onOpenDeliverables: () => void;
  contextCollapsed: boolean;
  onToggleContext: () => void;
  pendingFiles: File[];
  onAddFiles: (files: File[]) => void;
  onRemoveFile: (index: number) => void;
  onStop: () => void;
  onFeedback: (index: number, kind: "up" | "down") => void;
  onRegenerate: (index: number) => void;
  selectedHistory: string;
  onSelectHistory: (id: string) => void;
  onOpenFile: (name: string) => void;
}

function ChatView(props: ChatViewProps) {
  const { tasks, activeTask, taskIndex, messages, input, sending, error, mainView, newDraft, onInput, onNewDraft, onSend, onPrev, onNext, onNewTask, onBackNew, onCreateTask, onOpenDeliverables, contextCollapsed, onToggleContext, pendingFiles, onAddFiles, onRemoveFile, onStop, onFeedback, onRegenerate, selectedHistory, onSelectHistory, onOpenFile } = props;
  const [executionOpen, setExecutionOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  if (mainView === "new-task") {
    return (
      <div className="cend2-shell cend2-full">
        <main className="cend2-main">
          <header className="cend2-topbar">
            <div className="cend2-top-left"><button type="button" className="cend2-iconbtn" aria-label="返回任务" onClick={onBackNew}><ArrowLeft size={18} /></button></div>
          </header>
          <div className="cend2-newtask-page">
            <div className="cend2-newtask-hero">
              <span className="cend2-newtask-eyebrow">新建任务/聊天</span>
              <h2>有什么可以帮您？</h2>
              <p>描述希望得到的结果、截止时间，以及手头已有的材料。发送后会进入任务分屏继续对话。</p>
            </div>
            {!sending && (
              <div className="cend2-newtask-presets">
                {NEW_TASK_PRESETS.map((preset) => {
                  const Icon = preset.icon;
                  return (
                    <button key={preset.label} type="button" onClick={() => onNewDraft(preset.prompt)}>
                      <Icon size={14} /><span>{preset.label}</span>
                    </button>
                  );
                })}
              </div>
            )}
            <div className="cend2-new-strip">
              <button type="button" className="cend2-attach" aria-label="添加附件"><Paperclip size={19} /></button>
              <textarea
                autoFocus
                placeholder={sending ? "正在创建任务…" : "描述一个任务，发送后进入分屏与 Agent 对话…"}
                value={newDraft}
                onChange={(e) => onNewDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !sending) {
                    e.preventDefault();
                    onCreateTask();
                  }
                }}
                disabled={sending}
              />
              <button type="button" className="cend2-send" aria-label="发送任务描述" onClick={onCreateTask}><Send size={16} /></button>
            </div>
            <div className="cend2-new-actions"><button type="button" onClick={onBackNew}><ArrowLeft size={15} />返回任务</button></div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="cend2-shell cend2-full">
      <main className="cend2-main">
        <div className={`cend2-unit${contextCollapsed ? " context-collapsed" : ""}`}>
          <div className="cend2-context">
            <div className="cend2-context-bar">
              <button type="button" className="cend2-iconbtn" aria-label="收起任务上下文栏" onClick={onToggleContext}><PanelLeftClose size={15} /></button>
              <span>任务上下文</span>
            </div>
            <div className="cend2-context-body">
              <section className="focus-state-banner">
                <div>
                  <span><Sparkles size={13} /> 任务摘要</span>
                  <strong>{activeTask?.title || "未命名任务"}</strong>
                  <p>{activeTask ? "Agent 正在推进任务，执行状态与交付物会随对话持续更新。" : "选择左侧任务查看执行上下文。"}</p>
                </div>
                <div className="focus-state-grid">
                  <span><em>状态</em><strong>{activeTask?.status ?? "—"}</strong></span>
                  <span><em>步骤</em><strong>{activeTask?.progress ?? 0}%</strong></span>
                  <span><em>当前阶段</em><strong>{activeTask?.steps.find((s) => s.s === "working")?.t || "等待下一步"}</strong></span>
                  <span><em>最近更新</em><strong>{activeTask?.updated ?? "刚刚"}</strong></span>
                </div>
              </section>

              {(activeTask?.steps?.length ?? 0) > 0 && (
                <section className="focus-execution-path" aria-label="任务执行路径">
                  <header><span><Zap size={13} />执行步骤</span></header>
                  <div>
                    {(activeTask?.steps || []).map((s, i) => (
                      <span key={i} className={s.s}>
                        <i>{s.s === "done" ? <Check size={10} /> : ""}</i>
                        <strong>{s.t}</strong>
                        <em>{s.s === "done" ? "已完成" : s.s === "working" ? "进行中" : "待推进"}</em>
                      </span>
                    ))}
                  </div>
                </section>
              )}

              <div className="focus-detail-columns">
                <section className="focus-task-history">
                  <header><div><History size={14} /><strong>任务历史</strong></div><span>2 条记录</span></header>
                  <div className="focus-history-list">
                    <button type="button" className={`focus-history-item segment${selectedHistory === "live" ? " active" : ""}`} aria-pressed={selectedHistory === "live"} onClick={() => onSelectHistory("live")}>
                      <span className="focus-history-icon"><ListTodo size={15} /></span>
                      <div><strong>当前子任务</strong><p>执行中 · 清单 0/3</p><em>刚刚</em></div>
                    </button>
                    <button type="button" className={`focus-history-item segment${selectedHistory === "started" ? " active" : ""}`} aria-pressed={selectedHistory === "started"} onClick={() => onSelectHistory("started")}>
                      <span className="focus-history-icon"><ListTodo size={15} /></span>
                      <div><strong>任务启动</strong><p>已归档</p><em>刚刚</em></div>
                    </button>
                  </div>
                </section>

                <section className="focus-delivery-history">
                  <header><div><FileArchive size={14} /><strong>历史交付物</strong></div><span>{(activeTask?.files || []).length} 份</span></header>
                  {(activeTask?.files || []).length ? (
                    <div className="focus-delivery-groups">
                      <div className="focus-delivery-group">
                        {(activeTask?.files || []).map((f, i) => (
                          <button key={i} type="button" aria-label={`查看历史交付物 ${f}`} onClick={() => onOpenFile(f)}>
                            <span className="focus-file-preview"><FileText size={15} /><i /><i /><i /></span>
                            <span className="focus-file-copy"><strong>{f}</strong><em>本会话历史交付物 · {activeTask?.updated}</em></span>
                            <ChevronRight size={15} />
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="focus-delivery-empty"><p>本会话尚未形成交付物；生成后会累计出现在这里。</p></div>
                  )}
                </section>
              </div>
            </div>
          </div>

          <section className="cend2-chat" aria-label="对话">
            <div className="cend2-chat-top">
              <div className="cend2-chat-heading">
                {contextCollapsed && (
                  <button type="button" className="cend2-iconbtn cend2-reopen-context" aria-label="展开任务上下文栏" onClick={onToggleContext}><PanelLeftOpen size={15} /></button>
                )}
                {brandMark("mini")}
                <span>任务海豚工作室 <strong>「{activeTask?.title || "未命名任务"}」</strong></span>
              </div>
              <div className="cend2-chat-pager">
                <button type="button" className="cend2-pager-btn" disabled={taskIndex <= 0} aria-label="上一任务" onClick={onPrev}><ArrowLeft size={14} /></button>
                <span>{String(taskIndex + 1).padStart(2, "0")} / {String(tasks.length).padStart(2, "0")}</span>
                <button type="button" className="cend2-pager-btn" disabled={taskIndex >= tasks.length - 1} aria-label="下一任务" onClick={onNext}><ArrowRight size={14} /></button>
              </div>
              <div className="cend2-quick">
                <span className="agent-status-tooltip-wrap">
                  <button type="button" className={`chat-top-icon ${sending ? "busy" : ""}`} aria-label={sending ? "Agent 正在思考执行任务" : "Agent 空闲"} title={sending ? "Agent 正在思考执行任务" : "Agent 空闲"}><Clock size={15} /></button>
                </span>
                <span className="agent-status-tooltip-wrap">
                  <button type="button" className={`chat-top-icon ${sending ? "busy" : ""}`} aria-label={sending ? "Agent 正在思考执行任务" : "Agent 思考完成，任务空闲"} title={sending ? "Agent 正在思考执行任务" : "Agent 思考完成，任务空闲"}><span className={`signal-orb ${sending ? "red" : "green"}`} /></button>
                </span>
                <span className="agent-status-tooltip-wrap">
                  <button type="button" className={`chat-top-icon ${(activeTask?.files?.length ?? 0) > 0 ? "has-delivery" : ""}`} aria-label={(activeTask?.files?.length ?? 0) > 0 ? "查看新交付物" : "暂无新交付物"} title={(activeTask?.files?.length ?? 0) > 0 ? "查看新交付物" : "暂无新交付物"} onClick={onOpenDeliverables}><Package size={15} /></button>
                </span>
                <button type="button" className="cend2-newtask-mini" onClick={onNewTask}><Plus size={13} />新建任务/聊天</button>
              </div>
            </div>

            <div className="focus-chat-thread">
              {messages.length === 0 ? (
                <div className="focus-chat-empty">
                  <span className="focus-chat-avatar agent">{brandMark("mini")}</span>
                  <p>向 Agent 发送消息，开始围绕「{activeTask?.title || "当前任务"}」继续推进。</p>
                </div>
              ) : (
                messages.map((m, i) => <ChatMessageItem key={i} msg={m} last={i === messages.length - 1} sending={sending} onFeedback={(kind) => onFeedback(i, kind)} onRegenerate={() => onRegenerate(i)} />)
              )}
              {error && <div className="cend2-error">{error}</div>}
            </div>

            {messages.length > 0 && activeTask && activeTask.steps.length > 0 && (
              <div className={`cend2-execution${executionOpen ? " open" : ""}`}>
                <button type="button" className="cend2-execution-toggle" onClick={() => setExecutionOpen((v) => !v)}><ChevronRight size={13} /><span>执行步骤</span><em>{activeTask.steps.filter((x) => x.s === "done").length} / {activeTask.steps.length}</em></button>
                {executionOpen && (
                  <div className="cend2-execution-body">
                    {(activeTask.steps || []).map((s, i) => <div key={i}>{stepChip(s)}</div>)}
                  </div>
                )}
              </div>
            )}

            <div className="cend2-pending">
              {pendingFiles.map((file, i) => (
                <span key={`${file.name}-${i}`} className="cend2-chip"><Paperclip size={12} /><em>{file.name}</em><button type="button" aria-label={`移除 ${file.name}`} onClick={() => onRemoveFile(i)}><X size={12} /></button></span>
              ))}
            </div>

            <div className="cend2-composer">
              <input ref={fileInputRef} type="file" multiple hidden onChange={(e) => { onAddFiles(Array.from(e.target.files || [])); e.target.value = ""; }} />
              <button type="button" className="cend2-attach" aria-label="添加附件" onClick={() => fileInputRef.current?.click()}><Paperclip size={18} /></button>
              <textarea
                value={input}
                onChange={(e) => onInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    onSend();
                  }
                }}
                placeholder={`告诉 Agent 如何继续「${activeTask?.title || "当前任务"}」…`}
              />
              {sending ? (
                <button type="button" className="cend2-send stop" aria-label="停止生成" title="停止生成" onClick={onStop}><Square size={14} fill="currentColor" /></button>
              ) : (
                <button type="button" className="cend2-send" aria-label="发送" disabled={!input.trim() && pendingFiles.length === 0} onClick={onSend}><Send size={16} /></button>
              )}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}

function ChatMessageItem({ msg, last, sending, onFeedback, onRegenerate }: { msg: ChatMessage; last: boolean; sending: boolean; onFeedback: (kind: "up" | "down") => void; onRegenerate: () => void }) {
  const role = msg.role === "user" ? "user" : "agent";
  return (
    <div className={`focus-chat-msg ${role}`}>
      <span className={`focus-chat-avatar ${role}`}>{role === "agent" ? brandMark("mini") : "我"}</span>
      <div className="focus-chat-body">
        <div className="focus-chat-speaker">{role === "agent" ? "HaiTun Agent" : "我"}</div>
        {role === "agent" && msg.tools && msg.tools.length > 0 && (
          <div className="focus-chat-turn-process">
            <div className="focus-chat-thinking focus-chat-tools is-open">
              <button type="button" className="focus-chat-thinking-toggle" aria-expanded="true">
                <ChevronRight size={14} className="focus-chat-thinking-chevron" aria-hidden />
                <span>已用工具 {msg.tools.length} 项</span>
              </button>
              <div className="focus-chat-tools-body" role="list" aria-label="工具调用">
                {msg.tools.map((line, i) => <div className="focus-chat-progress-line" role="listitem" key={i}>{line}</div>)}
              </div>
            </div>
          </div>
        )}
        <div className="focus-chat-bubble-wrap">
          {role === "user" && (
            <div className="focus-chat-side-actions">
              <button type="button" className="focus-chat-copy-btn" title="复制" aria-label="复制" onClick={() => void navigator.clipboard?.writeText(msg.text)}><Copy size={16} /></button>
            </div>
          )}
          {role === "agent" ? (
            msg.text ? (
              <div className="focus-chat-bubble" dangerouslySetInnerHTML={{ __html: marked.parse(msg.text, { async: false }) as string }} />
            ) : (
              <div className="focus-chat-bubble thinking">
                {sending && last ? <span className="typing" aria-label="正在输入"><i /><i /><i /></span> : ""}
              </div>
            )
          ) : (
            <div className="focus-chat-bubble">{msg.text || (sending && last ? "…" : "")}</div>
          )}
        </div>
        {msg.files && msg.files.length > 0 && (
          <div className="focus-chat-files">
            {msg.files.map((f, i) => <span className="focus-chat-file-chip" key={i}><span>{f}</span><em>预览</em></span>)}
          </div>
        )}
        {role === "agent" && (
          <div className="focus-chat-msg-actions" role="toolbar" aria-label="消息操作">
            <button type="button" className={`focus-chat-action-btn${msg.feedback === "up" ? " active" : ""}`} title={msg.feedback === "up" ? "取消点赞" : "点赞"} aria-pressed={msg.feedback === "up"} onClick={() => onFeedback("up")}><ThumbsUp size={16} /></button>
            <button type="button" className={`focus-chat-action-btn${msg.feedback === "down" ? " active" : ""}`} title={msg.feedback === "down" ? "取消点踩" : "点踩"} aria-pressed={msg.feedback === "down"} onClick={() => onFeedback("down")}><ThumbsDown size={16} /></button>
            <button type="button" className="focus-chat-action-btn" title={msg.failed ? "重试" : "重新生成"} aria-label={msg.failed ? "重试" : "重新生成"} onClick={onRegenerate}><RefreshCw size={16} /></button>
            <button type="button" className="focus-chat-action-btn" title="复制" aria-label="复制" onClick={() => void navigator.clipboard?.writeText(msg.text)}><Copy size={16} /></button>
          </div>
        )}
      </div>
    </div>
  );
}

function DeliverablesDrawer({ tasks, onClose }: { tasks: Task[]; onClose: () => void }) {
  const allFiles = tasks.flatMap((t) => t.files.map((f) => ({ task: t, file: f })));
  return (
    <div className="ht-overlay" onClick={onClose}>
      <aside className="ht-drawer ht-deliverables-drawer" role="dialog" aria-label="全部交付物" onClick={(e) => e.stopPropagation()}>
        <div className="ht-drawer-head">
          <div><h3>全部交付物</h3><p>{tasks.length} 个任务 · {allFiles.length} 份文件</p></div>
          <button type="button" className="ht-iconbtn" aria-label="关闭" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="ht-drawer-body">
          {allFiles.map((d, i) => (
            <div key={i} className="ht-drawer-file">
              <FileText size={16} />
              <div><strong>{d.file}</strong><em>{d.task.title} · {d.task.updated}</em></div>
              <button type="button">查看</button>
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}

function DeliveryPreviewModal({ name, task, onClose }: { name: string; task: string; onClose: () => void }) {
  return (
    <div className="ht-overlay" onClick={onClose}>
      <div className="ht-preview-modal" role="dialog" aria-modal="true" aria-label={`预览 ${name}`} onClick={(e) => e.stopPropagation()}>
        <header>
          <div><FileText size={18} /><strong>{name}</strong><em>{task}</em></div>
          <button type="button" className="ht-iconbtn" aria-label="关闭预览" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="ht-preview-body">
          <span className="ht-preview-icon"><FileText size={34} /></span>
          <p>交付物预览（原型）</p>
          <em>真实文件预览将在交付物接口接入后显示，当前先保留交互入口。</em>
        </div>
      </div>
    </div>
  );
}
