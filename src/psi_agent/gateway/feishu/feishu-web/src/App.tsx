import { useCallback, useEffect, useMemo, useState } from "react";
import { PanelLeftClose } from "lucide-react";
import { generateTitle, getSessionHistory, revealWorkspacePath } from "./api";
import { ArtifactDrawer } from "./components/artifact-drawer";
import { ChatTopbar } from "./components/chat-topbar";
import { ChatView } from "./components/chat-view";
import { DesktopShell, type ShellNav } from "./components/desktop-shell";
import { DeliveryPreviewModal } from "./components/delivery-preview-modal";
import { NewDeliveriesPanel } from "./components/new-deliveries-panel";
import { NewTaskPage } from "./components/new-task-page";
import { TaskFocusDetails } from "./components/task-focus-details";
import { TasksView } from "./components/tasks-view";
import { useAuth } from "./hooks/useAuth";
import { useChatTurn } from "./hooks/useChatTurn";
import { useSessionHistory, useSessions } from "./hooks/useSessions";
import { useTasks } from "./hooks/useTasks";
import { mapHistory } from "./services/historyMap";
import { clearPendingDeliveries } from "./services/pendingDeliveries";
import { displayTitle } from "./services/taskModel";
import "./styles.css";

type View = "tasks" | "chat" | "new-task";

/**
 * 应用装配层。
 *
 * 有意保持薄: 状态在 hooks/ 里 (会话 / 流式一轮 / 任务派生), 渲染在 components/ 里,
 * 这里只做「哪个视图 + 谁连谁」。PR 版是 829 行的单文件, 登录、会话列表、历史加载、
 * 流式收发、任务过滤全在一个组件里互相串状态, 所以整体重做而不是搬。
 *
 * 登录: ``useAuth`` 走飞书 JSSDK 免登, 未就绪时渲染 ``LoginGate`` 而非放行。会话列表走
 * ``/feishu/sessions``(服务端按身份过滤), 「新建任务」开的是全新 session + 全新 jsonl。
 */

/**
 * 登录门禁。免登失败时给**可见的重试入口** —— code 只活几分钟, 从后台切回来时上一个
 * 大概率已过期; 没有重试按钮用户只能刷页面。绝不静默放行成某个默认身份。
 */
function LoginGate({
  status,
  error,
  onRetry,
}: {
  status: string;
  error: string;
  onRetry: () => void;
}) {
  return (
    <div className="ht-app ht-login-gate">
      {status === "loading" ? (
        <p>正在通过飞书登录…</p>
      ) : (
        <>
          <p role="alert">登录失败: {error || "未知原因"}</p>
          <p className="ht-card-hint">请在飞书客户端内打开本应用。若已在客户端内, 点下方重试。</p>
          <button type="button" className="ht-btn" onClick={onRetry}>
            重试登录
          </button>
        </>
      )}
    </div>
  );
}

/*
 * 开发旁路的提示**不在页面上**, 在 gateway 启动日志里 (``_auth.warn_if_dev_bypass_enabled``)。
 *
 * 这里原先挂一条常驻通栏, 由后端的 ``via_dev_bypass`` 触发。撤掉的理由: 旁路只在本机开发时
 * 开着, 而开发者就是启动 gateway 的那个人 —— 启动时喊一声就够, 不必让每个用户的每个页面都
 * 占着一条通栏。后端 ``via_dev_bypass`` 字段**保留**(``/feishu/auth/login`` 与 ``me`` 的形状
 * 约定不变), 只是前端不再用它渲染任何东西。
 */

export function App() {
  const auth = useAuth();
  if (auth.status !== "ready") {
    return <LoginGate status={auth.status} error={auth.error} onRetry={auth.retry} />;
  }
  return <AuthedApp userName={auth.me?.name || ""} />;
}

function AuthedApp({ userName }: { userName: string }) {
  const [view, setView] = useState<View>("tasks");
  const [input, setInput] = useState("");
  const [newDraft, setNewDraft] = useState("");
  const [creatingTask, setCreatingTask] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [selectedSegment, setSelectedSegment] = useState("live");
  const [artifactTaskId, setArtifactTaskId] = useState("");
  const [artifactFile, setArtifactFile] = useState("");
  const [previewFile, setPreviewFile] = useState("");
  const [showNewDeliveries, setShowNewDeliveries] = useState(false);
  const [contextCollapsed, setContextCollapsed] = useState(false);
  const [historyDeliverables, setHistoryDeliverables] = useState<
    Record<string, { files: string[]; paths: Record<string, string> }>
  >({});
  const [deliveriesRevision, setDeliveriesRevision] = useState(0);

  const sessions = useSessions();
  const tasks = useTasks(sessions.sessions, sessions.titles, historyDeliverables);
  const history = useSessionHistory(sessions.currentId);
  const turn = useChatTurn(sessions.currentId);

  // 任务总览/交付物抽屉需要的文件来自历史记录, 不能只依赖流式 blob 事件。
  useEffect(() => {
    let alive = true;
    void (async () => {
      const next: Record<string, { files: string[]; paths: Record<string, string> }> = {};
      await Promise.all(
        sessions.sessions.map(async (session) => {
          try {
            const rows = await getSessionHistory(session.id);
            const { messages, filePaths } = mapHistory(rows);
            const files = Array.from(new Set(messages.flatMap((m) => m.files || [])));
            next[session.id] = { files, paths: filePaths };
          } catch {
            // 没有历史/接口失败时这一项保持缺省, 不影响任务列表本身。
          }
        }),
      );
      if (alive) setHistoryDeliverables(next);
    })();
    return () => {
      alive = false;
    };
  }, [sessions.sessions, deliveriesRevision]);

  // 历史到了就铺进消息列表 (附件路径一起接管)。流式增量之后只改 turn.messages。
  useEffect(() => {
    // 发送中的会话不能拿「当时历史还没写入」的空结果覆盖本地乐观消息；
    // 已有本地消息时历史只是兜底, 也不需要重铺, 避免把刚上屏的输入又清掉。
    if (turn.sending || turn.messages.length > 0) return;
    const { messages, filePaths } = mapHistory(history.raw);
    turn.setMessages(sessions.currentId, messages);
    turn.setFilePaths(sessions.currentId, (prev) => ({ ...prev, ...filePaths }));
  }, [
    history.raw,
    turn.sending,
    turn.messages.length,
    turn.setMessages,
    turn.setFilePaths,
  ]);

  const currentTask = useMemo(
    () => tasks.tasks.find((t) => t.id === sessions.currentId),
    [tasks.tasks, sessions.currentId],
  );

  /**
   * 当前会话的显示名 —— **顶栏与对话区共用这一个**。
   *
   * 不直接用 ``currentTask?.title`` 兜底: 首屏 tasks 还没派生出来时会落空, 那时如果各自
   * 写一个 ``|| "未命名任务"``, IM 共用那条会先闪一下「未命名任务」再变成「海豚一号」。
   * 这里再走一次 ``displayTitle`` 同一个判据, 于是任何时刻都只有一个名字。
   */
  const currentTitle = useMemo(() => {
    const session = sessions.sessions.find((s) => s.id === sessions.currentId);
    return currentTask?.title || displayTitle(session, sessions.titles[sessions.currentId]);
  }, [currentTask, sessions.sessions, sessions.currentId, sessions.titles]);
  const artifactTask = useMemo(
    () => tasks.tasks.find((t) => t.id === artifactTaskId),
    [tasks.tasks, artifactTaskId],
  );
  const newDeliveryTasks = useMemo(
    () => tasks.tasks.filter((t) => t.newDeliverables.length > 0),
    [tasks.tasks],
  );

  const openChat = useCallback(
    (id: string) => {
      sessions.setCurrentId(id);
      setSelectedSegment("live");
      setView("chat");
    },
    [sessions],
  );

  const handleNewTask = useCallback(async () => {
    setView("new-task");
  }, []);

  const backToTasks = useCallback(() => {
    setView("tasks");
    setNewDraft("");
  }, []);

  const navigate = useCallback(
    (nav: ShellNav) => {
      if (nav === "tasks") {
        setView("tasks");
        return;
      }
      if (sessions.currentId) {
        setView("chat");
      } else {
        setView("new-task");
      }
    },
    [sessions.currentId],
  );

  const createFromDraft = useCallback(async () => {
    const draft = newDraft.trim();
    const files = pendingFiles;
    if (!draft && !files.length) return;
    setCreatingTask(true);
    try {
      const id = await sessions.create();
      if (!id) return;
      setNewDraft("");
      setPendingFiles([]);
      setSelectedSegment("live");
      setView("chat");
      // C 端语义: 「新建中」只锁新建页自己的那一次提交; 一旦会话建好并切回对话,
      // 释放创建锁, 首轮回复继续在后台跑。否则首轮没结束时再次点“新建任务”会打不了字。
      setCreatingTask(false);
      const assistantText = await turn.send(id, draft, files);
      if (!sessions.titles[id]) {
        try {
          const { title } = await generateTitle(id, draft, assistantText);
          sessions.setTitles((prev) => ({ ...prev, [id]: title }));
        } catch {
          // 标题失败不阻塞首轮对话。
        }
      }
      setDeliveriesRevision((n) => n + 1);
      void tasks.refresh();
    } finally {
      setCreatingTask(false);
    }
  }, [newDraft, pendingFiles, sessions, tasks, turn]);

  const handleSend = useCallback(async () => {
    const sessionId = sessions.currentId;
    if (!sessionId) return;
    const text = input;
    const files = pendingFiles;
    setInput("");
    setPendingFiles([]);
    const assistantText = await turn.send(sessionId, text, files);

    // 首轮结束后补标题, 否则列表里一直是「未命名任务」。
    if (!sessions.titles[sessionId]) {
      try {
        const { title } = await generateTitle(sessionId, text, assistantText);
        sessions.setTitles((prev) => ({ ...prev, [sessionId]: title }));
      } catch {
        // 标题生成失败不影响对话本身。
      }
    }
    setDeliveriesRevision((n) => n + 1);
    void tasks.refresh();
  }, [sessions, input, pendingFiles, turn, tasks]);

  const handleOpenFile = useCallback((name: string) => setPreviewFile(name), []);
  const handleReveal = useCallback((path: string) => {
    void revealWorkspacePath(path).catch(() => undefined);
  }, []);
  const saveArtifact = useCallback(() => {
    if (!artifactTaskId) return;
    clearPendingDeliveries(artifactTaskId);
    setArtifactTaskId("");
    setArtifactFile("");
  }, [artifactTaskId]);

  const listError = sessions.error || history.error;
  const taskIndex = useMemo(
    () => tasks.tasks.findIndex((t) => t.id === sessions.currentId),
    [tasks.tasks, sessions.currentId],
  );
  const switchTask = useCallback(
    (dir: 1 | -1) => {
      const idx = taskIndex;
      if (idx < 0) return;
      const next = tasks.tasks[idx + dir];
      if (next) openChat(next.id);
    },
    [taskIndex, tasks.tasks, openChat],
  );

  // 抽屉类浮层统一支持 Esc 关闭（与 PR 版行为一致）。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (previewFile) {
        setPreviewFile("");
      } else if (showNewDeliveries) {
        setShowNewDeliveries(false);
      } else if (artifactTaskId) {
        setArtifactTaskId("");
        setArtifactFile("");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [previewFile, showNewDeliveries, artifactTaskId]);

  return (
    <DesktopShell nav={view === "tasks" ? "tasks" : "chat"} userName={userName} onNavigate={navigate}>
      {view === "tasks" ? (
        <>
          {listError ? <div className="ht-error" role="alert">{listError}</div> : null}
          <TasksView
            tasks={tasks.tasks}
            filtered={tasks.filtered}
            counts={tasks.counts}
            selected={currentTask}
            filter={tasks.filter}
            search={tasks.search}
            onFilter={tasks.setFilter}
            onSearch={tasks.setSearch}
            onSelect={sessions.setCurrentId}
            onDelete={(id) => void sessions.remove(id)}
            onOpenChat={openChat}
            onOpenNewDeliverables={() => setShowNewDeliveries(true)}
            newDeliveryCount={newDeliveryTasks.length}
            onNewTask={() => void handleNewTask()}
          />
        </>
      ) : view === "new-task" ? (
        <NewTaskPage
          draft={newDraft}
          sending={creatingTask}
          pendingFiles={pendingFiles}
          onDraft={setNewDraft}
          onBack={backToTasks}
          onSubmit={() => void createFromDraft()}
          onAddFiles={(files) => setPendingFiles((prev) => [...prev, ...files])}
          onRemoveFile={(index) => setPendingFiles((prev) => prev.filter((_, idx) => idx !== index))}
        />
      ) : (
        <div className={`focus-view${contextCollapsed ? " is-context-collapsed" : ""}`}>
          {!contextCollapsed && (
            <div className="focus-context-col">
              <div className="cend2-context-bar">
                <button
                  type="button"
                  className="context-panel-toggle"
                  aria-label="收起任务上下文栏"
                  title="收起任务上下文栏"
                  onClick={() => setContextCollapsed(true)}
                >
                  <PanelLeftClose size={15} />
                </button>
                <span>任务上下文</span>
              </div>
              <TaskFocusDetails
                task={currentTask || null}
                tasks={tasks.tasks}
                todoSegments={tasks.segments[sessions.currentId] || []}
                selectedSegmentId={selectedSegment}
                onSelectTodoSegment={setSelectedSegment}
                onOpenArtifact={(task, fileName) => {
                  setArtifactTaskId(task.id);
                  setArtifactFile(fileName || "");
                  if (fileName) setPreviewFile("");
                }}
              />
            </div>
          )}
          <div className="focus-chat-col">
            <ChatTopbar
              title={currentTitle}
              sending={turn.sending}
              hasNewDeliveries={(currentTask?.newDeliverables.length ?? 0) > 0}
              taskIndex={taskIndex < 0 ? 0 : taskIndex}
              taskCount={tasks.tasks.length}
              contextCollapsed={contextCollapsed}
              onToggleContext={() => setContextCollapsed((v) => !v)}
              onPrevTask={() => switchTask(-1)}
              onNextTask={() => switchTask(1)}
              onNewTask={() => void handleNewTask()}
              onOpenDeliverables={() => setShowNewDeliveries(true)}
            />
            <ChatView
              messages={turn.messages}
              userName={userName}
              taskTitle={currentTitle}
              input={input}
              sending={turn.sending}
              error={turn.error || history.error}
              pendingFiles={pendingFiles}
              emptyHint={history.loading ? "正在加载历史…" : undefined}
              onInput={setInput}
              onSend={() => void handleSend()}
              onStop={turn.stop}
              onAddFiles={(files) => setPendingFiles((prev) => [...prev, ...files])}
              onRemoveFile={(index) => setPendingFiles((prev) => prev.filter((_, idx) => idx !== index))}
              onFeedback={(index, kind) =>
                turn.setMessages(sessions.currentId, (prev) =>
                  prev.map((m, i) =>
                    i === index ? { ...m, feedback: m.feedback === kind ? undefined : kind } : m,
                  ),
                )
              }
              onRegenerate={(index) => {
                const user = turn.messages[index - 1];
                if (user?.role === "user") void turn.send(sessions.currentId, user.text);
              }}
              onOpenFile={handleOpenFile}
              onRevealFile={handleReveal}
              filePathOf={turn.filePathOf}
              executionSteps={
                currentTask?.hasTodoTrack
                  ? currentTask.steps.map((step) => ({
                      label: step.t,
                      state: step.s as "done" | "working" | "waiting",
                      ...(step.detail ? { detail: step.detail } : {}),
                    }))
                  : undefined
              }
            />
          </div>
        </div>
      )}

      {showNewDeliveries && (
        <NewDeliveriesPanel
          tasks={newDeliveryTasks}
          onOpen={(taskId) => {
            setShowNewDeliveries(false);
            setArtifactTaskId(taskId);
            setArtifactFile("");
          }}
          onClose={() => setShowNewDeliveries(false)}
        />
      )}

      {artifactTask && (
        <ArtifactDrawer
          taskTitle={artifactTask.title}
          files={[...new Set([...artifactTask.files, ...artifactTask.newDeliverables])]}
          filePathOf={(name) =>
            historyDeliverables[artifactTask.id]?.paths[name] ??
            (artifactTask.id === sessions.currentId ? turn.filePathOf(name) : undefined)
          }
          initialFile={artifactFile || undefined}
          pending={artifactTask.newDeliverables.length > 0}
          onSave={saveArtifact}
          onClose={() => {
            setArtifactTaskId("");
            setArtifactFile("");
          }}
        />
      )}

      {previewFile && (
        <DeliveryPreviewModal
          name={previewFile}
          path={turn.filePathOf(previewFile)}
          onClose={() => setPreviewFile("")}
        />
      )}
    </DesktopShell>
  );
}
