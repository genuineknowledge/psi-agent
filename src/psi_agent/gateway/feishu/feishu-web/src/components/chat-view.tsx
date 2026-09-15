import { useRef } from "react";
import { Clock, Paperclip, Send, Square, X } from "lucide-react";
import type { ChatMessage } from "../types";
import { filesFromClipboard } from "../services/clipboardFiles";
import { useComposerFileDrop } from "../services/composerFileDrop";
import type { QueuedSend } from "../services/queuedSend";
import { brandMark } from "./brand";
import { ChatThread } from "./chat-thread";
import { ExecutionStepsPanel, type ExecutionStep } from "./execution-steps-panel";

/**
 * 会话视图 = 消息列表 + 输入区。视觉照 PR 复刻, 但**只收 props**:
 * PR 版在这里引了 ToC 侧的 haitun-agent/execution-steps-panel, 那是整棵 ToC 组件树被
 * 拷进来的原因之一; 执行步骤面板等后端有了对应数据源再单独做。
 */
export function ChatView({
  messages,
  userName,
  taskTitle,
  input,
  sending,
  error,
  pendingFiles,
  emptyHint,
  onInput,
  onSend,
  onStop,
  onAddFiles,
  onRemoveFile,
  onFeedback,
  onRegenerate,
  onOpenFile,
  onRevealFile,
  filePathOf,
  executionSteps,
  queued,
  onQueue,
  onCancelQueued,
}: {
  messages: ChatMessage[];
  userName: string;
  taskTitle?: string;
  input: string;
  sending: boolean;
  error: string;
  pendingFiles: File[];
  emptyHint?: string;
  onInput: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  onAddFiles: (files: File[]) => void;
  onRemoveFile: (index: number) => void;
  onFeedback: (index: number, kind: "up" | "down") => void;
  onRegenerate: (index: number) => void;
  onOpenFile: (name: string) => void;
  onRevealFile: (path: string) => void;
  filePathOf: (name: string) => string | undefined;
  executionSteps?: ExecutionStep[];
  /** 已排队待发的那条(本回合结束后自动发出); 没有则不显示排队芯片。 */
  queued?: QueuedSend | null;
  onQueue: () => void;
  onCancelQueued: () => void;
}) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const hasContent = !!input.trim() || pendingFiles.length > 0;

  // 拖拽进来的文件与粘贴、回形针按钮走**同一条** onAddFiles 路径。
  const { isFileDragOver, dropProps } = useComposerFileDrop({ onFiles: onAddFiles });

  /**
   * 一个入口两种去向: 空闲时发出去, 正在回复时**排队**(本轮结束后自动发)。
   *
   * 回合进行中发送按钮会变成「停止」, 所以排队实际由 Enter 触发 —— 占位文案里写明了这点,
   * 否则用户根本不知道排队存在。
   */
  const submit = () => {
    if (!hasContent) return;
    if (sending) onQueue();
    else onSend();
  };

  return (
    <div className="focus-chat-pane" {...dropProps}>
      {isFileDragOver && (
        <div className="focus-chat-dropzone" aria-hidden="true">
          <Paperclip size={22} />
          <span>松开以添加附件</span>
        </div>
      )}
      <div className="focus-chat-scroll">
        {messages.length === 0 ? (
          <div className="focus-chat-empty">
            <span className="focus-chat-avatar agent" aria-hidden="true">{brandMark("mini")}</span>
            <p>
              {emptyHint
                || (taskTitle
                  ? `向 Agent 发送消息，开始围绕「${taskTitle}」继续推进。`
                  : "有什么可以帮您？")}
            </p>
          </div>
        ) : (
          <ChatThread
            messages={messages}
            typing={sending}
            userName={userName}
            filePathOf={filePathOf}
            onFeedback={onFeedback}
            onRegenerate={onRegenerate}
            onOpenFile={onOpenFile}
            onRevealFile={onRevealFile}
          />
        )}
      </div>

      {error && (
        <div className="focus-chat-error" role="alert">
          {error}
        </div>
      )}

      {executionSteps && executionSteps.length > 0 ? <ExecutionStepsPanel steps={executionSteps} /> : null}

      <form
        className="focus-chat-composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {pendingFiles.length > 0 && (
          <div className="focus-chat-pending-files">
            {pendingFiles.map((f, i) => (
              <span className="focus-chat-pending-chip" key={`${f.name}-${i}`}>
                <span>{f.name}</span>
                <button type="button" aria-label={`移除 ${f.name}`} onClick={() => onRemoveFile(i)}>
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
        {queued && (
          <div className="focus-chat-queued" role="status">
            <Clock size={12} />
            <span className="focus-chat-queued-label">已排队，本轮结束后自动发送</span>
            <em>{queued.display}</em>
            <button type="button" aria-label="取消排队" title="取消排队" onClick={onCancelQueued}>
              <X size={12} />
            </button>
          </div>
        )}
        <div className="focus-chat-composer-row">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              onAddFiles(Array.from(e.target.files || []));
              e.target.value = "";
            }}
          />
          <button
            type="button"
            className="chat-attach-button"
            aria-label="添加附件"
            onClick={() => fileInputRef.current?.click()}
          >
            <Paperclip size={20} />
          </button>
          <textarea
            className="focus-chat-input"
            placeholder={
              sending
                ? "正在回复…（Enter 排队，本轮结束后自动发送）"
                : "输入消息，Enter 发送，Shift+Enter 换行"
            }
            value={input}
            onChange={(e) => onInput(e.target.value)}
            onPaste={(e) => {
              // 截图/文件直接粘进来。**纯文本粘贴不拦** —— 一拦就把正常打字也吃掉了。
              const files = filesFromClipboard(e.clipboardData);
              if (!files.length) return;
              e.preventDefault();
              onAddFiles(files);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
          />
          {sending ? (
            <button type="button" className="focus-chat-send stop" aria-label="停止" onClick={onStop}>
              <Square size={16} />
            </button>
          ) : (
            <button type="submit" className="focus-chat-send" aria-label="发送" disabled={!hasContent}>
              <Send size={16} />
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
