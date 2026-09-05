import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Download, FileText, FolderOpen, X } from "lucide-react";
import { readWorkspaceFile, revealWorkspacePath } from "../api";
import type { Task } from "../types";
import { ArtifactFileBody } from "./artifact-file-body";
import { TreasureVisual } from "./treasure";

export function ArtifactDrawer({
  task,
  listMode,
  initialFile,
  onClose,
  onSave,
  filePathOf,
  fileDataOf,
}: {
  task: Task;
  listMode: "new" | "history";
  initialFile?: string;
  onClose: () => void;
  onSave: (task: Task) => void;
  filePathOf: (name: string) => string | undefined;
  fileDataOf?: (name: string) => string | undefined;
}) {
  const fileNames = useMemo(() => {
    const names = listMode === "new" ? task.newDeliverables : task.files;
    return [...new Set(names.filter(Boolean))];
  }, [listMode, task.newDeliverables, task.files]);

  const [selectedName, setSelectedName] = useState(() => {
    if (initialFile && fileNames.includes(initialFile)) return initialFile;
    return fileNames[0] ?? "";
  });
  const [fileData, setFileData] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [revealBusy, setRevealBusy] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const acceptTimer = useRef<number | null>(null);

  useEffect(() => {
    if (initialFile && fileNames.includes(initialFile)) setSelectedName(initialFile);
  }, [initialFile, fileNames]);

  useEffect(() => () => {
    if (acceptTimer.current) window.clearTimeout(acceptTimer.current);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const inline = selectedName ? fileDataOf?.(selectedName) : undefined;
    if (inline) {
      setFileData(inline);
      setLoadError("");
      setLoading(false);
      return;
    }
    const path = selectedName ? filePathOf(selectedName) : undefined;
    if (!selectedName || !path) {
      setFileData("");
      setLoadError(selectedName ? "历史记录中没有该文件的路径，无法读取。" : "");
      setLoading(false);
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
  }, [selectedName, filePathOf, fileDataOf]);

  const handleReveal = async () => {
    const path = selectedName ? filePathOf(selectedName) : undefined;
    if (!path || revealBusy) return;
    setRevealBusy(true);
    setLoadError("");
    try {
      await revealWorkspacePath(path);
    } catch (err) {
      setLoadError((err as Error).message || "打开失败");
    } finally {
      setRevealBusy(false);
    }
  };

  const acceptWithCelebration = () => {
    if (accepting || task.deliveryState === "saved") return;
    setAccepting(true);
    acceptTimer.current = window.setTimeout(() => onSave(task), 600);
  };

  const empty = fileNames.length === 0;
  const kicker = empty
    ? "交付物"
    : listMode === "new"
      ? (task.deliveryState === "saved" ? "已保存的交付物" : "新交付物已就绪")
      : "历史交付物";
  const showSave = listMode === "new" && task.newDeliverables.length > 0 && task.deliveryState !== "saved";

  return createPortal(
    <div className="preview-drawer-shell">
      <button type="button" className="preview-scrim" aria-label="关闭交付物" onClick={onClose} />
      <aside className="artifact-drawer" role="dialog" aria-modal="true" aria-label={`${task.title} 交付物`}>
        <header className="preview-drawer-header">
          <div className="preview-title-wrap">
            <TreasureVisual state={task.newDeliverables.length ? "ready" : task.deliveryState === "saved" ? "saved" : "none"} size="mini" />
            <div className="preview-title" title={task.title}>{task.title}</div>
            <em className="preview-task-name">{kicker}</em>
          </div>
          <div className="preview-actions">
            <button type="button" className="preview-icon-btn" title={selectedName ? (revealBusy ? "正在打开…" : "在文件夹中显示") : "无文件"} disabled={!selectedName || revealBusy} onClick={() => void handleReveal()} aria-label="在文件夹中显示"><FolderOpen size={16} /></button>
            {fileData ? (
              <a className="preview-icon-btn" title="下载" href={`data:application/octet-stream;base64,${fileData}`} download={selectedName} aria-label="下载"><Download size={16} /></a>
            ) : null}
            <button type="button" className="preview-icon-btn" title="关闭" onClick={onClose} aria-label="关闭"><X size={16} /></button>
          </div>
        </header>
        <div className="artifact-drawer-body">
          <div className="artifact-file-list">
            {empty ? (
              <div className="artifact-file-empty">暂无交付物</div>
            ) : (
              fileNames.map((name) => (
                <button
                  key={name}
                  type="button"
                  className={`artifact-file-row${selectedName === name ? " active" : ""}`}
                  onClick={() => setSelectedName(name)}
                >
                  <FileText size={15} />
                  <span>{name}</span>
                </button>
              ))
            )}
          </div>
          <div className="artifact-file-preview">
            {loading ? (
              <p>正在读取文件…</p>
            ) : fileData ? (
              <ArtifactFileBody key={`${selectedName}:${fileData.slice(0, 32)}`} file={{ name: selectedName, data: fileData }} />
            ) : (
              <p>{loadError || "选择左侧文件查看预览"}</p>
            )}
          </div>
        </div>
        {showSave && (
          <footer className="artifact-drawer-footer">
            <button type="button" className="ht-btn" onClick={onClose}>稍后处理</button>
            <button type="button" className="ht-btn primary artifact-accept" disabled={accepting} onClick={acceptWithCelebration}>
              <TreasureVisual state={accepting ? "ready" : "none"} size="mini" />
              {accepting ? "正在保存成果" : task.deliveryState === "saved" ? <><Check size={14} />已保存到成果库</> : "保存到成果库"}
            </button>
          </footer>
        )}
      </aside>
    </div>,
    document.body,
  );
}
