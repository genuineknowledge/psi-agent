import { useState } from "react";
import { ChevronRight, Copy, FolderOpen, RefreshCw, ThumbsDown, ThumbsUp } from "lucide-react";
import { FAILED_REASON_LABEL } from "../services/messageTurn";
import { stripTransferMarkers } from "../services/sendMarkers";
import { isBlobPreviewable } from "../services/filePreview";
import type { ChatMessage } from "../types";
import { brandMark } from "./brand";
import { MarkdownBubble, renderMarkdownHtml } from "./markdown";

export function ChatMessageItem({
  msg,
  last,
  sending,
  userName,
  onFeedback,
  onRegenerate,
  onOpenFile,
  filePathOf,
  onRevealFile,
}: {
  msg: ChatMessage;
  last: boolean;
  sending: boolean;
  userName: string;
  onFeedback: (kind: "up" | "down") => void;
  onRegenerate: () => void;
  onOpenFile: (name: string) => void;
  filePathOf: (name: string) => string | undefined;
  onRevealFile: (path: string) => void;
}) {
  const role = msg.role === "user" ? "user" : "agent";
  const [toolsOpen, setToolsOpen] = useState(true);
  const [thinkingOpen, setThinkingOpen] = useState(false);
  const hasTools = !!msg.tools?.length;
  const hasThinking = !!msg.reasoning;
  const displayText = role === "agent" ? stripTransferMarkers(msg.text) : msg.text;
  const displayName = userName.trim() || "我";
  return (
    <div className={`focus-chat-msg ${role}`}>
      <span className={`focus-chat-avatar ${role}`}>{role === "agent" ? brandMark("mini") : displayName.slice(0, 1).toUpperCase()}</span>
      <div className="focus-chat-body">
        <div className="focus-chat-speaker">{role === "agent" ? "HaiTun Agent" : displayName}</div>
        {sending && last && role === "agent" && msg.progress && msg.progress.length > 0 && (
          <div className="focus-chat-live-progress" aria-live="polite">
            {msg.progress.map((line, i) => (
              <div key={i}><span className="focus-chat-progress-dot" />{line}</div>
            ))}
          </div>
        )}
        {role === "agent" && (hasTools || hasThinking) && (
          <div className="focus-chat-turn-process">
            {hasTools && (
              <div className={`focus-chat-thinking focus-chat-tools${toolsOpen ? " is-open" : ""}`}>
                <button type="button" className="focus-chat-thinking-toggle" aria-expanded={toolsOpen} onClick={() => setToolsOpen((v) => !v)}>
                  <ChevronRight size={14} className="focus-chat-thinking-chevron" aria-hidden />
                  <span>已用工具 {msg.tools!.length} 项</span>
                </button>
                {toolsOpen && (
                  <div className="focus-chat-tools-body" role="list" aria-label="工具调用">
                    {msg.tools!.map((line, i) => <div className="focus-chat-progress-line" role="listitem" key={i}>{line}</div>)}
                  </div>
                )}
              </div>
            )}
            {hasThinking && (
              <div className={`focus-chat-thinking${thinkingOpen ? " is-open" : ""}`}>
                <button type="button" className="focus-chat-thinking-toggle" aria-expanded={thinkingOpen} onClick={() => setThinkingOpen((v) => !v)}>
                  <ChevronRight size={14} className="focus-chat-thinking-chevron" aria-hidden />
                  <span>思考过程</span>
                </button>
                {thinkingOpen && (
                  <div className="focus-chat-thinking-body" role="region" aria-label="思考过程">{msg.reasoning}</div>
                )}
              </div>
            )}
          </div>
        )}
        {sending && last && role === "agent" && msg.interimText && (
          <div className="focus-chat-bubble interim" dangerouslySetInnerHTML={{ __html: renderMarkdownHtml(msg.interimText) }} />
        )}
        <div className="focus-chat-bubble-wrap">
          {role === "user" && (
            <div className="focus-chat-side-actions">
              <button type="button" className="focus-chat-copy-btn" title="复制" aria-label="复制" onClick={() => void navigator.clipboard?.writeText(displayText)}><Copy size={16} /></button>
            </div>
          )}
          {role === "agent" ? (
            displayText ? (
              <MarkdownBubble text={displayText} />
            ) : (
              <div className="focus-chat-bubble thinking">
                {sending && last ? <span className="typing" aria-label="正在输入"><i /><i /><i /></span> : ""}
              </div>
            )
          ) : (
            <div className="focus-chat-bubble">{displayText || (sending && last ? "…" : "")}</div>
          )}
        </div>
        {msg.files && msg.files.length > 0 && (
          <div className="focus-chat-files">
            {msg.files.map((f, i) => {
              const p = filePathOf(f);
              const canPreview = isBlobPreviewable(f) && !!p;
              return (
                <div className="focus-chat-file-row" key={`${f}-${i}`}>
                  <button type="button" className="focus-chat-file-chip" disabled={!canPreview} title={canPreview ? `预览 ${f}` : f} onClick={() => onOpenFile(f)}>
                    <span>{f}</span>{canPreview ? <em>预览</em> : null}
                  </button>
                  {p ? (
                    <button type="button" className="focus-chat-file-reveal" title="在文件夹中显示" aria-label={`在文件夹中显示 ${f}`} onClick={() => onRevealFile(p)}>
                      <FolderOpen size={14} />
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
        {msg.failed && msg.failedReason && (
          <div className="focus-chat-failed-label">{FAILED_REASON_LABEL[msg.failedReason]}</div>
        )}
        {role === "agent" && (
          <div className="focus-chat-msg-actions" role="toolbar" aria-label="消息操作">
            <button type="button" className={`focus-chat-action-btn${msg.feedback === "up" ? " active" : ""}`} title={msg.feedback === "up" ? "取消点赞" : "点赞"} aria-pressed={msg.feedback === "up"} onClick={() => onFeedback("up")}><ThumbsUp size={16} /></button>
            <button type="button" className={`focus-chat-action-btn${msg.feedback === "down" ? " active" : ""}`} title={msg.feedback === "down" ? "取消点踩" : "点踩"} aria-pressed={msg.feedback === "down"} onClick={() => onFeedback("down")}><ThumbsDown size={16} /></button>
            <button type="button" className="focus-chat-action-btn" title={msg.failed ? "重试" : "重新生成"} aria-label={msg.failed ? "重试" : "重新生成"} onClick={onRegenerate}><RefreshCw size={16} /></button>
            <button type="button" className="focus-chat-action-btn" title="复制" aria-label="复制" onClick={() => void navigator.clipboard?.writeText(displayText)}><Copy size={16} /></button>
          </div>
        )}
      </div>
    </div>
  );
}
