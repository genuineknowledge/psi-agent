import { useState } from "react";
import { Download, Loader2, X } from "lucide-react";
import { fetchSessionHistoryFile } from "../api";
import { saveBlob } from "../services/fileDownload";

/**
 * 导出对话历史 —— 勾选会话, 逐个下回它的**原始** ``jsonl``。
 *
 * 文件名就是 ``{session_id}.jsonl``: 与磁盘上的那份同名, 导出后拿回去能和
 * ``{appdata}/histories/`` 里的记录对上, 也不需要为中文标题做文件名安全化。
 * 哪个文件是哪条会话, 由列表里的标题标注。
 */
export type ExportableSession = {
  id: string;
  title: string;
  fromIm: boolean;
  updated: string;
};

export function ExportHistoryDialog({
  sessions,
  onClose,
}: {
  sessions: ExportableSession[];
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const exportSelected = async () => {
    const chosen = sessions.filter((s) => selected.has(s.id));
    if (!chosen.length) return;
    setBusy(true);
    setError("");
    let done = 0;
    try {
      for (const session of chosen) {
        setProgress(`${session.title}（${done + 1}/${chosen.length}）`);
        const blob = await fetchSessionHistoryFile(session.id);
        saveBlob(blob, `${session.id}.jsonl`);
        done += 1;
        await new Promise((resolve) => window.setTimeout(resolve, 300));
      }
      setProgress("");
      setSelected(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="chest-backdrop" role="dialog" aria-modal="true" aria-label="导出对话历史">
      <div className="chest-panel">
        <header className="chest-head">
          <div>
            <strong>导出对话历史</strong>
            <em>勾选会话，下载它的原始 jsonl（{sessions.length} 条）</em>
          </div>
          <button type="button" className="chest-close" aria-label="关闭" title="关闭" onClick={onClose}>
            <X size={16} />
          </button>
        </header>

        <div className="chest-body">
          {sessions.length === 0 && <p className="chest-empty">当前身份下没有可见的会话。</p>}
          <ul className="export-list">
            {sessions.map((session) => (
              <li key={session.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={selected.has(session.id)}
                    onChange={() => toggle(session.id)}
                    disabled={busy}
                  />
                  <span className="export-title">{session.title}</span>
                  {session.fromIm && <span className="export-badge">来自飞书对话</span>}
                  <em>{session.updated}</em>
                </label>
              </li>
            ))}
          </ul>
        </div>

        {error && <div className="chest-error" role="alert">{error}</div>}

        <footer className="chest-foot">
          <button
            type="button"
            className="ht-btn"
            disabled={busy || sessions.length === 0}
            onClick={() => setSelected(new Set(sessions.map((s) => s.id)))}
          >
            全选
          </button>
          <button type="button" className="ht-btn" disabled={busy || !selected.size} onClick={() => setSelected(new Set())}>
            清空
          </button>
          <span className="chest-progress">{progress}</span>
          <button
            type="button"
            className="ht-btn primary"
            disabled={busy || !selected.size}
            onClick={() => void exportSelected()}
          >
            {busy ? <Loader2 size={14} className="chest-spin" /> : <Download size={14} />}
            导出所选（{selected.size}）
          </button>
        </footer>
      </div>
    </div>
  );
}
