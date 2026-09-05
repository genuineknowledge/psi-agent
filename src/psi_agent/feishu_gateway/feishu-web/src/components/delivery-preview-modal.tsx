import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Download, FileText, FolderOpen, X } from "lucide-react";
import { readWorkspaceFile, revealWorkspacePath } from "../api";
import { ArtifactFileBody } from "./artifact-file-body";

export function DeliveryPreviewModal({ name, task, path, data, onClose }: { name: string; task: string; path?: string; data?: string; onClose: () => void }) {
  const [fileData, setFileData] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [revealBusy, setRevealBusy] = useState(false);
  const [revealError, setRevealError] = useState("");

  useEffect(() => {
    let cancelled = false;
    if (data) {
      setFileData(data);
      setLoading(false);
      setLoadError("");
      return;
    }
    if (!path) {
      setLoadError("暂无文件路径，无法读取内容");
      return;
    }
    setLoading(true);
    setLoadError("");
    setFileData("");
    readWorkspaceFile(path)
      .then((file) => {
        if (cancelled) return;
        setFileData(file.data);
      })
      .catch((err) => {
        if (!cancelled) setLoadError((err as Error).message || "文件读取失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [path, data]);

  const handleReveal = async () => {
    if (!path || revealBusy) return;
    setRevealBusy(true);
    setRevealError("");
    try {
      await revealWorkspacePath(path);
    } catch (err) {
      setRevealError((err as Error).message || "打开失败");
    } finally {
      setRevealBusy(false);
    }
  };

  return createPortal(
    <div className="preview-drawer-shell">
      <button type="button" className="preview-scrim" aria-label="关闭预览" onClick={onClose} />
      <aside className="file-preview preview-drawer" role="dialog" aria-modal="true" aria-label={`预览 ${name}`}>
        <header className="preview-drawer-header">
          <div className="preview-title-wrap"><FileText size={18} /><div className="preview-title" title={path || name}>{name}</div><em className="preview-task-name">{task}</em></div>
          <div className="preview-actions">
            <button type="button" className="preview-icon-btn" title={path ? (revealBusy ? "正在打开…" : "在文件夹中显示") : "无磁盘路径，无法定位"} disabled={!path || revealBusy} onClick={() => void handleReveal()} aria-label="在文件夹中显示"><FolderOpen size={16} /></button>
            {fileData ? (
              <a className="preview-icon-btn" title="下载" href={`data:application/octet-stream;base64,${fileData}`} download={name} aria-label="下载"><Download size={16} /></a>
            ) : null}
            <button type="button" className="preview-icon-btn" title="关闭" onClick={onClose} aria-label="关闭"><X size={16} /></button>
          </div>
        </header>
        {revealError ? <div className="preview-reveal-error" role="alert">{revealError}</div> : null}
        <div className="preview-drawer-body">
          {loading ? (
            <p>正在读取文件…</p>
          ) : fileData ? (
            <ArtifactFileBody file={{ name, data: fileData }} />
          ) : (
            <p>{loadError || "暂无文件路径，无法读取内容"}</p>
          )}
        </div>
      </aside>
    </div>,
    document.body,
  );
}
