import { Check, Copy, FolderOpen, RefreshCw, ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import type { ChatFile, ChatMessage, MessageFeedback } from "./model";
import { BrandLogo } from "./primitives";
import { readStoredAvatar, readStoredName } from "../services/userProfile";
import { htmlEscape, renderMd } from "../services/renderMd";
import { stripTransferMarkers } from "../services/sendMarkers";
import { downloadMatrixXlsx, matrixToTsv, tableToMatrix } from "../services/mdTable";
import { preferResultBelowRule } from "../services/assistantDisplay";
import { TURN_PROGRESS, type ProgressLog } from "../services/turnProgress";
import { FAILED_REASON_LABEL, isCompleteAgent } from "../services/messageTurn";
import { ensureChatFileData, revealDeliverableInFolder } from "../utils/filePreviewUtils";
import { isBlobPreviewable } from "../utils/renderBlobPreview";
import FilePreview from "../components/FilePreview";

function ChatAvatar({ role }: { role: "agent" | "user" }) {
  const [userAvatar, setUserAvatar] = useState(readStoredAvatar);
  const [userName, setUserName] = useState(readStoredName);

  useEffect(() => {
    const sync = () => {
      setUserAvatar(readStoredAvatar());
      setUserName(readStoredName());
    };
    window.addEventListener("storage", sync);
    window.addEventListener("focus", sync);
    return () => {
      window.removeEventListener("storage", sync);
      window.removeEventListener("focus", sync);
    };
  }, []);

  if (role === "agent") {
    return (
      <div className="focus-chat-avatar agent" aria-hidden="true">
        <BrandLogo size="mini" />
      </div>
    );
  }

  const initial = userName.trim().charAt(0).toUpperCase() || "我";
  return (
    <div className="focus-chat-avatar user" aria-hidden="true">
      {userAvatar ? <img src={userAvatar} alt="" /> : <span>{initial}</span>}
    </div>
  );
}

function ChatBlock({
  role,
  children,
}: {
  role: "agent" | "user";
  children: ReactNode;
}) {
  const speaker = role === "agent" ? "HaiTun Agent" : (readStoredName().trim() || "我");
  return (
    <div className={`focus-chat-msg ${role}`}>
      <ChatAvatar role={role} />
      <div className="focus-chat-body">
        <div className="focus-chat-speaker">{speaker}</div>
        {children}
      </div>
    </div>
  );
}

function isPreviewable(name: string) {
  return isBlobPreviewable(name);
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

async function handleTableAction(e: MouseEvent) {
  const btn = (e.target as HTMLElement).closest?.("[data-table-action]") as HTMLElement | null;
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  const card = btn.closest("[data-md-table]");
  const table = card?.querySelector("table") as HTMLTableElement | null;
  const matrix = tableToMatrix(table);
  if (!matrix.length) return;
  const action = btn.getAttribute("data-table-action");
  if (action === "copy") {
    const tsv = matrixToTsv(matrix);
    await copyText(tsv);
    btn.classList.add("is-done");
    window.setTimeout(() => btn.classList.remove("is-done"), 1400);
    return;
  }
  if (action === "download") {
    btn.classList.add("is-busy");
    try {
      const stamp = new Date().toISOString().slice(0, 10);
      await downloadMatrixXlsx(matrix, `table-${stamp}.xlsx`);
    } finally {
      btn.classList.remove("is-busy");
    }
  }
}

function CopyButton({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={className}
      title={copied ? "已复制" : "复制"}
      aria-label="复制"
      onClick={() => {
        void copyText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <Check size={16} aria-hidden /> : <Copy size={16} aria-hidden />}
    </button>
  );
}

/** Split-mode right pane: v1-like chat (MD tables, file preview chips, message actions). */
export function FocusChatThread({
  messages,
  typing,
  title,
  progressLog,
  workspaceRoot = "",
  onFeedback,
  onRegenerate,
  onRetry,
}: {
  messages: ChatMessage[];
  typing: boolean;
  title: string;
  /** Growing Cursor-style process log (summary lines + 规划下一步 trailer). */
  progressLog?: ProgressLog | null;
  /** Session workspace — used to resolve relative SEND paths after refresh. */
  workspaceRoot?: string;
  onFeedback?: (index: number, kind: Exclude<MessageFeedback, "">) => void;
  onRegenerate?: (index: number) => void;
  onRetry?: (index: number) => void;
}) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const [preview, setPreview] = useState<ChatFile | null>(null);
  const [previewBusy, setPreviewBusy] = useState<string | null>(null);
  const [revealBusy, setRevealBusy] = useState<string | null>(null);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, typing, progressLog]);

  const openPreview = async (file: ChatFile) => {
    if (!isPreviewable(file.name)) return;
    const key = file.path || file.name;
    if (file.data.trim()) {
      setPreview(file);
      return;
    }
    if (!file.path?.trim()) return;
    setPreviewBusy(key);
    try {
      const loaded = await ensureChatFileData(file, workspaceRoot);
      setPreview(loaded);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : String(e));
    } finally {
      setPreviewBusy(null);
    }
  };

  const revealFile = async (file: ChatFile) => {
    const path = file.path?.trim();
    if (!path) return;
    const key = path || file.name;
    setRevealBusy(key);
    try {
      await revealDeliverableInFolder(path, workspaceRoot);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : String(e));
    } finally {
      setRevealBusy(null);
    }
  };

  const hasContent =
    messages.some((m) => m.text.trim() || (m.files?.length ?? 0) > 0) || typing;

  const showAgentActions = (msg: ChatMessage) => {
    if (msg.role !== "agent") return false;
    if (typing) return false;
    return isCompleteAgent(msg);
  };

  const thinkingBubble = (
    <div className="focus-chat-bubble thinking focus-chat-progress-wrap">
      {progressLog ? (
        <div className="focus-chat-progress-log" aria-live="polite">
          {progressLog.lines.map((line, i) => (
            <div key={`p-${i}-${line}`} className="focus-chat-progress-line">{line}</div>
          ))}
          <div className="focus-chat-progress-line is-current">
            <span>{progressLog.current}</span>
            <span className="typing" aria-label="正在输入"><i /><i /><i /></span>
          </div>
        </div>
      ) : (
        <span className="typing" aria-label="正在输入"><i /><i /><i /></span>
      )}
    </div>
  );

  return (
    <div
      className="focus-chat-thread"
      ref={scrollerRef}
      aria-label={`${title} 的对话`}
      onClick={(e) => void handleTableAction(e)}
    >
      {!hasContent && (
        <div className="focus-chat-empty">
          <div className="focus-chat-avatar agent" aria-hidden="true">
            <BrandLogo size="mini" />
          </div>
          <p>向 Agent 发送消息，开始围绕「{title}」继续推进。</p>
        </div>
      )}
      {messages.map((message, index) => {
        const isLast = index === messages.length - 1;
        const isLiveAgent = typing && isLast && message.role === "agent";
        // Cursor-style: hide interim prose during tools/planning; once content
        // SSE arrives (trailer → 撰写回复…), stream 正文 live under the process log.
        const writing = progressLog?.current === TURN_PROGRESS.writing;
        const hideAgentProse = isLiveAgent && !writing;
        const clean = stripTransferMarkers(message.text);
        const displayText = hideAgentProse ? "" : preferResultBelowRule(clean);
        const showFiles = !hideAgentProse && (message.files?.length ?? 0) > 0;

        if (hideAgentProse) {
          return (
            <ChatBlock role="agent" key={`typing-${index}`}>
              {thinkingBubble}
            </ChatBlock>
          );
        }

        if (!displayText.trim() && !showFiles && !(isLiveAgent && writing)) return null;

        const html = message.role === "agent"
          ? renderMd(displayText)
          : htmlEscape(displayText).replace(/\n/g, "<br>");

        const failedLabel = message.failed
          ? (FAILED_REASON_LABEL[message.failedReason ?? "incomplete"] ?? FAILED_REASON_LABEL.incomplete)
          : "";

        const fileChips = showFiles ? (
          <div className="focus-chat-files">
            {message.files!.map((f, fi) => {
              const canPreview = isPreviewable(f.name) && Boolean(f.data.trim() || f.path?.trim());
              const canReveal = Boolean(f.path?.trim());
              const busyKey = f.path || f.name;
              const busy = previewBusy === busyKey;
              const revealing = revealBusy === busyKey;
              return (
                <div className="focus-chat-file-row" key={`${f.name}-${fi}`}>
                  <button
                    type="button"
                    className="focus-chat-file-chip"
                    disabled={!canPreview || busy}
                    onClick={() => {
                      void openPreview(f);
                    }}
                    title={canPreview ? `预览 ${f.name}` : f.name}
                  >
                    <span>{f.name}</span>
                    {isPreviewable(f.name) ? <em>{busy ? "加载中" : "预览"}</em> : null}
                  </button>
                  {canReveal ? (
                    <button
                      type="button"
                      className="focus-chat-file-reveal"
                      disabled={revealing}
                      title={revealing ? "正在打开…" : "在文件夹中显示"}
                      aria-label={`在文件夹中显示 ${f.name}`}
                      onClick={() => {
                        void revealFile(f);
                      }}
                    >
                      <FolderOpen size={14} aria-hidden />
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : null;

        return (
          <ChatBlock role={message.role} key={`${message.role}-${index}`}>
            {isLiveAgent && writing ? thinkingBubble : null}
            <div className="focus-chat-bubble-wrap">
              {message.role === "user" && (
                <div className={`focus-chat-side-actions${message.failed ? " has-retry" : ""}`}>
                  <CopyButton text={clean} className="focus-chat-copy-btn" />
                  {message.failed && (
                    <button
                      type="button"
                      className="focus-chat-retry-btn"
                      aria-label="重新发送"
                      title={failedLabel}
                      disabled={typing}
                      onClick={() => onRetry?.(index)}
                    >
                      <RefreshCw size={16} aria-hidden />
                    </button>
                  )}
                </div>
              )}
              {displayText.trim() ? (
                <div
                  className="focus-chat-bubble"
                  dangerouslySetInnerHTML={{ __html: html }}
                />
              ) : null}
            </div>
            {fileChips}
            {showAgentActions(message) && (
              <div className="focus-chat-msg-actions" role="toolbar" aria-label="消息操作">
                <button
                  type="button"
                  className={`focus-chat-action-btn${message.feedback === "up" ? " active" : ""}`}
                  title={message.feedback === "up" ? "取消点赞" : "点赞"}
                  aria-pressed={message.feedback === "up"}
                  onClick={() => onFeedback?.(index, "up")}
                >
                  <ThumbsUp size={16} aria-hidden />
                </button>
                <button
                  type="button"
                  className={`focus-chat-action-btn${message.feedback === "down" ? " active" : ""}`}
                  title={message.feedback === "down" ? "取消点踩" : "点踩"}
                  aria-pressed={message.feedback === "down"}
                  onClick={() => onFeedback?.(index, "down")}
                >
                  <ThumbsDown size={16} aria-hidden />
                </button>
                <button
                  type="button"
                  className="focus-chat-action-btn"
                  title="重新生成"
                  aria-label="重新生成"
                  disabled={typing}
                  onClick={() => onRegenerate?.(index)}
                >
                  <RefreshCw size={16} aria-hidden />
                </button>
                <CopyButton text={displayText || clean} className="focus-chat-action-btn" />
              </div>
            )}
          </ChatBlock>
        );
      })}
      {typing && messages[messages.length - 1]?.role === "user" && (
        <ChatBlock role="agent">
          {thinkingBubble}
        </ChatBlock>
      )}
      {preview && (
        <FilePreview
          file={preview}
          workspaceRoot={workspaceRoot}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}
