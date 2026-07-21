import { Check, Copy, RefreshCw, ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import type { ChatFile, ChatMessage, MessageFeedback } from "./model";
import { BrandLogo } from "./primitives";
import { readStoredAvatar, readStoredName } from "../services/userProfile";
import { htmlEscape, renderMd } from "../services/renderMd";
import { stripTransferMarkers } from "../services/sendMarkers";
import { downloadMatrixXlsx, matrixToTsv, tableToMatrix } from "../services/mdTable";
import { FAILED_REASON_LABEL, isCompleteAgent } from "../services/messageTurn";
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
  const ext = (name.split(".").pop() || "").toLowerCase();
  return ext === "md" || ext === "markdown" || ext === "html" || ext === "htm";
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
  onFeedback,
  onRegenerate,
  onRetry,
}: {
  messages: ChatMessage[];
  typing: boolean;
  title: string;
  onFeedback?: (index: number, kind: Exclude<MessageFeedback, "">) => void;
  onRegenerate?: (index: number) => void;
  onRetry?: (index: number) => void;
}) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const [preview, setPreview] = useState<ChatFile | null>(null);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, typing]);

  const hasContent = messages.some((m) => m.text.trim() || (m.files?.length ?? 0) > 0) || typing;

  const showAgentActions = (msg: ChatMessage) => {
    if (msg.role !== "agent") return false;
    if (typing) return false;
    return isCompleteAgent(msg);
  };

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
        const clean = stripTransferMarkers(message.text);
        const emptyStreaming =
          message.role === "agent" &&
          !clean.trim() &&
          !(message.files?.length) &&
          typing &&
          index === messages.length - 1;
        if (emptyStreaming) {
          return (
            <ChatBlock role="agent" key={`typing-${index}`}>
              <div className="focus-chat-bubble thinking">
                <span className="typing" aria-label="正在输入"><i /><i /><i /></span>
              </div>
            </ChatBlock>
          );
        }
        if (!clean.trim() && !(message.files?.length)) return null;

        const html = message.role === "agent"
          ? renderMd(clean)
          : htmlEscape(clean).replace(/\n/g, "<br>");

        const failedLabel = message.failed
          ? (FAILED_REASON_LABEL[message.failedReason ?? "incomplete"] ?? FAILED_REASON_LABEL.incomplete)
          : "";

        return (
          <ChatBlock role={message.role} key={`${message.role}-${index}`}>
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
              {clean.trim() ? (
                <div
                  className="focus-chat-bubble"
                  dangerouslySetInnerHTML={{ __html: html }}
                />
              ) : null}
            </div>
            {(message.files?.length ?? 0) > 0 && (
              <div className="focus-chat-files">
                {message.files!.map((f, fi) => (
                  <button
                    type="button"
                    key={`${f.name}-${fi}`}
                    className="focus-chat-file-chip"
                    disabled={!isPreviewable(f.name) || !f.data}
                    onClick={() => {
                      if (isPreviewable(f.name) && f.data) setPreview(f);
                    }}
                    title={isPreviewable(f.name) ? `预览 ${f.name}` : f.name}
                  >
                    <span>{f.name}</span>
                    {isPreviewable(f.name) ? <em>预览</em> : null}
                  </button>
                ))}
              </div>
            )}
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
                <CopyButton text={clean} className="focus-chat-action-btn" />
              </div>
            )}
          </ChatBlock>
        );
      })}
      {typing && messages[messages.length - 1]?.role === "user" && (
        <ChatBlock role="agent">
          <div className="focus-chat-bubble thinking">
            <span className="typing" aria-label="正在输入"><i /><i /><i /></span>
          </div>
        </ChatBlock>
      )}
      {preview && <FilePreview file={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
