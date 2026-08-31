import { useEffect, useMemo, useRef, useState } from "react";
import { chatStream, createSession, fetchHistory, fileToUploadFile, NotLoggedInError } from "../api";
import type { ChatEvent, HistoryMessage } from "../api";
import { extractChartData, extractChartDataFromMarkdown } from "../chart-data";
import { DataChart } from "../components/DataChart";
import { MarkdownWithSortableTables } from "../components/SortableTable";
import { listSessions, registerSession, type SessionEntry } from "../session-store";

interface AttachmentInfo {
  name: string;
  size: number;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  /** Raw non-text events (tool activity etc.), shown under 依据. */
  evidence: ChatEvent[];
  /** Tool activity as progress lines (plan 6.2: tool_* events only). */
  tools?: ToolActivity[];
  /** Attachments the user sent with this message (P2-1). */
  files?: AttachmentInfo[];
  done: boolean;
}

interface ToolActivity {
  name: string;
  status: "running" | "done";
}

const SESSION_KEY = "guoshu_weekly_session_id";

/** "[Tool Call: weekly_aggregate({...})]" → "weekly_aggregate" (plan 6.2). */
function parseToolCallName(event: ChatEvent): string | null {
  const match = /\[Tool Call: ([^(\]]+)/.exec(event.text ?? "");
  return match ? match[1].trim() : null;
}

/** Transcript rows → display messages. Restored turns rebuild their evidence
 * from the per-message tool list the Gateway keeps, so 查看依据 survives
 * switching views (it used to vanish: only role/text were carried over). */
function historyToMessages(history: HistoryMessage[]): Message[] {
  return history.map((item) => ({
    role: item.role === "user" ? ("user" as const) : ("assistant" as const),
    content: item.text,
    evidence: (item.tools ?? []).map((tool) => ({
      type: "reasoning",
      kind: "tool_call",
      text: `[Tool Call: ${tool.name}(${tool.arguments ?? "{}"})]`,
    })),
    done: true,
  }));
}

// Mirrors the BFF's MAX_ATTACHMENT_BYTES (bff/main.py): reject oversized
// files at the picker instead of waiting for the 413 round-trip.
const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

function AssistantMessageView({ message }: { message: Message }) {
  // Chart (P0-3) from two sources: streamed tool results first, then the
  // answer's own markdown tables (covers turns where the model restates a
  // table from history without calling a tool). Recompute on either change.
  const chartData = useMemo(
    () => extractChartData(message.evidence) ?? extractChartDataFromMarkdown(message.content),
    [message.evidence, message.content],
  );
  return (
    <div className="assistantMessage">
      <div className="avatar agentAvatar">周</div>
      <div className="answerBody">
        <div className="answerMeta">
          <strong>周报 Agent</strong>
          <span>{message.done ? "已结束" : "回答中…"}</span>
        </div>
        {/* Plan 6.2: tool progress as process lines — live while answering,
            folded away once done (the citations block keeps the raw events). */}
        {!message.done && message.tools && message.tools.length > 0 && (
          <div className="toolProgress">
            {message.tools.map((tool, index) => (
              <div className="toolProgressLine" key={`${tool.name}-${index}`}>
                {tool.status === "done" ? "✓" : "…"} {tool.name}
              </div>
            ))}
          </div>
        )}
        {message.thinking && !message.content && <div className="thinking">思考中:{message.thinking}</div>}
        {message.content && (
          <div className="answerText">
            <MarkdownWithSortableTables content={message.content} />
          </div>
        )}
        {message.done && chartData && <DataChart series={chartData} />}
        {message.evidence.length > 0 && (
          <details className="citations">
            <summary>查看依据({message.evidence.length} 条)</summary>
            <pre>{message.evidence.map((e) => JSON.stringify(e)).join("\n")}</pre>
          </details>
        )}
      </div>
    </div>
  );
}

/**
 * Chat view — the core of B4. Message flow, streaming rendering, follow-up
 * input, identity/preference panel, evidence collapse (plan 6.2).
 */
export function ChatView({ onSessionExpired }: { onSessionExpired: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [identity, setIdentity] = useState("领导");
  const [preference, setPreference] = useState("结论优先");
  const [sessionId, setSessionId] = useState<string>(() => localStorage.getItem(SESSION_KEY) ?? "");
  const [sessions, setSessions] = useState<SessionEntry[]>([]);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function pickFiles(list: FileList | File[] | null) {
    if (!list?.length) return;
    const picked = Array.from(list);
    const oversized = picked.filter((f) => f.size > MAX_ATTACHMENT_BYTES);
    if (oversized.length) {
      alert(`以下文件超过 20MB,已忽略:${oversized.map((f) => f.name).join("、")}`);
    }
    setPendingFiles((prev) => [...prev, ...picked.filter((f) => f.size <= MAX_ATTACHMENT_BYTES)]);
  }

  useEffect(() => {
    // Scroll the message list itself, never the page — the page is fixed at
    // one viewport tall and scrolling it would fight the list's own scroll.
    const list = messagesRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [messages]);

  useEffect(() => {
    setSessions(listSessions());
  }, []);

  useEffect(() => {
    // Restore the transcript on mount: switching views unmounts this one,
    // but the conversation lives in the Gateway — reload it, don't start
    // blank (and never duplicate it — this runs once per mount).
    if (!sessionId) return;
    fetchHistory(sessionId)
      .then((history) => {
        setMessages((prev) => {
          if (prev.length) return prev;
          return historyToMessages(history);
        });
      })
      .catch(() => {
        // An invalid session id (gateway restarted) is handled on next send.
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function newChat() {
    localStorage.removeItem(SESSION_KEY);
    setSessionId("");
    setMessages([]);
  }

  function switchSession(targetId: string) {
    localStorage.setItem(SESSION_KEY, targetId);
    setSessionId(targetId);
    fetchHistory(targetId)
      .then((history) => {
        setMessages(historyToMessages(history));
      })
      .catch(() => {
        setMessages([]);
      });
  }

  async function ensureSession(): Promise<string> {
    if (sessionId) return sessionId;
    const info = await createSession();
    setSessionId(info.id);
    localStorage.setItem(SESSION_KEY, info.id);
    return info.id;
  }

  async function send() {
    const question = input.trim();
    if ((!question && !pendingFiles.length) || busy) return;
    const files = pendingFiles;
    setInput("");
    setPendingFiles([]);
    setBusy(true);
    setMessages((prev) => [
      ...prev,
      {
        role: "user",
        content: question,
        evidence: [],
        done: true,
        files: files.map((f) => ({ name: f.name, size: f.size })),
      },
    ]);
    // Assistant placeholder fills in as events arrive.
    setMessages((prev) => [...prev, { role: "assistant", content: "", evidence: [], done: false }]);
    // Past turns (done, non-empty) plus the new question — the new message is
    // added explicitly, never through the filter (a placeholder without
    // `done` would be dropped and the question would silently vanish).
    const history = [
      ...messages.filter((m) => m.done && m.content).map((m) => ({ role: m.role, content: m.content })),
      { role: "user" as const, content: question },
    ];
    try {
      const sid = await ensureSession();
      // Index the session so the history view can find it again later.
      if (messages.length === 0) {
        registerSession(sid, question || (files[0]?.name ?? "新对话"));
        setSessions(listSessions());
      }
      // Identity/preference are demo-stage page-level toggles: the BFF injects
      // them as answer-organisation instructions. Production replaces the
      // identity source with the login-derived role (plan 6.2: identity must
      // come from trusted server state, never from what the model can read).
      // Attachments travel as bare-base64 uploads with the message (P2-1).
      const uploads = await Promise.all(files.map(fileToUploadFile));
      await chatStream(sid, history, (event) => {
        if (event.type === "reasoning" && event.kind === "tool_call") {
          // Tool activity rides the reasoning stream — keep it as evidence so
          // the chart parser (P0-3) and the citations panel can see it, and
          // mirror it as a live progress line (plan 6.2: tool_* only).
          const name = parseToolCallName(event) ?? "工具调用";
          setMessages((prev) =>
            prev.map((m, i) =>
              i === prev.length - 1
                ? {
                    ...m,
                    evidence: [...m.evidence, event],
                    tools: [...(m.tools ?? []), { name, status: "running" as const }],
                  }
                : m,
            ),
          );
        } else if (event.type === "reasoning" && event.kind === "tool_result") {
          // Results carry no tool name — mark the most recent running line done.
          setMessages((prev) =>
            prev.map((m, i) =>
              i === prev.length - 1
                ? {
                    ...m,
                    evidence: [...m.evidence, event],
                    tools: (m.tools ?? []).map((tool, index, all) =>
                      index === all.length - 1 ? { ...tool, status: "done" as const } : tool,
                    ),
                  }
                : m,
            ),
          );
        } else if (event.type === "reasoning") {
          setMessages((prev) =>
            prev.map((m, i) =>
              i === prev.length - 1 ? { ...m, thinking: (m.thinking ?? "") + (event.text ?? "") } : m,
            ),
          );
        } else if (event.type === "text" || event.type === "chunk") {
          setMessages((prev) =>
            prev.map((m, i) =>
              i === prev.length - 1 ? { ...m, content: m.content + (event.text ?? "") } : m,
            ),
          );
        } else if (event.type === "error") {
          setMessages((prev) =>
            prev.map((m, i) =>
              i === prev.length - 1 ? { ...m, content: m.content || `出错了:${event.error ?? ""}`, done: true } : m,
            ),
          );
        } else {
          // Tool calls and anything else: keep as evidence.
          setMessages((prev) =>
            prev.map((m, i) => (i === prev.length - 1 ? { ...m, evidence: [...m.evidence, event] } : m)),
          );
        }
      }, { identity, preference, files: uploads });
    } catch (err) {
      if (err instanceof NotLoggedInError) {
        onSessionExpired();
        return;
      }
      setMessages((prev) =>
        prev.map((m, i) => (i === prev.length - 1 ? { ...m, content: `请求失败:${String(err)}`, done: true } : m)),
      );
    } finally {
      setBusy(false);
      setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? { ...m, done: true } : m)));
    }
  }

  return (
    <div className="workspace">
      <div
        className={`conversation${dragOver ? " dragOver" : ""}`}
        onDragOver={(e) => {
          // preventDefault is what allows a drop here — without it the browser
          // navigates away to the file itself.
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={(e) => {
          // Only clear when the pointer truly left the card: moving between
          // child elements fires leave/enter pairs that must not flicker it.
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragOver(false);
        }}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          pickFiles(e.dataTransfer.files);
        }}
      >
        <div className="conversationHeader">
          <div className="chatTitle">
            <div className="eyebrow">国数周报 Agent</div>
            <h1>周报问答</h1>
          </div>
          <div className="chatHeaderTools">
            {sessions.length > 0 && (
              <select
                className="sessionSelect"
                value={sessionId}
                onChange={(e) => {
                  const target = e.target.value;
                  if (target === "__new__") {
                    newChat();
                  } else if (target) {
                    switchSession(target);
                  }
                }}
                title="切换会话"
              >
                {!sessionId && (
                  <option value="" disabled>
                    新对话
                  </option>
                )}
                {sessions.map((session) => (
                  <option key={session.id} value={session.id}>
                    {session.title}
                  </option>
                ))}
                <option value="__new__">＋ 新建对话</option>
              </select>
            )}
            <button className="newChatButton" onClick={newChat} title="开始一轮新对话(历史仍保留在历史页)">
              新建对话
            </button>
            <div className="prefsPanel">
            <label>
              身份
              <select value={identity} onChange={(e) => setIdentity(e.target.value)}>
                <option>领导</option>
                <option>个人</option>
              </select>
            </label>
            <label>
              输出
              <select value={preference} onChange={(e) => setPreference(e.target.value)}>
                <option>结论优先</option>
                <option>过程优先</option>
              </select>
            </label>
            <span className="prefsNote" title="演示阶段为页内切换;生产阶段身份由登录用户决定(方案 6.2)">
              演示:页内切换
            </span>
            </div>
          </div>
        </div>

        <div className="messages" ref={messagesRef}>
          {messages.length === 0 && (
            <p style={{ color: "var(--muted)", fontSize: 13 }}>
              试试问:技术组有多少个正式任务?哪个专项组完成率最高?最近谁被驳回了?
            </p>
          )}
          {messages.map((message, i) =>
            message.role === "user" ? (
              <div className="userMessage" key={i}>
                <div className="avatar userAvatar">我</div>
                <div>
                  <span>用户</span>
                  {message.content && <p>{message.content}</p>}
                  {message.files && message.files.length > 0 && (
                    <div className="userAttachments">
                      {message.files.map((file) => (
                        <span className="userAttachment" key={file.name} title={`${file.name} (${formatBytes(file.size)})`}>
                          📎 {file.name}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <AssistantMessageView key={i} message={message} />
            ),
          )}
        </div>

        <div className="composer">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              pickFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            onPaste={(e) => {
              // Ctrl+V a copied file / screenshot: treat it as an attachment.
              // Text pastes (no files) fall through to the normal edit path.
              const files = Array.from(e.clipboardData.files);
              if (files.length) {
                e.preventDefault();
                pickFiles(files);
              }
            }}
            placeholder="问周报数据,或上传材料分析…(Enter 发送,Shift+Enter 换行)"
            rows={2}
          />
          <div className="composerButtons">
            <button
              className="attachButton"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy}
              title="上传材料(Excel / PDF / 文本,单个 ≤20MB,可多选)"
            >
              📎
            </button>
            <button className="sendButton" onClick={() => void send()} disabled={busy || (!input.trim() && !pendingFiles.length)}>
              {busy ? "…" : "发送"}
            </button>
          </div>
        </div>
        {pendingFiles.length > 0 && (
          <div className="attachChips">
            {pendingFiles.map((file, index) => (
              <span className="attachChip" key={`${file.name}-${index}`} title={`${file.name} (${formatBytes(file.size)})`}>
                📎 {file.name}
                <button
                  className="attachChipRemove"
                  onClick={() => setPendingFiles((prev) => prev.filter((_, j) => j !== index))}
                  title="移除"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="composerHint">数据来源:演示库(weekly_mock),非集团真实周报 · 首 token 流式可见 · 支持点击📎、拖拽、Ctrl+V 上传材料</div>
      </div>
    </div>
  );
}
