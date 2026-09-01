import { useRef } from "react";
import { Paperclip, Send, Square, X } from "lucide-react";
import type { ChatMessage } from "../types";
import { brandMark } from "./brand";
import { ChatThread } from "./chat-thread";

/**
 * 会话视图 = 消息列表 + 输入区。视觉照 PR 复刻, 但**只收 props**:
 * PR 版在这里引了 ToC 侧的 haitun-agent/execution-steps-panel, 那是整棵 ToC 组件树被
 * 拷进来的原因之一; 执行步骤面板等后端有了对应数据源再单独做。
 */
export function ChatView({
  messages,
  userName,
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
}: {
  messages: ChatMessage[];
  userName: string;
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
}) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const canSend = !sending && (!!input.trim() || pendingFiles.length > 0);

  return (
    <div className="focus-chat-pane">
      <div className="focus-chat-scroll">
        {messages.length === 0 ? (
          <div className="focus-chat-empty">
            {brandMark("hero")}
            <p>{emptyHint || "有什么可以帮您？"}</p>
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

      <form
        className="focus-chat-composer"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSend) onSend();
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
            placeholder={sending ? "正在回复…" : "输入消息，Enter 发送，Shift+Enter 换行"}
            value={input}
            onChange={(e) => onInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                if (canSend) onSend();
              }
            }}
          />
          {sending ? (
            <button type="button" className="focus-chat-send stop" aria-label="停止" onClick={onStop}>
              <Square size={16} />
            </button>
          ) : (
            <button type="submit" className="focus-chat-send" aria-label="发送" disabled={!canSend}>
              <Send size={16} />
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
