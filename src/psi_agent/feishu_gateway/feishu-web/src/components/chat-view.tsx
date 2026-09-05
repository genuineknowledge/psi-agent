import { useRef } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  ClipboardList,
  Clock,
  Layers3,
  MessageCircle,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  Plus,
  Search,
  Send,
  Square,
  X,
} from "lucide-react";
import type { TodoSegmentSummary } from "../api";
import { filesFromClipboard } from "../services/clipboardFiles";
import { useComposerFileDrop } from "../services/composerFileDrop";
import type { ChatMessage, Task } from "../types";
import { brandMark } from "./brand";
import { ChatThread } from "./chat-thread";
import { TaskFocusDetails } from "./task-focus-details";
import { TreasureVisual } from "./treasure";
import { ExecutionStepsPanel } from "../cend/haitun-agent/execution-steps-panel";

const NEW_TASK_PRESETS = [
  { label: "学习新知识", prompt: "请帮我学习以下新知识，提炼核心概念、关键要点和可验证的结论：", category: "知识学习", icon: BookOpen },
  { label: "管理工作SOP", prompt: "请帮我梳理并管理以下工作的 SOP，形成清晰可执行的流程步骤：", category: "流程管理", icon: ClipboardList },
  { label: "做一份领导汇报", prompt: "请帮我整理一份面向管理层的汇报，材料包括：", category: "内容整理", icon: Layers3 },
  { label: "研究市场或竞品", prompt: "请研究以下市场或竞品，核验公开来源并给出证据：", category: "深度研究", icon: Search },
  { label: "整理会议与行动项", prompt: "请把以下会议材料整理为结论和行动项：", category: "会议协作", icon: MessageCircle },
];

export interface ChatViewProps {
  tasks: Task[];
  activeTask?: Task;
  taskIndex: number;
  messages: ChatMessage[];
  userName: string;
  liveThinking: string;
  progressLog?: { lines: string[]; current: string } | null;
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
  onOpenTaskDeliverables: (taskId: string, mode: "new" | "history") => void;
  contextCollapsed: boolean;
  onToggleContext: () => void;
  pendingFiles: File[];
  onAddFiles: (files: File[]) => void;
  onRemoveFile: (index: number) => void;
  onStop: () => void;
  onFeedback: (index: number, kind: "up" | "down") => void;
  onRegenerate: (index: number) => void;
  segments: TodoSegmentSummary[];
  selectedHistory: string;
  onSelectHistory: (id: string) => void;
  filePathOf: (name: string) => string | undefined;
  fileDataOf: (name: string) => string | undefined;
  onRevealFile: (path: string) => void;
  onOpenFile: (name: string) => void;
}

export function ChatView(props: ChatViewProps) {
  const { tasks, activeTask, taskIndex, messages, liveThinking, progressLog, input, sending, error, mainView, newDraft, onInput, onNewDraft, onSend, onPrev, onNext, onNewTask, onBackNew, onCreateTask, onOpenTaskDeliverables, contextCollapsed, onToggleContext, pendingFiles, onAddFiles, onRemoveFile, onStop, onFeedback, onRegenerate, segments, selectedHistory, onSelectHistory, filePathOf, fileDataOf, onOpenFile } = props;
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { isFileDragOver, dropProps } = useComposerFileDrop({ onFiles: onAddFiles });

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
            <div className="new-task-compose-block">
            {!sending && (
              <div className="new-task-presets">
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
            <form
              className="new-task-composer-strip"
              onSubmit={(e) => {
                e.preventDefault();
                if (!sending && newDraft.trim()) onCreateTask();
              }}
            >
              <input ref={fileInputRef} type="file" multiple hidden onChange={(e) => { onAddFiles(Array.from(e.target.files || [])); e.target.value = ""; }} />
              <button type="button" className="chat-attach-button" aria-label="添加附件" onClick={() => fileInputRef.current?.click()}><Paperclip size={20} /></button>
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
                aria-label="描述新任务"
              />
              <button type="submit" className="send-button" disabled={!newDraft.trim() || sending} aria-label="发送任务描述"><Send size={16} /></button>
            </form>
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
              <button type="button" className="context-panel-toggle" aria-label="收起任务上下文栏" onClick={onToggleContext}><PanelLeftClose size={15} /></button>
              <span>任务上下文</span>
            </div>
            <div className="cend2-context-body">
              <TaskFocusDetails
                task={activeTask || null}
                tasks={tasks}
                todoSegments={segments}
                selectedSegmentId={selectedHistory}
                onSelectTodoSegment={onSelectHistory}
                onOpenArtifact={(_owner, name) => onOpenFile(name || "")}
              />
            </div>
          </div>

          <section
            className={`cend2-chat${isFileDragOver ? " is-file-drag-over" : ""}`}
            aria-label="对话"
            {...dropProps}
          >
            <div className="cend2-chat-top">
              <div className="cend2-chat-heading">
                {contextCollapsed && (
                  <button type="button" className="context-panel-toggle context-panel-toggle-in-chat" aria-label="展开任务上下文栏" onClick={onToggleContext}><PanelLeftOpen size={15} /></button>
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
                  <button type="button" className={`chat-top-icon ${(activeTask?.newDeliverables?.length ?? 0) > 0 ? "has-delivery" : ""}`} aria-label={(activeTask?.newDeliverables?.length ?? 0) > 0 ? "查看新交付物" : "暂无新交付物"} title={(activeTask?.newDeliverables?.length ?? 0) > 0 ? "查看新交付物" : "暂无新交付物"} onClick={() => activeTask && onOpenTaskDeliverables(activeTask.id, "new")}><TreasureVisual state={(activeTask?.newDeliverables?.length ?? 0) > 0 ? "ready" : "none"} size="mini" /></button>
                </span>
                <button type="button" className="cend2-newtask-mini" onClick={onNewTask}><Plus size={13} />新建任务/聊天</button>
              </div>
            </div>

            <ChatThread
              messages={messages}
              typing={sending}
              title={activeTask?.title || "当前任务"}
              liveThinking={liveThinking}
              progressLog={progressLog || null}
              filePathOf={filePathOf}
              fileDataOf={fileDataOf}
              onFeedback={onFeedback}
              onRegenerate={onRegenerate}
            />
            {error && <div className="cend2-error">{error}</div>}

            {activeTask && activeTask.hasTodoTrack && (
              <ExecutionStepsPanel steps={activeTask.steps.map((s) => ({ label: s.t, state: s.s as "done" | "waiting" | "working", detail: s.detail }))} />
            )}

            {pendingFiles.length > 0 && (
              <div className="chat-pending-files" data-attach-control>
                {pendingFiles.map((file, i) => (
                  <span className="chat-pending-chip" key={`${file.name}-${i}`}>
                    <Paperclip size={13} />
                    <em>{file.name}</em>
                    <button type="button" aria-label={`移除 ${file.name}`} onClick={() => onRemoveFile(i)}><X size={12} /></button>
                  </span>
                ))}
              </div>
            )}

            <form
              className="chat-composer-strip"
              onSubmit={(e) => {
                e.preventDefault();
                if (!sending && (input.trim() || pendingFiles.length)) onSend();
              }}
            >
              <input ref={fileInputRef} type="file" multiple hidden onChange={(e) => { onAddFiles(Array.from(e.target.files || [])); e.target.value = ""; }} />
              <button type="button" className="chat-attach-button" aria-label="添加附件" onClick={() => fileInputRef.current?.click()}><Paperclip size={20} /></button>
              <textarea
                rows={1}
                value={input}
                onChange={(e) => onInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                    if (e.shiftKey || e.altKey) return;
                    if (e.ctrlKey || e.metaKey) {
                      e.preventDefault();
                      const el = e.currentTarget;
                      const start = el.selectionStart ?? input.length;
                      const end = el.selectionEnd ?? input.length;
                      const next = `${input.slice(0, start)}\n${input.slice(end)}`;
                      onInput(next);
                      requestAnimationFrame(() => {
                        el.selectionStart = start + 1;
                        el.selectionEnd = start + 1;
                      });
                      return;
                    }
                    e.preventDefault();
                    onSend();
                  }
                }}
                onPaste={(e) => {
                  const files = filesFromClipboard(e.clipboardData);
                  if (files.length) {
                    e.preventDefault();
                    onAddFiles(files);
                  }
                }}
                placeholder={`告诉 Agent 如何继续「${activeTask?.title || "当前任务"}」…`}
                aria-label="对话内容"
              />
              {sending ? (
                <button type="button" className="send-button stop-button" aria-label="停止生成" title="停止生成" onClick={onStop}><Square size={14} fill="currentColor" /></button>
              ) : (
                <button type="submit" className="send-button" disabled={!input.trim() && pendingFiles.length === 0} aria-label="发送" title="发送"><Send size={16} /></button>
              )}
            </form>
          </section>
        </div>
      </main>
    </div>
  );
}
