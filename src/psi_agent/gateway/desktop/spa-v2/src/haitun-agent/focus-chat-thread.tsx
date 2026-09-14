import { Check, ChevronRight, Copy, FolderOpen, RefreshCw, RotateCcw, ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import type { ChatFile, ChatMessage, MessageFeedback } from "./model";
import { BrandLogo } from "./primitives";
import { readStoredAvatar, readStoredName, USER_PROFILE_EVENT } from "../services/userProfile";
import { htmlEscape, renderMd } from "../services/renderMd";
import { stripTransferMarkers } from "../services/sendMarkers";
import { downloadMatrixXlsx, matrixToTsv, tableToMatrix } from "../services/mdTable";
import { preferResultBelowRule } from "../services/assistantDisplay";
import { type ProgressLog } from "../services/turnProgress";
import {
  hasDisplayableReasoning,
  stripToolMarkersFromReasoning,
} from "../services/reasoningDisplay";
import { isCompleteAgent } from "../services/messageTurn";
import { formatMessageClock, thinkingHeaderWithDuration } from "../services/messageTiming";
import { ensureChatFileData, revealDeliverableInFolder } from "../utils/filePreviewUtils";
import { isBlobPreviewable } from "../utils/renderBlobPreview";
import FilePreview from "../components/FilePreview";
import { useI18n } from "../i18n";

/** Distance from bottom (px) — beyond this, streaming must not yank the viewport down. */
const STICK_BOTTOM_PX = 60;

function ChatAvatar({ role }: { role: "agent" | "user" }) {
  const { t } = useI18n();
  const [userAvatar, setUserAvatar] = useState(readStoredAvatar);
  const [userName, setUserName] = useState(readStoredName);

  useEffect(() => {
    const sync = () => {
      setUserAvatar(readStoredAvatar());
      setUserName(readStoredName());
    };
    window.addEventListener("storage", sync);
    window.addEventListener("focus", sync);
    window.addEventListener(USER_PROFILE_EVENT, sync);
    return () => {
      window.removeEventListener("storage", sync);
      window.removeEventListener("focus", sync);
      window.removeEventListener(USER_PROFILE_EVENT, sync);
    };
  }, []);

  if (role === "agent") {
    return (
      <div className="focus-chat-avatar agent" aria-hidden="true">
        <BrandLogo size="mini" />
      </div>
    );
  }

  const initial = userName.trim().charAt(0).toUpperCase() || t("chat.me");
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
  const { t } = useI18n();
  const [userName, setUserName] = useState(readStoredName);
  useEffect(() => {
    const sync = () => setUserName(readStoredName());
    window.addEventListener("storage", sync);
    window.addEventListener("focus", sync);
    window.addEventListener(USER_PROFILE_EVENT, sync);
    return () => {
      window.removeEventListener("storage", sync);
      window.removeEventListener("focus", sync);
      window.removeEventListener(USER_PROFILE_EVENT, sync);
    };
  }, []);
  const speaker = role === "agent" ? "HaiTun Agent" : (userName.trim() || t("chat.me"));
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
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={className}
      title={copied ? t("chat.copied") : t("chat.copy")}
      aria-label={t("chat.copy")}
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

/**
 * Cursor-style post-turn process: tools (primary, from ``message.tools``) + thinking prose.
 * Live streaming still uses the process log; after the turn, tools are a separate field
 * (history ``tools`` / live progress lines) — not parsed out of ``reasoning``.
 */
function TurnProcessDisclosure({
  reasoning,
  tools = [],
  streaming = false,
  thinkingMs,
}: {
  reasoning?: string;
  tools?: string[];
  streaming?: boolean;
  thinkingMs?: number;
}) {
  const { t } = useI18n();
  const toolLines = tools.filter((line) => !!line.trim());
  const thinking = stripToolMarkersFromReasoning(reasoning ?? "");
  const [toolsOpen, setToolsOpen] = useState(true);
  const [thinkingOpen, setThinkingOpen] = useState(false);
  if (!toolLines.length && !thinking) return null;
  const thinkingLabel = streaming
    ? t("chat.thinkingHeaderStreaming")
    : thinkingHeaderWithDuration(t("chat.thinkingHeaderDone"), thinkingMs);

  return (
    <div className="focus-chat-turn-process">
      {toolLines.length > 0 ? (
        <div className={`focus-chat-thinking focus-chat-tools${toolsOpen ? " is-open" : ""}`}>
          <button
            type="button"
            className="focus-chat-thinking-toggle"
            aria-expanded={toolsOpen}
            onClick={() => setToolsOpen((v) => !v)}
          >
            <ChevronRight size={14} className="focus-chat-thinking-chevron" aria-hidden />
            <span>{t("chat.toolsHeader", { count: toolLines.length })}</span>
          </button>
          {toolsOpen ? (
            <div
              className="focus-chat-tools-body"
              role="list"
              aria-label={t("chat.toolsAria")}
            >
              {toolLines.map((line, i) => (
                <div className="focus-chat-progress-line" role="listitem" key={`${i}-${line}`}>
                  {line}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      {thinking ? (
        <div className={`focus-chat-thinking${thinkingOpen ? " is-open" : ""}`}>
          <button
            type="button"
            className="focus-chat-thinking-toggle"
            aria-expanded={thinkingOpen}
            onClick={() => setThinkingOpen((v) => !v)}
          >
            <ChevronRight size={14} className="focus-chat-thinking-chevron" aria-hidden />
            <span>{thinkingLabel}</span>
          </button>
          {thinkingOpen ? (
            <div className="focus-chat-thinking-body" role="region" aria-label={t("chat.thinkingAria")}>
              {thinking}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Split-mode right pane: v1-like chat (MD tables, file preview chips, message actions). */
export function FocusChatThread({
  messages,
  typing,
  title,
  progressLog,
  liveThinking = "",
  workspaceRoot = "",
  loadingHistory = false,
  onFeedback,
  onRegenerate,
  onRetry,
}: {
  messages: ChatMessage[];
  typing: boolean;
  title: string;
  /** Growing Cursor-style process log (summary lines + 规划下一步 trailer). */
  progressLog?: ProgressLog | null;
  /** Raw thinking prose streamed live before the final body starts. */
  liveThinking?: string;
  /** Session workspace — used to resolve relative SEND paths after refresh. */
  workspaceRoot?: string;
  /** Sidebar jump before GET /history resolves — avoid empty-prompt flash. */
  loadingHistory?: boolean;
  onFeedback?: (index: number, kind: Exclude<MessageFeedback, "">) => void;
  onRegenerate?: (index: number) => void;
  onRetry?: (index: number) => void;
}) {
  const { t, language } = useI18n();
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  /** Align with spa v1 / Cursor: only pin to bottom while the user is near the end. */
  const stickToBottomRef = useRef(true);
  /**
   * Same stick rule for the in-bubble live thinking scroller (`.focus-chat-live-thinking`).
   * 刻意为之: never force scrollTop on every token — that glued the box even after the user
   * scrolled up to read earlier thinking.
   */
  const stickLiveThinkingRef = useRef(true);
  const prevLiveThinkingEmptyRef = useRef(true);
  const prevMessageCountRef = useRef(0);
  const liveThinkingRef = useRef<HTMLDivElement | null>(null);
  const [preview, setPreview] = useState<ChatFile | null>(null);
  const [previewBusy, setPreviewBusy] = useState<string | null>(null);
  const [revealBusy, setRevealBusy] = useState<string | null>(null);

  const distanceFromBottom = (el: HTMLElement) =>
    el.scrollHeight - el.clientHeight - el.scrollTop;

  const onThreadScroll = () => {
    const el = scrollerRef.current;
    if (!el) return;
    stickToBottomRef.current = distanceFromBottom(el) <= STICK_BOTTOM_PX;
  };

  const onLiveThinkingScroll = () => {
    const el = liveThinkingRef.current;
    if (!el) return;
    stickLiveThinkingRef.current = distanceFromBottom(el) <= STICK_BOTTOM_PX;
  };

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;

    // New user turn → jump to bottom + re-stick (same as spa v1 clearing userHasScrolledUp).
    // 刻意为之: send appends user + empty agent in one setState, so last.role is "agent" —
    // must scan the newly added slice for role=user, not only messages.at(-1).
    const count = messages.length;
    const prevCount = prevMessageCountRef.current;
    if (count > prevCount) {
      const added = messages.slice(prevCount);
      if (added.some((m) => m.role === "user")) {
        stickToBottomRef.current = true;
        stickLiveThinkingRef.current = true;
      }
    }
    prevMessageCountRef.current = count;

    if (!stickToBottomRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      const node = scrollerRef.current;
      if (!node || !stickToBottomRef.current) return;
      node.scrollTop = node.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [messages, typing, progressLog, liveThinking]);

  useEffect(() => {
    const lastInterim = messages[messages.length - 1]?.interimText?.trim() ?? "";
    const hasLive = !!(liveThinking.trim() || lastInterim);
    // Fresh thinking/step content after an empty gap → stick again for the new run.
    if (hasLive && prevLiveThinkingEmptyRef.current) {
      stickLiveThinkingRef.current = true;
    }
    prevLiveThinkingEmptyRef.current = !hasLive;

    if (!stickLiveThinkingRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      const node = liveThinkingRef.current;
      if (!node || !stickLiveThinkingRef.current) return;
      node.scrollTop = node.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [liveThinking, messages, typing]);

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
      alertError(e);
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
      alertError(e);
    } finally {
      setRevealBusy(null);
    }
  };

  const hasContent =
    messages.some((m) => m.text.trim() || (m.interimText ?? "").trim() || (m.files?.length ?? 0) > 0) || typing;
  const lastInterim = messages[messages.length - 1]?.interimText
    ? stripTransferMarkers(messages[messages.length - 1]!.interimText ?? "")
    : "";
  const stepText = lastInterim;
  const alertError = (err: unknown) => {
    const msg = err instanceof Error ? err.message : String(err)
    const text = msg === '没有可打开的文件路径。'
      ? t('fileError.noPath')
      : msg === '历史记录中没有该文件的路径，无法从磁盘读取预览。'
        ? t('fileError.readPath')
        : msg
    window.alert(text)
  }

  const showAgentActions = (msg: ChatMessage) => {
    if (msg.role !== "agent") return false;
    if (typing) return false;
    return isCompleteAgent(msg);
  };

  /** 重新生成只挂在「最后一条完整助手气泡」上：`runChatTurn` 只往末条 agent 写流，
   * 点中间那条会清空该气泡却把新回复盖到末条上，等于抹掉最新一轮。 */
  let lastCompleteAgentIndex = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i]?.role === "agent" && isCompleteAgent(messages[i]!)) {
      lastCompleteAgentIndex = i;
      break;
    }
  }

  const thinkingBubble = (
    <div className="focus-chat-bubble thinking focus-chat-progress-wrap">
      <div
        ref={liveThinkingRef}
        className="focus-chat-live-thinking"
        aria-live="polite"
        onScroll={onLiveThinkingScroll}
      >
        {liveThinking.trim() ? (
          <div className="focus-chat-live-thinking-prose">{liveThinking}</div>
        ) : null}
        {stepText.trim() ? (
          <div
            className="focus-chat-live-thinking-step"
            dangerouslySetInnerHTML={{ __html: renderMd(stepText, language) }}
          />
        ) : null}
        {!liveThinking.trim() && !stepText.trim() ? (
          <span className="typing" aria-label={t("chat.typing")}><i /><i /><i /></span>
        ) : null}
      </div>
    </div>
  );

  return (
    <div
      className="focus-chat-thread"
      ref={scrollerRef}
      aria-label={t("chat.threadAria", { title })}
      onScroll={onThreadScroll}
      onClick={(e) => void handleTableAction(e)}
    >
      {!hasContent && loadingHistory && (
        <div className="focus-chat-empty" aria-busy="true">
          <div className="focus-chat-avatar agent" aria-hidden="true">
            <BrandLogo size="mini" />
          </div>
          <p>
            {t("chat.syncing")}
            <span className="typing" aria-label={t("chat.loading")}><i /><i /><i /></span>
          </p>
        </div>
      )}
      {!hasContent && !loadingHistory && (
        <div className="focus-chat-empty">
          <div className="focus-chat-avatar agent" aria-hidden="true">
            <BrandLogo size="mini" />
          </div>
          <p>{t("chat.emptyHint", { title })}</p>
        </div>
      )}
      {messages.map((message, index) => {
        const isLast = index === messages.length - 1;
        const isLiveAgent = typing && isLast && message.role === "agent";
        const writing = progressLog?.current === t("turn.writing");
        const clean = stripTransferMarkers(message.text);
        const interimClean = stripTransferMarkers(message.interimText ?? "");
        // Live: keep step interim + current segment visible (do not hide on tool rounds).
        // Settled: optional prefer-below---- on the final body only.
        const displayText = isLiveAgent ? clean : preferResultBelowRule(clean);
        const interimDisplay = interimClean;
        const showFiles = (message.files?.length ?? 0) > 0;
        const showLiveProgress = isLiveAgent;
        const showProse = Boolean(displayText.trim()) || Boolean(interimDisplay.trim()) || showFiles;

        if (isLiveAgent && !showProse) {
          return (
            <ChatBlock role="agent" key={`typing-${index}`}>
              {thinkingBubble}
            </ChatBlock>
          );
        }

        if (!showProse && !(isLiveAgent && writing)) return null;

        const finalHtml = message.role === "agent"
          ? renderMd(displayText, language)
          : htmlEscape(displayText).replace(/\n/g, "<br>");
        const failedLabel = message.failed
          ? t(`chat.failed.${message.failedReason ?? "incomplete"}`)
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
                    title={canPreview ? t("chat.previewFile", { name: f.name }) : f.name}
                  >
                    <span>{f.name}</span>
                    {isPreviewable(f.name) ? <em>{busy ? t("chat.loading") : t("chat.preview")}</em> : null}
                  </button>
                  {canReveal ? (
                    <button
                      type="button"
                      className="focus-chat-file-reveal"
                      disabled={revealing}
                      title={revealing ? t("chat.opening") : t("chat.showInFolder")}
                      aria-label={t("chat.showInFolderAria", { name: f.name })}
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
            {showLiveProgress ? thinkingBubble : null}
            {message.role === "agent"
              && !isLiveAgent
              && (
                (message.tools?.length ?? 0) > 0
                || hasDisplayableReasoning(message.reasoning ?? "")
              )
              ? (
                <TurnProcessDisclosure
                  reasoning={message.reasoning}
                  tools={message.tools}
                  thinkingMs={message.thinkingMs}
                />
              )
              : null}
            <div className="focus-chat-bubble-wrap">
              {message.role === "user" && (
                <div className={`focus-chat-side-actions${message.failed ? " has-retry" : ""}`}>
                  <CopyButton text={clean} className="focus-chat-copy-btn" />
                  {message.failed && (
                    <button
                      type="button"
                      className="focus-chat-retry-btn"
                      aria-label={t("chat.retryAria")}
                      title={t("chat.retryTitle", { label: failedLabel })}
                      disabled={typing}
                      onClick={() => onRetry?.(index)}
                    >
                      <RotateCcw size={16} aria-hidden />
                    </button>
                  )}
                </div>
              )}
              {(message.role === "user" || !isLiveAgent) && displayText.trim() ? (
                <div className="focus-chat-bubble-stack">
                  <div
                    className="focus-chat-bubble"
                    dangerouslySetInnerHTML={{ __html: finalHtml }}
                  />
                  {message.createdAt ? (
                    <time
                      className={`focus-chat-msg-clock focus-chat-msg-clock--${message.role}`}
                      dateTime={message.createdAt}
                      title={message.createdAt}
                    >
                      {formatMessageClock(message.createdAt)}
                    </time>
                  ) : null}
                </div>
              ) : null}
            </div>
            {fileChips}
            {showAgentActions(message) && (
              <div className="focus-chat-msg-actions" role="toolbar" aria-label={t("chat.actionsAria")}>
                <button
                  type="button"
                  className={`focus-chat-action-btn${message.feedback === "up" ? " active" : ""}`}
                  title={message.feedback === "up" ? t("chat.unlike") : t("chat.like")}
                  aria-pressed={message.feedback === "up"}
                  onClick={() => onFeedback?.(index, "up")}
                >
                  <ThumbsUp size={16} aria-hidden />
                </button>
                <button
                  type="button"
                  className={`focus-chat-action-btn${message.feedback === "down" ? " active" : ""}`}
                  title={message.feedback === "down" ? t("chat.undislike") : t("chat.dislike")}
                  aria-pressed={message.feedback === "down"}
                  onClick={() => onFeedback?.(index, "down")}
                >
                  <ThumbsDown size={16} aria-hidden />
                </button>
                {index === lastCompleteAgentIndex ? (
                  <button
                    type="button"
                    className="focus-chat-action-btn"
                    title={t("chat.regenerate")}
                    aria-label={t("chat.regenerate")}
                    disabled={typing}
                    onClick={() => onRegenerate?.(index)}
                  >
                    <RefreshCw size={16} aria-hidden />
                  </button>
                ) : null}
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
