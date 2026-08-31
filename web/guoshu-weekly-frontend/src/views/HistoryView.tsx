import { useEffect, useState } from "react";
import { cleanHistoryText } from "../api";
import { listSessions, type SessionEntry } from "../session-store";

interface HistoryMessage {
  role: string;
  text: string;
}

const SESSION_KEY = "guoshu_weekly_session_id";

/** One user question plus the assistant answers that follow it. */
function pairRounds(messages: HistoryMessage[]): { question: string; answer: string }[] {
  const rounds: { question: string; answer: string }[] = [];
  let question = "";
  let answers: string[] = [];
  for (const message of messages) {
    if (message.role === "user") {
      if (question !== "") rounds.push({ question, answer: answers.join("\n\n") });
      question = cleanHistoryText(message.text);
      answers = [];
    } else {
      answers.push(message.text);
    }
  }
  if (question !== "") rounds.push({ question, answer: answers.join("\n\n") });
  return rounds;
}

/**
 * History view (P1-3): every session this browser created is listed and
 * selectable; the selected one can be exported as Excel or PDF.
 */
export function HistoryView() {
  const [sessions, setSessions] = useState<SessionEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string>(() => localStorage.getItem(SESSION_KEY) ?? "");
  const [messages, setMessages] = useState<HistoryMessage[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setSessions(listSessions());
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setMessages([]);
      setLoaded(true);
      return;
    }
    void load(selectedId);
  }, [selectedId]);

  async function load(sessionId: string) {
    setLoaded(false);
    setError("");
    try {
      const response = await fetch(`/api/sessions/${sessionId}/history`);
      if (!response.ok) {
        if (response.status >= 500) {
          throw new Error("该会话已过期(服务重启后旧会话不可回放),可导出不可继续对话。");
        }
        throw new Error(`加载失败(${response.status})`);
      }
      const payload = (await response.json()) as HistoryMessage[];
      setMessages(Array.isArray(payload) ? payload : []);
    } catch (err) {
      setMessages([]);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoaded(true);
    }
  }

  async function exportAs(format: "excel" | "pdf") {
    if (!selectedId) return;
    try {
      const response = await fetch(`/api/sessions/${selectedId}/export?format=${format}`);
      if (!response.ok) {
        const detail = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(detail?.detail ?? `导出失败(${response.status})`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const disposition = response.headers.get("content-disposition") ?? "";
      const match = /filename="([^"]+)"/.exec(disposition);
      link.download = match ? match[1] : `conversation.${format === "excel" ? "xlsx" : "pdf"}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const currentId = localStorage.getItem(SESSION_KEY) ?? "";

  return (
    <div className="workspace">
      <div className="historyCard">
        <div className="conversationHeader">
          <div>
            <div className="eyebrow">会话历史</div>
            <h1>对话记录</h1>
          </div>
          <div className="historyActions">
            <button className="generateButton" onClick={() => void exportAs("excel")} disabled={!selectedId || !messages.length}>
              导出 Excel
            </button>
            <button className="generateButton" onClick={() => void exportAs("pdf")} disabled={!selectedId || !messages.length}>
              导出 PDF
            </button>
          </div>
        </div>
        <div className="historyBody">
          <div className="sessionRail">
            {sessions.length === 0 && <p className="historyEmpty">还没有会话,先去对话页问一个问题。</p>}
            {sessions.map((session) => (
              <button
                key={session.id}
                className={`sessionItem${session.id === selectedId ? " active" : ""}`}
                onClick={() => setSelectedId(session.id)}
              >
                <span>{session.title}</span>
                <small>
                  {session.createdAt.slice(0, 10)}
                  {session.id === currentId && " · 当前"}
                </small>
              </button>
            ))}
          </div>
          <div className="historyList">
            {error && <div className="loginError">{error}</div>}
            {loaded && !messages.length && !error && <p className="historyEmpty">该会话还没有内容。</p>}
            {pairRounds(messages).map((round, index) => (
              <div className="historyRound" key={index}>
                <div className="historyRole">第 {index + 1} 轮 · 问</div>
                <div className="historyQuestion">{round.question}</div>
                {round.answer && (
                  <>
                    <div className="historyRole" style={{ marginTop: 8 }}>答</div>
                    <div className="historyText">{round.answer}</div>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
        <div className="composerHint">导出为对话记录(Excel / PDF),内容来自左侧选中的会话</div>
      </div>
    </div>
  );
}
