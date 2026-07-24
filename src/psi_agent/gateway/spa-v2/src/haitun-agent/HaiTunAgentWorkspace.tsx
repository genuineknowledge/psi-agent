import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronRight,
  Grid2X2,
  History,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  Plus,
  Search,
  Send,
  Square,
  SquareStack,
  X,
} from "lucide-react";
import {
  FormEvent,
  PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  DELIVERY_LABEL,
  OVERVIEW_LABEL,
  PENDING_LABEL,
  type CardTransition,
  type ChatFile,
  type ChatMessage,
  type MainView,
  type MessageFeedback,
  type SidebarPanel,
  type Task,
  type TaskTemplate,
} from "./model";

import {
  INITIAL_TEMPLATES,
  QUICK_ACTIONS,
} from "./demo-fixtures";

import { mobileHaptic, prefersReducedMotion } from "./client-feedback";
import {
  createSession,
  deleteSession,
  fetchHistory,
  fetchSessionTodos,
  generateTitle,
  listSessions,
  listTitles,
  setTitle,
  type AiInfo,
} from "../services/api";
import { ensureDefaultAi, pickPreferredAi, purgePlaceholderAis, writeStoredAiId } from "../services/bootstrapAi";
import { chatFileToFile, filesToChatFiles } from "../services/chatFiles";
import { filesFromClipboard } from "../services/clipboardFiles";
import { streamSessionChat } from "../services/chatStream";
import {
  historyToChat,
  historyToDeliverables,
  sessionToTask,
  titleFromPrompt,
  withDeliverables,
  withHistoricalDeliverables,
  withCompletedTurn,
  withTodoProgress,
} from "../services/sessionBridge";

const OVERVIEW_WELCOME: ChatMessage = {
  role: "agent",
  text: "工作区已连接 Gateway。新建任务或从侧栏打开历史 Session，即可与 Agent 真实对话。",
};

import {
  AgentMark,
  BrandLogo,
  TreasureVisual,
} from "./primitives";

import {
  CompactOverviewContext,
  CompactTaskContext,
  OverviewCard,
  TaskCard,
  TaskRow,
} from "./task-cards";

import { TaskFocusDetails } from "./task-focus-details";
import { FocusChatThread } from "./focus-chat-thread";

import { ArtifactDrawer } from "./workspace-overlays";

import { NewTaskWorkspace, TemplateLibrary } from "./secondary-views";
import UserHub from "../components/user-hub/UserHub";
import { collectDeliverableFiles } from "../utils/filePreviewUtils";

type Props = {
  workspace: string;
  onChangeWorkspace?: () => void;
};

export default function HaiTunAgentWorkspace({ workspace, onChangeWorkspace }: Props) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [templates, setTemplates] = useState<TaskTemplate[]>(INITIAL_TEMPLATES);
  const [aiId, setAiId] = useState<string | null>(null);
  const [bootReady, setBootReady] = useState(false);
  /** Only open Hub models when no AI is available after open-and-use (not on every refresh). */
  const [openModelsOnce, setOpenModelsOnce] = useState(false);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarPanel, setSidebarPanel] = useState<SidebarPanel>(null);
  const [artifactTask, setArtifactTask] = useState<Task | null>(null);
  const [artifactListMode, setArtifactListMode] = useState<"new" | "history">("new");
  const [artifactInitialFile, setArtifactInitialFile] = useState<string | undefined>(undefined);
  const [mainView, setMainView] = useState<MainView>("workspace");
  const [newTaskReturnView, setNewTaskReturnView] = useState<MainView>("workspace");
  const [newTaskSession, setNewTaskSession] = useState(0);
  const [newTaskDraft, setNewTaskDraft] = useState("");
  const [newTaskCategory, setNewTaskCategory] = useState("自由任务");
  const [messages, setMessages] = useState<Record<string, ChatMessage[]>>({
    overview: [OVERVIEW_WELCOME],
  });
  const [chatDrafts, setChatDrafts] = useState<Record<string, string>>({});
  const [chatAttachments, setChatAttachments] = useState<Record<string, File[]>>({});
  const [chatExpanded, setChatExpanded] = useState(false);
  const [contextPanelCollapsed, setContextPanelCollapsed] = useState(false);
  const [typingCard, setTypingCard] = useState<string | null>(null);
  const [dragX, setDragX] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [cardTransition, setCardTransition] = useState<CardTransition | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [globalSearch, setGlobalSearch] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [templateSearchSeed, setTemplateSearchSeed] = useState("");
  const dragOrigin = useRef<number | null>(null);
  const transitionTimer = useRef<number | null>(null);
  const toastTimer = useRef<number | null>(null);
  const globalSearchRef = useRef<HTMLInputElement | null>(null);
  const activeChatInputRef = useRef<HTMLInputElement | null>(null);
  const attachInputRef = useRef<HTMLInputElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  /** Bumped each runChatTurn so a superseded/aborted turn cannot keep appending deltas. */
  const streamEpochRef = useRef(0);
  /** After Stop, block submit briefly — Stop↔Send swap under the same click would re-send the restored draft. */
  const suppressSubmitUntilRef = useRef(0);
  const historyLoadedRef = useRef<Set<string>>(new Set(["overview"]));
  /** Invalidate in-flight todo polls so a late streaming refresh cannot reopen 「产出与确认」. */
  const todoRefreshSeqRef = useRef<Record<string, number>>({});
  const workspaceNorm = workspace.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();

  const cards = useMemo(() => [{ id: "overview", title: OVERVIEW_LABEL }, ...tasks.map((task) => ({ id: task.id, title: task.shortTitle }))], [tasks]);
  const currentTask = currentIndex === 0 ? null : tasks[currentIndex - 1];
  const currentCard = cards[currentIndex] ?? cards[0];
  const currentChatDraft = chatDrafts[currentCard.id] ?? "";
  const pendingTasks = tasks.filter((task) => task.status === "attention");
  const deliveryTasks = tasks.filter((task) => task.newDeliverables.length > 0);
  const normalizedSearch = globalSearch.trim().toLocaleLowerCase("zh-CN");
  const taskSearchResults = normalizedSearch
    ? tasks.filter((task) => `${task.title}${task.shortTitle}${task.category}${task.summary}${task.statusLabel}${task.deliverables.join(" ")}`.toLocaleLowerCase("zh-CN").includes(normalizedSearch)).slice(0, 4)
    : [];
  const templateSearchResults = normalizedSearch
    ? templates.filter((template) => `${template.title}${template.category}${template.description}${template.starterPrompt}${template.deliverables.join(" ")}`.toLocaleLowerCase("zh-CN").includes(normalizedSearch)).slice(0, 4)
    : [];

  const showToast = useCallback((message: string) => {
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    setToast(message);
    toastTimer.current = window.setTimeout(() => setToast(null), 2600);
  }, []);

  const refreshTodos = useCallback(async (taskId: string, streaming = false) => {
    if (taskId === "overview") return;
    const seq = (todoRefreshSeqRef.current[taskId] ?? 0) + 1;
    todoRefreshSeqRef.current[taskId] = seq;
    try {
      const data = await fetchSessionTodos(taskId);
      if (todoRefreshSeqRef.current[taskId] !== seq) return;
      const todos = Array.isArray(data.todos) ? data.todos : [];
      setTasks((current) =>
        current.map((task) => {
          if (task.id !== taskId) return task;
          return withTodoProgress(task, todos, {
            streaming,
            // Starting a stream clears settled; idle poll keeps prior settled flag.
            turnSettled: streaming ? false : task.turnSettled,
          });
        }),
      );
    } catch {
      // Missing file / transient — keep previous steps.
    }
  }, []);

  const ensureHistory = useCallback(async (taskId: string) => {
    if (taskId === "overview" || historyLoadedRef.current.has(taskId)) return;
    historyLoadedRef.current.add(taskId);
    try {
      const hist = await fetchHistory(taskId);
      const chat = historyToChat(hist);
      const { names, paths } = historyToDeliverables(hist);
      setMessages((current) => ({
        ...current,
        [taskId]: chat.length ? chat : (current[taskId] ?? []),
      }));
      setTasks((current) =>
        current.map((task) => {
          if (task.id !== taskId) return task;
          let next = names.length ? withHistoricalDeliverables(task, names, paths) : task;
          if (chat.length) {
            const lastAgent = [...chat].reverse().find((m) => m.role === "agent");
            if (lastAgent) {
              next = withCompletedTurn(
                {
                  ...next,
                  summary: lastAgent.text.slice(0, 120) + (lastAgent.text.length > 120 ? "…" : ""),
                  updated: names.length ? "已从历史同步交付物" : "已从历史同步",
                },
              );
            }
          }
          return next;
        }),
      );
      await refreshTodos(taskId);
    } catch (e) {
      historyLoadedRef.current.delete(taskId);
      showToast(e instanceof Error ? e.message : "加载历史失败");
    }
  }, [refreshTodos, showToast]);

  // While Agent runs, poll todos so middle step updates mid-turn (tool writes file).
  // Pass streaming=true so 「产出与确认」 stays working until the turn ends.
  useEffect(() => {
    if (!typingCard || typingCard === "overview") return;
    void refreshTodos(typingCard, true);
    const id = window.setInterval(() => {
      void refreshTodos(typingCard, true);
    }, 2500);
    return () => window.clearInterval(id);
  }, [typingCard, refreshTodos]);

  const openArtifact = useCallback((task: Task, fileName?: string, listMode?: "new" | "history") => {
    const mode = listMode
      ?? (fileName ? "history" : (task.newDeliverables.length ? "new" : "history"));
    setArtifactListMode(mode);
    setArtifactInitialFile(fileName);
    setArtifactTask(task);
  }, []);

  const closeArtifact = useCallback(() => {
    setArtifactTask(null);
    setArtifactInitialFile(undefined);
  }, []);

  useEffect(() => {
    let cancelled = false;
    ;(async () => {
      setBootReady(false);
      setOpenModelsOnce(false);
      try {
        // Empty pool → open models panel. Do NOT POST free defaults on boot.
        // Drop leftover haitun-default entries when the user already has a real key.
        let ais = await purgePlaceholderAis();
        if (cancelled) return;
        if (!Array.isArray(ais) || ais.length === 0) {
          setAiId(null);
          writeStoredAiId(null);
          setOpenModelsOnce(true);
          setBootReady(true);
          return;
        }
        const preferred = pickPreferredAi(ais, null);
        setAiId(preferred?.id ?? null);
        if (preferred?.id) writeStoredAiId(preferred.id);
        const [sessions, titles] = await Promise.all([listSessions(), listTitles()]);
        if (cancelled) return;
        const inWs = sessions.filter((s) => {
          const w = (s.workspace || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
          return !w || w === workspaceNorm;
        });
        const mapped = inWs.map((s) => sessionToTask(s, titles[s.id] || "新任务"));
        setTasks(mapped);
        historyLoadedRef.current = new Set(["overview"]);
        setMessages({ overview: [OVERVIEW_WELCOME] });
        setCurrentIndex(0);
      } catch (e) {
        if (!cancelled) {
          showToast(e instanceof Error ? e.message : "连接 Gateway 失败");
          setOpenModelsOnce(true);
        }
      } finally {
        if (!cancelled) setBootReady(true);
      }
    })();
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [workspaceNorm, showToast]);

  const collapseChat = useCallback(() => {
    setChatExpanded(false);
    setContextPanelCollapsed(false);
    activeChatInputRef.current?.blur();
  }, []);

  const goTo = useCallback((index: number, animate = true) => {
    const next = Math.max(0, Math.min(index, cards.length - 1));
    const fromExpanded = chatExpanded;
    collapseChat();
    if (next === currentIndex) {
      setDragX(0);
      return;
    }
    if (transitionTimer.current) window.clearTimeout(transitionTimer.current);
    if (animate && !prefersReducedMotion()) {
      setCardTransition({
        from: currentIndex,
        direction: next > currentIndex ? "next" : "previous",
        token: Date.now(),
        fromExpanded,
      });
      transitionTimer.current = window.setTimeout(() => setCardTransition(null), 470);
    } else {
      setCardTransition(null);
    }
    setCurrentIndex(next);
    setDragX(0);
    const card = cards[next];
    if (card && card.id !== "overview") void ensureHistory(card.id);
  }, [cards, chatExpanded, collapseChat, currentIndex, ensureHistory]);

  const selectTask = (task: Task) => {
    const index = tasks.findIndex((item) => item.id === task.id);
    if (index >= 0) goTo(index + 1);
    setMainView("workspace");
    setSidebarOpen(false);
    setSearchOpen(false);
    setGlobalSearch("");
  };

  const togglePanel = (panel: SidebarPanel) => {
    setSidebarPanel((current) => (current === panel ? null : panel));
    setSidebarOpen(true);
  };

  const goHome = useCallback(() => {
    setMainView("workspace");
    setSidebarPanel(null);
    setSidebarOpen(false);
    goTo(0);
  }, [goTo]);

  const deleteTask = useCallback(async (task: Task) => {
    const ok = window.confirm(`确认删除任务「${task.title}」？\n删除后 Session 与对话历史将无法恢复。`);
    if (!ok) return;

    if (typingCard === task.id) {
      abortRef.current?.abort();
      abortRef.current = null;
      setTypingCard(null);
    }

    try {
      await deleteSession(task.id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // 404: already gone on server — still clear local UI
      if (!/404|not found/i.test(msg)) {
        showToast(`删除失败：${msg}`);
        return;
      }
    }

    historyLoadedRef.current.delete(task.id);
    setTasks((current) => current.filter((item) => item.id !== task.id));
    setMessages((current) => {
      const next = { ...current };
      delete next[task.id];
      return next;
    });
    setChatDrafts((current) => {
      const next = { ...current };
      delete next[task.id];
      return next;
    });
    setChatAttachments((current) => {
      const next = { ...current };
      delete next[task.id];
      return next;
    });
    if (artifactTask?.id === task.id) closeArtifact();

    const deletedIndex = tasks.findIndex((item) => item.id === task.id);
    if (deletedIndex >= 0 && currentIndex === deletedIndex + 1) {
      goHome();
    } else if (deletedIndex >= 0 && currentIndex > deletedIndex + 1) {
      setCurrentIndex((i) => Math.max(0, i - 1));
    }

    showToast(`已删除任务「${task.shortTitle}」`);
  }, [artifactTask?.id, currentIndex, goHome, showToast, tasks, typingCard]);

  const openNewTask = useCallback((draft = "", category = "自由任务", returnView: MainView = "workspace") => {
    collapseChat();
    setNewTaskDraft(draft);
    setNewTaskCategory(category);
    setNewTaskReturnView(returnView);
    setNewTaskSession((current) => current + 1);
    setMainView("new-task");
    setSidebarPanel(null);
    setSidebarOpen(false);
  }, [collapseChat]);

  const openTemplates = useCallback(() => {
    collapseChat();
    setTemplateSearchSeed("");
    setMainView("templates");
    setSidebarPanel(null);
    setSidebarOpen(false);
  }, [collapseChat]);

  const appendStreamingAgent = (cardId: string, delta: string) => {
    setMessages((current) => {
      const list = [...(current[cardId] ?? [])];
      const last = list[list.length - 1];
      if (last?.role === "agent") {
        // Preserve files: blob may arrive before more text deltas.
        list[list.length - 1] = { ...last, text: last.text + delta };
      } else {
        list.push({ role: "agent", text: delta });
      }
      return { ...current, [cardId]: list };
    });
  };

  const isAbortError = (e: unknown) =>
    typeof e === "object" && e !== null && "name" in e && (e as { name: string }).name === "AbortError";

  /** Cursor-like stop: drop this turn's bubbles and put the draft back in the input. */
  const restoreStoppedTurn = (
    cardId: string,
    text: string,
    files: Array<File | ChatFile>,
  ) => {
    setMessages((current) => {
      const list = [...(current[cardId] ?? [])];
      if (list.at(-1)?.role === "agent") list.pop();
      if (list.at(-1)?.role === "user") list.pop();
      return { ...current, [cardId]: list };
    });
    const fileNames = files.map((f) => f.name).join("、");
    const uploadOnly =
      files.length > 0 && (!text.trim() || text === `已上传：${fileNames}`);
    setChatDrafts((current) => ({
      ...current,
      [cardId]: uploadOnly ? "" : text,
    }));
    setChatAttachments((current) => ({
      ...current,
      [cardId]: files.map((f) => (f instanceof File ? f : chatFileToFile(f))),
    }));
    queueMicrotask(() => activeChatInputRef.current?.focus());
  };

  /** Stream one turn; caller must already append user + empty agent (or replace agent stub). */
  const runChatTurn = async (
    cardId: string,
    text: string,
    files: Array<File | ChatFile> = [],
    titleSource?: string,
  ) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const epoch = ++streamEpochRef.current;
    const live = () => epoch === streamEpochRef.current && !controller.signal.aborted;

    setTypingCard(cardId);
    const userVisible = titleSource ?? (text.trim() || "附件");
    let turnOk = false;
    let replySummary = "";
    // Enter advance phase for this turn (layer-1); todos refine the middle label.
    setTasks((current) =>
      current.map((task) =>
        (task.id === cardId
          ? withTodoProgress(task, task.todoItems ?? [], { streaming: true, turnSettled: false })
          : task),
      ),
    );

    try {
      const { text: full, blobs } = await streamSessionChat(
        cardId,
        text,
        files,
        controller.signal,
        {
          onText: (delta) => {
            if (!live()) return;
            appendStreamingAgent(cardId, delta);
          },
          onBlob: (name, data) => {
            if (!live()) return;
            setTasks((current) =>
              current.map((task) =>
                (task.id === cardId
                  ? withDeliverables(task, [name], { streaming: true })
                  : task),
              ),
            );
            setMessages((current) => {
              const list = [...(current[cardId] ?? [])];
              const last = list[list.length - 1];
              if (last?.role === "agent") {
                list[list.length - 1] = {
                  ...last,
                  files: [...(last.files ?? []), { name, data }],
                };
              }
              return { ...current, [cardId]: list };
            });
          },
        },
      );
      // Some browsers end the body with done instead of throwing AbortError.
      if (!live()) {
        if (epoch === streamEpochRef.current) restoreStoppedTurn(cardId, text, files);
        return;
      }
      turnOk = true;
      replySummary = full.trim();
      if (!full.trim()) {
        setMessages((current) => {
          const list = [...(current[cardId] ?? [])];
          const last = list[list.length - 1];
          if (last?.role === "agent" && !last.text.trim() && !(last.files?.length)) {
            list[list.length - 1] = { ...last, text: "（本轮无文本回复；若正在跑工具，请稍候在历史中查看。）" };
          }
          return { ...current, [cardId]: list };
        });
      }
      if (blobs.length) {
        setTasks((current) =>
          current.map((task) =>
            (task.id === cardId
              ? withDeliverables(task, blobs.map((b) => b.name), { streaming: false })
              : task),
          ),
        );
      }
      const title = tasks.find((t) => t.id === cardId)?.title;
      if (!title || title === "新任务") {
        void generateTitle(cardId, userVisible, full.slice(0, 400)).then((res) => {
          if (res?.title) {
            setTasks((current) =>
              current.map((task) =>
                task.id === cardId
                  ? { ...task, title: res.title!, shortTitle: res.title!.slice(0, 10) + (res.title!.length > 10 ? "…" : "") }
                  : task,
              ),
            );
          }
        }).catch(() => {});
      }
    } catch (e) {
      if (isAbortError(e) || controller.signal.aborted) {
        if (epoch === streamEpochRef.current) restoreStoppedTurn(cardId, text, files);
        return;
      }
      if (epoch !== streamEpochRef.current) return;
      const err = e instanceof Error ? e.message : String(e);
      setMessages((current) => {
        const list = [...(current[cardId] ?? [])];
        for (let i = list.length - 1; i >= 0; i--) {
          if (list[i]?.role === "user") {
            list[i] = { ...list[i]!, failed: true, failedReason: "error" };
            break;
          }
        }
        const last = list[list.length - 1];
        if (last?.role === "agent") {
          list[list.length - 1] = {
            ...last,
            text: last.text || `[错误] ${err}`,
          };
        } else {
          list.push({ role: "agent", text: `[错误] ${err}` });
        }
        return { ...current, [cardId]: list };
      });
      showToast(err);
    } finally {
      if (epoch === streamEpochRef.current) {
        setTypingCard((current) => (current === cardId ? null : current));
        if (abortRef.current === controller) abortRef.current = null;
      }
      void (async () => {
        await refreshTodos(cardId, false);
        if (!turnOk || epoch !== streamEpochRef.current) return;
        setTasks((current) =>
          current.map((task) =>
            (task.id === cardId
              ? withCompletedTurn(task, { summary: replySummary || undefined })
              : task),
          ),
        );
      })();
    }
  };

  const stopChat = useCallback(() => {
    // Same pointer gesture must not land on the Send button that replaces Stop
    // after typingCard clears — especially once we restore the draft text.
    suppressSubmitUntilRef.current = Date.now() + 400;
    abortRef.current?.abort();
  }, []);

  const sendMessage = async (text: string, cardId = currentCard.id, files: File[] = []) => {
    if (Date.now() < suppressSubmitUntilRef.current) return;
    const clean = text.trim();
    const pendingFiles = files.length ? files : (chatAttachments[cardId] ?? []);
    if (!clean && !pendingFiles.length) return;
    const userVisible = clean || `已上传：${pendingFiles.map((file) => file.name).join("、")}`;
    if (cardId === "overview") {
      setMessages((current) => ({
        ...current,
        overview: [
          ...(current.overview ?? []),
          { role: "user", text: userVisible },
          { role: "agent", text: "请先新建任务或打开历史任务；总览卡片不直接调用模型。" },
        ],
      }));
      setChatDrafts((current) => ({ ...current, overview: "" }));
      setChatAttachments((current) => ({ ...current, overview: [] }));
      return;
    }

    const storedFiles = pendingFiles.length ? await filesToChatFiles(pendingFiles) : [];
    setMessages((current) => ({
      ...current,
      [cardId]: [
        ...(current[cardId] ?? []),
        { role: "user", text: userVisible, files: storedFiles.length ? storedFiles : undefined },
        { role: "agent", text: "" },
      ],
    }));
    setChatDrafts((current) => ({ ...current, [cardId]: "" }));
    setChatAttachments((current) => ({ ...current, [cardId]: [] }));
    await runChatTurn(cardId, clean, pendingFiles, userVisible);
  };

  const setMessageFeedback = (cardId: string, index: number, kind: Exclude<MessageFeedback, "">) => {
    setMessages((current) => {
      const list = [...(current[cardId] ?? [])];
      const msg = list[index];
      if (!msg || msg.role !== "agent") return current;
      list[index] = { ...msg, feedback: msg.feedback === kind ? "" : kind };
      return { ...current, [cardId]: list };
    });
  };

  const regenerateAgentMessage = async (cardId: string, agentIndex: number) => {
    if (typingCard === cardId || cardId === "overview") return;
    const list = messages[cardId] ?? [];
    const agent = list[agentIndex];
    if (!agent || agent.role !== "agent") return;
    let userIndex = -1;
    for (let i = agentIndex - 1; i >= 0; i--) {
      if (list[i]?.role === "user") {
        userIndex = i;
        break;
      }
    }
    if (userIndex < 0) return;
    const userMsg = list[userIndex]!;
    const text = userMsg.text;
    const files = userMsg.files ?? [];
    setMessages((current) => {
      const next = [...(current[cardId] ?? [])];
      next.splice(agentIndex, 1, { role: "agent", text: "" });
      if (next[userIndex]?.role === "user") {
        next[userIndex] = { ...next[userIndex]!, failed: false, failedReason: undefined };
      }
      return { ...current, [cardId]: next };
    });
    await runChatTurn(cardId, text, files, text);
  };

  const retryFailedMessage = async (cardId: string, userIndex: number) => {
    if (typingCard === cardId || cardId === "overview") return;
    const list = messages[cardId] ?? [];
    const userMsg = list[userIndex];
    if (!userMsg || userMsg.role !== "user" || !userMsg.failed) return;
    const text = userMsg.text;
    const files = userMsg.files ?? [];
    setMessages((current) => {
      const next = [...(current[cardId] ?? [])];
      const after = next[userIndex + 1];
      const removeCount = after?.role === "agent" ? 2 : 1;
      next.splice(userIndex, removeCount, { role: "user", text, files }, { role: "agent", text: "" });
      return { ...current, [cardId]: next };
    });
    await runChatTurn(cardId, text, files, text);
  };

  const handleChatSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (typingCard) return;
    if (Date.now() < suppressSubmitUntilRef.current) return;
    const files = chatAttachments[currentCard.id] ?? [];
    if (!currentChatDraft.trim() && !files.length) return;
    if (!chatExpanded) setChatExpanded(true);
    void sendMessage(currentChatDraft, currentCard.id, files);
  };

  const addChatAttachments = (cardId: string, fileList: FileList | File[] | null) => {
    if (!fileList?.length) return;
    const next = Array.from(fileList);
    setChatAttachments((current) => ({
      ...current,
      [cardId]: [...(current[cardId] ?? []), ...next],
    }));
  };

  /** Paste any clipboard file (screenshot, copied file, …) ≡ paperclip attach. */
  const handleChatPaste = (cardId: string, event: React.ClipboardEvent<HTMLInputElement>) => {
    const files = filesFromClipboard(event.clipboardData);
    if (!files.length) return;
    addChatAttachments(cardId, files);
    const text = event.clipboardData.getData("text/plain");
    // File-only paste (e.g. screenshot): block browser stuffing binary into the input.
    if (!text) event.preventDefault();
  };

  const removeChatAttachment = (cardId: string, index: number) => {
    setChatAttachments((current) => ({
      ...current,
      [cardId]: (current[cardId] ?? []).filter((_, i) => i !== index),
    }));
  };

  const expandChatFromStrip = () => {
    if (!chatExpanded) setChatExpanded(true);
  };

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if ((event.target as HTMLElement).closest("button, input, textarea, a")) return;
    dragOrigin.current = event.clientX;
    setIsDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragOrigin.current === null) return;
    setDragX(Math.max(-120, Math.min(120, event.clientX - dragOrigin.current)));
  };

  const handlePointerUp = () => {
    if (dragX < -58) {
      mobileHaptic(8);
      goTo(currentIndex + 1);
    } else if (dragX > 58) {
      mobileHaptic(8);
      goTo(currentIndex - 1);
    }
    else setDragX(0);
    dragOrigin.current = null;
    setIsDragging(false);
  };

  const createTask = async (description: string, category: string, files: File[] = []) => {
    const clean = description.trim();
    const pendingFiles = files;
    const userVisible =
      clean || (pendingFiles.length ? `已上传：${pendingFiles.map((file) => file.name).join("、")}` : "");
    if (!userVisible) throw new Error("empty task");

    // Always re-resolve against the live pool so leftover haitun-default never wins.
    const ais = await purgePlaceholderAis();
    let resolvedAiId = pickPreferredAi(ais, aiId)?.id ?? null;
    if (!resolvedAiId) {
      // Empty pool (free mode) → resolve remote defaults only when a task needs an AI.
      const ai = await ensureDefaultAi(aiId);
      if (!ai?.id) {
        showToast("没有可用 AI，请先在大模型中连接，或检查免费模型网络");
        setOpenModelsOnce(true);
        throw new Error("no ai");
      }
      resolvedAiId = ai.id;
    }
    setAiId(resolvedAiId);
    writeStoredAiId(resolvedAiId);
    const title = titleFromPrompt(clean || userVisible);
    let session;
    try {
      session = await createSession(resolvedAiId, workspace);
    } catch (e) {
      showToast(e instanceof Error ? e.message : "创建任务失败");
      throw e;
    }
    await setTitle(session.id, title).catch(() => {});
    const summarySeed = clean || userVisible;
    const newTask = {
      ...sessionToTask(session, title, {
        summary: `Agent 已收到任务描述：“${summarySeed.slice(0, 58)}${summarySeed.length > 58 ? "…" : ""}”`,
        status: "working",
        progress: 8,
      }),
      category: category || "自由任务",
    };
    historyLoadedRef.current.add(session.id);
    setTasks((current) => [...current, newTask]);
    const storedFiles = pendingFiles.length ? await filesToChatFiles(pendingFiles) : [];
    setMessages((current) => ({
      ...current,
      [newTask.id]: [
        {
          role: "user",
          text: userVisible,
          files: storedFiles.length ? storedFiles : undefined,
        },
        { role: "agent", text: "" },
      ],
    }));
    setToast("新任务已创建，正在请求 Agent…");
    window.setTimeout(() => setToast(null), 2600);

    // Same multipart path as overview/focus chat composer (text + File attachments).
    void runChatTurn(newTask.id, clean, pendingFiles, userVisible);

    return newTask;
  };

  /** Preset chip → expand focus chat, then same path as a typed send (stream in FocusChatThread). */
  const sendQuickAction = async (action: string, cardId: string) => {
    const clean = action.trim();
    if (!clean) return;
    if (typingCard === cardId) return;

    if (cardId === "overview") {
      // Overview has no Session — create a task and jump into its dialog.
      const nextIndex = tasks.length + 1;
      try {
        await createTask(clean, "自由任务");
        setMainView("workspace");
        setSidebarOpen(false);
        setSearchOpen(false);
        setCurrentIndex(nextIndex);
        setDragX(0);
        setChatExpanded(true);
      } catch {
        // createTask already toasted
      }
      return;
    }

    if (!chatExpanded) setChatExpanded(true);
    await sendMessage(clean, cardId);
  };

  const viewCreatedTask = (task: Task) => {
    // Prefer task id; fall back to "just appended" index (same as overview quick-create).
    const index = tasks.findIndex((item) => item.id === task.id);
    const nextIndex = index >= 0 ? index + 1 : tasks.length + 1;
    setMainView("workspace");
    setSidebarOpen(false);
    setSearchOpen(false);
    setGlobalSearch("");
    setCurrentIndex(nextIndex);
    setDragX(0);
    setCardTransition(null);
    setChatExpanded(true);
  };

  const useTemplate = (template: TaskTemplate) => {
    openNewTask(template.starterPrompt, template.category, "templates");
  };

  const createTemplate = (title: string, category: string, prompt: string) => {
    setTemplates((current) => [
      ...current,
      {
        id: `template-${Date.now()}`,
        title,
        category,
        description: "由您沉淀的可复用任务模板。",
        starterPrompt: prompt,
        deliverables: ["按任务生成交付物"],
        cadence: "自定义",
        icon: SquareStack,
      },
    ]);
    setToast("新模板已保存到模板库");
    window.setTimeout(() => setToast(null), 2400);
  };

  const saveArtifact = (task: Task) => {
    setTasks((current) => current.map((item) => item.id === task.id
      ? {
          ...item,
          newDeliverables: [],
          deliveryState: "saved",
          updated: "刚刚保存交付物",
        }
      : item));
    setMessages((current) => ({
      ...current,
      [task.id]: [...(current[task.id] ?? []), { role: "agent", text: "交付物已保存到成果库。本会话历史交付物仍保留，您仍可基于本次成果继续迭代。" }],
    }));
    closeArtifact();
    setToast("交付物已保存到成果库");
    window.setTimeout(() => setToast(null), 2600);
  };

  const reviseArtifact = (task: Task) => {
    setTasks((current) => current.map((item) => item.id === task.id
      ? { ...item, status: "working", statusLabel: "按意见修改中", deliveryState: "generating", progress: Math.min(item.progress, 92), updated: "刚刚收到修改要求" }
      : item));
    closeArtifact();
    setToast("修改要求已发送，任务重新开始推进");
    window.setTimeout(() => setToast(null), 2600);
  };

  useEffect(() => {
    document.documentElement.dataset.haptics = "on";
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSidebarCollapsed(false);
        setSidebarOpen(true);
        setSearchOpen(true);
        window.setTimeout(() => globalSearchRef.current?.focus(), 50);
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "n") {
        event.preventDefault();
        openNewTask();
        return;
      }
      if (artifactTask) {
        if (event.key === "Escape") {
          closeArtifact();
        }
        return;
      }
      if (searchOpen && event.key === "Escape") {
        setSearchOpen(false);
        return;
      }
      if (chatExpanded && event.key === "Escape") {
        collapseChat();
        return;
      }
      const target = event.target as HTMLElement;
      if (["INPUT", "TEXTAREA"].includes(target.tagName)) return;
      if (mainView !== "workspace") {
        if (event.key === "Escape") {
          if (mainView === "new-task") setMainView(newTaskReturnView);
          else goHome();
        }
        return;
      }
      if (event.key === "ArrowLeft") goTo(currentIndex - 1);
      if (event.key === "ArrowRight") goTo(currentIndex + 1);
      if (event.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [artifactTask, chatExpanded, collapseChat, currentIndex, goHome, goTo, mainView, newTaskReturnView, openNewTask, searchOpen]);

  useEffect(() => () => {
    if (transitionTimer.current) window.clearTimeout(transitionTimer.current);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
  }, []);

  const visibleSidebarTasks = sidebarPanel === "pending" ? pendingTasks : sidebarPanel === "deliveries" ? deliveryTasks : tasks;
  const renderCardAt = (index: number) => {
    const task = index === 0 ? null : tasks[index - 1];
    return task ? <TaskCard task={task} onOpenArtifact={openArtifact} onDelete={deleteTask} /> : <OverviewCard tasks={tasks} />;
  };

  const renderTaskUnit = (index: number, interactive: boolean, visualExpanded = false) => {
    const unitCard = cards[index] ?? cards[0];
    const unitTask = index === 0 ? null : tasks[index - 1];
    const unitMessages = messages[unitCard.id] ?? [];
    const unitDraft = chatDrafts[unitCard.id] ?? "";
    const expanded = interactive ? chatExpanded : visualExpanded;

    return (
      <div className={`card-chat-unit ${expanded ? "chat-expanded" : ""} ${expanded && contextPanelCollapsed ? "context-collapsed" : ""}`}>
        <div className="mobile-card-peek" aria-hidden="true" />
        <div className="card-chat-pair">
        <div className="task-context-stack">
          {expanded && interactive && (
            <div className="context-panel-toolbar">
              <button
                type="button"
                className="context-panel-toggle"
                onClick={() => setContextPanelCollapsed(true)}
                aria-label="收起任务卡片栏"
                aria-expanded={!contextPanelCollapsed}
              >
                <PanelLeftClose size={15} />
              </button>
              <span className="context-panel-toolbar-label">任务卡片</span>
            </div>
          )}
          <div className="card-transition-frame">
            <div
              className={`card-swipe-surface ${interactive && isDragging ? "dragging" : ""}`}
              onPointerDown={interactive ? handlePointerDown : undefined}
              onPointerMove={interactive ? handlePointerMove : undefined}
              onPointerUp={interactive ? handlePointerUp : undefined}
              onPointerCancel={interactive ? handlePointerUp : undefined}
              aria-hidden={expanded || undefined}
              inert={expanded ? true : undefined}
            >
              {renderCardAt(index)}
            </div>
            <div className="compact-card-layer" aria-hidden={!expanded} inert={!expanded ? true : undefined}>
              {expanded ? (
                <div className="compact-card-shell focus-info-shell">
                  <TaskFocusDetails
                    task={unitTask}
                    tasks={tasks}
                    onOpenArtifact={openArtifact}
                  />
                </div>
              ) : unitTask ? (
                <CompactTaskContext task={unitTask} onOpenArtifact={openArtifact} onDelete={interactive ? deleteTask : undefined} />
              ) : (
                <CompactOverviewContext
                  tasks={tasks}
                />
              )}
            </div>
          </div>

          {!expanded && (
            <div className="card-pagination" aria-label="卡片分页">
              <span className="swipe-hint"><ArrowLeft size={12} /> 整体滑动切换 <ArrowRight size={12} /></span>
              <div>
                {cards.map((card, cardIndex) => (
                  <button
                    key={card.id}
                    type="button"
                    className={index === cardIndex ? "active" : ""}
                    onClick={() => interactive && goTo(cardIndex)}
                    disabled={!interactive}
                    aria-label={`切换到${card.title}`}
                  />
                ))}
              </div>
              <span>{String(index + 1).padStart(2, "0")} / {String(cards.length).padStart(2, "0")}</span>
            </div>
          )}
        </div>

        <section
          className="context-chat"
          aria-label={`关于${unitCard.title}的对话`}
          onClick={(event) => {
            if (!interactive || expanded) return;
            if ((event.target as HTMLElement).closest("[data-attach-control], button, a")) return;
            setChatExpanded(true);
          }}
        >
          <div className="chat-context-row">
            <div>
              {expanded && interactive && contextPanelCollapsed && (
                <button
                  type="button"
                  className="context-panel-toggle context-panel-toggle-in-chat"
                  onClick={() => setContextPanelCollapsed(false)}
                  aria-label="展开任务卡片栏"
                  aria-expanded={false}
                >
                  <PanelLeftOpen size={15} />
                </button>
              )}
              <AgentMark /><span>{expanded ? "任务工作区" : "关于"} <strong>{unitCard.title}</strong>{!expanded && " 的对话"}</span>
            </div>
            <div className="quick-actions">
              {expanded && (
                <button type="button" className="chat-collapse" onClick={collapseChat}>
                  <ChevronDown size={13} /> 收起
                </button>
              )}
              {!expanded && QUICK_ACTIONS.map((action) => (
                <button
                  type="button"
                  key={action}
                  disabled={!interactive || typingCard === unitCard.id}
                  onClick={(event) => {
                    event.stopPropagation();
                    if (!interactive || typingCard === unitCard.id) return;
                    void sendQuickAction(action, unitCard.id);
                  }}
                >
                  {action}
                </button>
              ))}
            </div>
          </div>

          {expanded && (
            <FocusChatThread
              messages={unitMessages}
              typing={typingCard === unitCard.id}
              title={unitCard.title}
              onFeedback={(index, kind) => setMessageFeedback(unitCard.id, index, kind)}
              onRegenerate={(index) => void regenerateAgentMessage(unitCard.id, index)}
              onRetry={(index) => void retryFailedMessage(unitCard.id, index)}
            />
          )}

          {!expanded && <div className="latest-message">
            {unitMessages.slice(-1).map((message, messageIndex) => (
              <span key={`${message.role}-${messageIndex}`} className={message.role}>{message.text}</span>
            ))}
            {typingCard === unitCard.id && <span className="typing"><i /><i /><i /></span>}
          </div>}

          {(chatAttachments[unitCard.id] ?? []).length > 0 && (
            <div className="chat-pending-files" data-attach-control>
              {(chatAttachments[unitCard.id] ?? []).map((file, index) => (
                <span className="chat-pending-chip" key={`${file.name}-${file.size}-${index}`}>
                  <Paperclip size={13} />
                  <em>{file.name}</em>
                  <button
                    type="button"
                    data-attach-control
                    disabled={!interactive}
                    onClick={(event) => {
                      event.stopPropagation();
                      if (interactive) removeChatAttachment(unitCard.id, index);
                    }}
                    aria-label={`移除 ${file.name}`}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}

          <form
            onSubmit={interactive ? handleChatSubmit : (event) => event.preventDefault()}
            onClick={(event) => {
              if (!interactive || expanded) return;
              if ((event.target as HTMLElement).closest("[data-attach-control]")) return;
              expandChatFromStrip();
            }}
          >
            <button
              type="button"
              className="chat-attach-button"
              data-attach-control
              disabled={!interactive}
              onClick={(event) => {
                event.stopPropagation();
                if (interactive) attachInputRef.current?.click();
              }}
              aria-label="添加附件"
            >
              <Paperclip size={20} />
            </button>
            <input
              ref={interactive ? attachInputRef : undefined}
              data-attach-control
              type="file"
              multiple
              hidden
              onChange={(event) => {
                if (!interactive) return;
                addChatAttachments(unitCard.id, event.target.files);
                event.target.value = "";
              }}
            />
            <input
              ref={interactive ? activeChatInputRef : undefined}
              value={unitDraft}
              onChange={(event) => interactive && setChatDrafts((current) => ({ ...current, [unitCard.id]: event.target.value }))}
              onFocus={() => interactive && setChatExpanded(true)}
              onPaste={(event) => {
                if (!interactive) return;
                handleChatPaste(unitCard.id, event);
              }}
              placeholder={`告诉 Agent 如何继续「${unitCard.title}」…`}
              aria-label="对话内容"
              readOnly={!interactive}
            />
            {typingCard === unitCard.id ? (
              <button
                type="button"
                className="send-button stop-button"
                data-attach-control
                disabled={!interactive}
                onPointerDown={(event) => {
                  // preventDefault: avoid mouseup activating the Send that replaces this button.
                  event.preventDefault();
                  event.stopPropagation();
                  if (interactive) stopChat();
                }}
                aria-label="停止生成"
                title="停止生成"
              >
                <Square size={14} fill="currentColor" />
              </button>
            ) : (
              <button
                type="submit"
                className="send-button"
                disabled={!interactive || (!unitDraft.trim() && !(chatAttachments[unitCard.id] ?? []).length)}
                aria-label="发送"
                title="发送"
              >
                <Send size={16} />
              </button>
            )}
          </form>
        </section>
        </div>
      </div>
    );
  };

  const liveArtifactTask = artifactTask
    ? (tasks.find((t) => t.id === artifactTask.id) ?? artifactTask)
    : null;

  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`} data-main-view={mainView}>
      {!bootReady && (
        <div className="workspace-gate" aria-busy="true">
          <div className="workspace-gate-card">
            <BrandLogo size="hero" />
            <p>正在连接 Gateway…</p>
          </div>
        </div>
      )}
      <button
        type="button"
        className={`mobile-sidebar-scrim ${sidebarOpen ? "visible" : ""}`}
        onClick={() => setSidebarOpen(false)}
        aria-label="关闭侧边栏"
      />

      <aside id="main-sidebar" className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <div className="sidebar-topline">
          <div className="signal-controls" aria-label="任务提醒">
            <button type="button" className={sidebarPanel === "pending" ? "active" : ""} onClick={() => togglePanel("pending")}>
              <span className="signal-orb red"><span>{pendingTasks.length}</span></span>
              <span>{PENDING_LABEL}</span>
            </button>
            <button type="button" className={sidebarPanel === "deliveries" ? "active" : ""} onClick={() => togglePanel("deliveries")}>
              <span className="signal-treasure"><TreasureVisual state="ready" size="mini" /><span>{deliveryTasks.length}</span></span>
              <span>{DELIVERY_LABEL}</span>
            </button>
          </div>
          <div className="sidebar-topline-actions">
            <button
              type="button"
              className="sidebar-collapse-btn desktop-only"
              onClick={() => setSidebarCollapsed(true)}
              aria-label="收起左侧边栏"
              aria-controls="main-sidebar"
            >
              <PanelLeftClose size={16} />
            </button>
            <button type="button" className="mobile-close" onClick={() => setSidebarOpen(false)} aria-label="收起侧边栏"><X size={18} /></button>
          </div>
        </div>

        <button type="button" className="brand-block" onClick={goHome} aria-label={`返回 HaiTun Agent ${OVERVIEW_LABEL}`}>
          <BrandLogo />
          <div><strong>HaiTun</strong><span>Agent</span></div>
        </button>

        <button type="button" className="new-task-button" onClick={() => openNewTask()}>
          <Plus size={18} /> 新建任务 <span>⌘ / Ctrl N</span>
        </button>

        <div className={`global-search ${searchOpen ? "open" : ""}`}>
          <label>
            <Search size={15} />
            <input
              ref={globalSearchRef}
              value={globalSearch}
              onFocus={() => setSearchOpen(true)}
              onChange={(event) => { setGlobalSearch(event.target.value); setSearchOpen(true); }}
              placeholder="搜索任务或模板"
              aria-label="全局搜索任务或模板"
            />
            <kbd>⌘ K</kbd>
          </label>
          {searchOpen && normalizedSearch && (
            <div className="global-search-results">
              {taskSearchResults.length > 0 && <span className="search-group-title">历史任务</span>}
              {taskSearchResults.map((task) => (
                <button type="button" key={task.id} onClick={() => selectTask(task)}>
                  <History size={14} /><span><strong>{task.shortTitle}</strong><em>{task.category} · {task.statusLabel}</em></span><ChevronRight size={13} />
                </button>
              ))}
              {templateSearchResults.length > 0 && <span className="search-group-title">任务模板</span>}
              {templateSearchResults.map((template) => (
                <button type="button" key={template.id} onClick={() => {
                  setTemplateSearchSeed(template.title);
                  setMainView("templates");
                  setSidebarPanel(null);
                  setSidebarOpen(false);
                  setSearchOpen(false);
                }}>
                  <SquareStack size={14} /><span><strong>{template.title}</strong><em>{template.category} · 进入模板库查看</em></span><ChevronRight size={13} />
                </button>
              ))}
              {!taskSearchResults.length && !templateSearchResults.length && <div className="search-empty">没有找到匹配的任务或模板</div>}
            </div>
          )}
        </div>

        <nav className="primary-nav" aria-label="主导航">
          <button type="button" className={mainView === "workspace" && currentIndex === 0 && !sidebarPanel ? "active" : ""} onClick={goHome}>
            <Grid2X2 size={18} /> {OVERVIEW_LABEL} <ChevronRight size={15} />
          </button>
          <button type="button" className={mainView === "workspace" && (sidebarPanel === "history" || sidebarPanel === "pending" || sidebarPanel === "deliveries") ? "active" : ""} onClick={() => { setMainView("workspace"); setSidebarPanel((current) => current === "history" ? null : "history"); }}>
            <History size={18} /> 历史任务 {(sidebarPanel === "history" || sidebarPanel === "pending" || sidebarPanel === "deliveries") ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          </button>

          <div className={`sidebar-task-panel ${sidebarPanel ? "visible" : ""}`}>
            <div className="panel-heading">
              <span>
                {sidebarPanel === "pending" ? PENDING_LABEL : sidebarPanel === "deliveries" ? DELIVERY_LABEL : "最近任务"}
              </span>
              <em>{visibleSidebarTasks.length}</em>
            </div>
            <div className="task-list">
              {visibleSidebarTasks.map((task) => (
                <TaskRow
                  key={task.id}
                  task={task}
                  active={currentTask?.id === task.id}
                  onSelect={() => selectTask(task)}
                  onOpenArtifact={openArtifact}
                  onDelete={deleteTask}
                />
              ))}
            </div>
          </div>

          <button type="button" className={mainView === "templates" ? "active" : ""} onClick={openTemplates}>
            <SquareStack size={18} /> 任务模板 <ChevronRight size={15} />
          </button>
        </nav>

        <div className="sidebar-spacer" />
        <div className="sidebar-account">
          <UserHub
            selectedAiId={aiId}
            onSelectAi={(id) => {
              setAiId(id);
              writeStoredAiId(id);
            }}
            workspace={workspace}
            onChangeWorkspace={onChangeWorkspace}
            onToast={showToast}
            openModelsOnMount={bootReady && openModelsOnce}
            onModelsAutoOpened={() => setOpenModelsOnce(false)}
            onAisChanged={(ais: AiInfo[]) => {
              if (ais.length === 0) {
                setAiId(null);
                writeStoredAiId(null);
                return;
              }
              const preferred = pickPreferredAi(ais, aiId);
              setAiId(preferred?.id ?? null);
              if (preferred?.id) writeStoredAiId(preferred.id);
            }}
          />
        </div>
      </aside>

      {sidebarCollapsed && (
        <button
          type="button"
          className="sidebar-expand-rail"
          onClick={() => setSidebarCollapsed(false)}
          aria-label="展开左侧边栏"
          aria-controls="main-sidebar"
          aria-expanded={false}
        >
          <PanelLeftOpen size={16} />
        </button>
      )}

      <main className={`main-stage ${chatExpanded ? "chat-focus-mode" : ""}`}>
        <header className={`stage-topbar ${chatExpanded ? "stage-topbar-focus" : ""}`}>
          <div className="stage-leading-actions">
            <button type="button" className="mobile-menu-button" onClick={() => setSidebarOpen(true)} aria-label="展开侧边栏" aria-controls="main-sidebar" aria-expanded={sidebarOpen}><Menu size={21} /></button>
            {mainView !== "workspace" && (
              <button type="button" className="view-back-button" onClick={() => setMainView(mainView === "new-task" ? newTaskReturnView : "workspace")} aria-label="返回上一页">
                <ArrowLeft size={17} /><span>{mainView === "new-task" && newTaskReturnView === "templates" ? "返回模板库" : `返回${OVERVIEW_LABEL}`}</span>
              </button>
            )}
          </div>
          {!chatExpanded && (
            <div className="stage-actions">
              <button type="button" className="topbar-create-button" onClick={() => openNewTask()}>
                <Plus size={15} /> 新建任务
              </button>
            </div>
          )}
        </header>

        {mainView === "workspace" && (
          <section className={`card-stage ${chatExpanded ? "chat-focus-stage" : ""}`} aria-label="任务卡片">
            {!chatExpanded && (
              <button type="button" className="card-arrow previous" onClick={() => goTo(currentIndex - 1)} disabled={currentIndex === 0} aria-label="上一张卡片"><ArrowLeft size={20} /></button>
            )}

            <div className="task-unit-frame">
              {cardTransition && (
                <div key={`exit-${cardTransition.token}`} className={`card-chat-unit-layer card-motion-exit ${cardTransition.direction}`} aria-hidden="true" inert>
                  {renderTaskUnit(cardTransition.from, false, cardTransition.fromExpanded)}
                </div>
              )}
              <div
                key={`current-${currentCard.id}`}
                className={`card-chat-unit-layer ${isDragging ? "dragging" : ""} ${cardTransition ? `card-motion-enter ${cardTransition.direction}` : ""}`}
                style={{ transform: `translateX(${dragX}px) rotate(${dragX * 0.012}deg)` }}
              >
                {renderTaskUnit(currentIndex, true)}
              </div>
            </div>

            {!chatExpanded && (
              <button type="button" className="card-arrow next" onClick={() => goTo(currentIndex + 1)} disabled={currentIndex === cards.length - 1} aria-label="下一张卡片"><ArrowRight size={20} /></button>
            )}
          </section>
        )}

        {mainView === "new-task" && (
          <NewTaskWorkspace
            key={newTaskSession}
            draft={newTaskDraft}
            category={newTaskCategory}
            setDraft={setNewTaskDraft}
            setCategory={setNewTaskCategory}
            onBack={goHome}
            onOpenTemplates={openTemplates}
            onCreate={createTask}
            onViewTask={viewCreatedTask}
          />
        )}

        {mainView === "templates" && (
          <TemplateLibrary key={templateSearchSeed || "all-templates"} templates={templates} initialSearch={templateSearchSeed} onBack={goHome} onUseTemplate={useTemplate} onCreateTemplate={createTemplate} />
        )}
      </main>

      {liveArtifactTask && (
        <ArtifactDrawer
          task={liveArtifactTask}
          listMode={artifactListMode}
          initialFile={artifactInitialFile}
          workspaceRoot={workspace}
          files={collectDeliverableFiles(
            liveArtifactTask.deliverables,
            messages[liveArtifactTask.id] ?? [],
          )}
          onClose={closeArtifact}
          onSave={saveArtifact}
          onRevise={reviseArtifact}
        />
      )}
      {toast && <div className="toast" role="status" aria-live="polite"><Check size={16} /> {toast}</div>}
    </div>
  );
}
