import {
  CheckCircle2,
  ChevronRight,
  Download,
  FileArchive,
  FileText,
  FolderOpen,
  Grid2X2,
  MessageCircle,
  Settings2,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArtifactFileBody } from "../components/ArtifactFileBody";
import {
  downloadChatFile,
  ensureChatFileData,
  findDeliverableFile,
  revealDeliverableInFolder,
} from "../utils/filePreviewUtils";
import { mobileHaptic, prefersReducedMotion } from "./client-feedback";
import type { ChatFile, Task } from "./model";
import { TreasureVisual } from "./primitives";

function fileIcon(name: string) {
  const n = name.toLowerCase();
  if (n.endsWith(".xlsx") || n.endsWith(".xls") || n.endsWith(".csv")) return <Grid2X2 size={17} />;
  if (
    n.endsWith(".pdf")
    || n.endsWith(".md")
    || n.endsWith(".markdown")
    || n.endsWith(".txt")
    || n.endsWith(".docx")
    || n.endsWith(".doc")
  ) {
    return <FileText size={17} />;
  }
  return <FileArchive size={17} />;
}

export function ArtifactDrawer({
  task,
  files = [],
  listMode = "history",
  initialFile,
  workspaceRoot = "",
  onClose,
  onSave,
  onRevise,
}: {
  task: Task;
  /** Live SSE blob payloads keyed by deliverable basename (may be empty after reload). */
  files?: ChatFile[];
  /** ``new`` = unread chest; ``history`` = all session deliverables. */
  listMode?: "new" | "history";
  initialFile?: string;
  workspaceRoot?: string;
  onClose: () => void;
  onSave: (task: Task) => void;
  onRevise: (task: Task) => void;
}) {
  const fileNames = useMemo(() => {
    if (listMode === "new") {
      return task.newDeliverables.length ? task.newDeliverables : [];
    }
    return task.deliverables;
  }, [listMode, task.deliverables, task.newDeliverables]);

  const empty = fileNames.length === 0;
  const [selectedFile, setSelectedFile] = useState(0);
  const [accepting, setAccepting] = useState(false);
  const [loadedFiles, setLoadedFiles] = useState<ChatFile[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [revealBusy, setRevealBusy] = useState(false);
  const acceptTimer = useRef<number | null>(null);

  useEffect(() => {
    setSelectedFile(() => {
      if (initialFile) {
        const idx = fileNames.indexOf(initialFile);
        if (idx >= 0) return idx;
      }
      return 0;
    });
  }, [fileNames, initialFile, listMode]);

  useEffect(() => () => {
    if (acceptTimer.current) window.clearTimeout(acceptTimer.current);
  }, []);

  const selectedName = fileNames[selectedFile] ?? "";
  const selectedBlob = useMemo(() => {
    if (!selectedName) return undefined;
    const fromLive = findDeliverableFile(selectedName, files);
    const fromDisk = findDeliverableFile(selectedName, loadedFiles);
    // Prefer a payload with data (live SSE or lazy-loaded); else keep path stub.
    if (fromLive?.data.trim()) return fromLive;
    if (fromDisk?.data.trim()) return fromDisk;
    return fromDisk ?? fromLive ?? (
      task.deliverablePaths[selectedName]
        ? { name: selectedName, data: "", path: task.deliverablePaths[selectedName] }
        : undefined
    );
  }, [selectedName, files, loadedFiles, task.deliverablePaths]);

  useEffect(() => {
    if (!selectedName || selectedBlob?.data.trim()) {
      setLoadError(null);
      setLoading(false);
      return;
    }
    const path = selectedBlob?.path?.trim() || task.deliverablePaths[selectedName];
    if (!path) {
      setLoadError("历史记录中没有该文件的路径，无法从海豚工作室读取。");
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    void ensureChatFileData({ name: selectedName, data: "", path }, workspaceRoot)
      .then((res) => {
        if (cancelled) return;
        setLoadedFiles((current) => {
          const rest = current.filter((f) => f.name !== res.name && f.name !== selectedName);
          return [...rest, res];
        });
      })
      .catch((e) => {
        if (cancelled) return;
        setLoadError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedName, selectedBlob?.data, selectedBlob?.path, task.deliverablePaths, workspaceRoot]);

  const acceptWithCelebration = () => {
    if (accepting || empty || !task.newDeliverables.length) return;
    setAccepting(true);
    mobileHaptic([10, 28, 16]);
    acceptTimer.current = window.setTimeout(
      () => onSave(task),
      prefersReducedMotion() ? 30 : 820,
    );
  };

  const handleDownload = () => {
    if (!selectedName) return;
    const blob = selectedBlob;
    if (blob?.data.trim()) {
      downloadChatFile(blob);
      return;
    }
    const path = blob?.path || task.deliverablePaths[selectedName];
    if (!path) {
      setLoadError("历史记录中没有该文件的路径，无法下载。");
      return;
    }
    void ensureChatFileData({ name: selectedName, data: "", path }, workspaceRoot)
      .then((loaded) => {
        setLoadedFiles((current) => {
          const rest = current.filter((f) => f.name !== loaded.name && f.name !== selectedName);
          return [...rest, loaded];
        });
        downloadChatFile(loaded);
      })
      .catch((e) => {
        setLoadError(e instanceof Error ? e.message : String(e));
      });
  };

  const revealPath = selectedBlob?.path?.trim() || task.deliverablePaths[selectedName] || "";
  const handleReveal = () => {
    if (!revealPath || revealBusy) return;
    setRevealBusy(true);
    setLoadError(null);
    void revealDeliverableInFolder(revealPath, workspaceRoot)
      .catch((e) => {
        setLoadError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setRevealBusy(false));
  };

  const kicker = empty
    ? "交付物"
    : listMode === "new"
      ? (task.deliveryState === "saved" ? "已保存的交付物" : "新交付物已就绪")
      : "历史交付物";

  const showSave = listMode === "new" && task.newDeliverables.length > 0;

  return (
    <div className="drawer-layer" role="dialog" aria-modal="true" aria-label={`${task.shortTitle}交付物预览`}>
      <button className="drawer-backdrop" type="button" onClick={onClose} aria-label="关闭预览" />
      <aside className="artifact-drawer">
        <header className="drawer-header">
          <div>
            <span className="gold-kicker">
              <Sparkles size={14} />{" "}
              {kicker}
            </span>
            <h2>{task.shortTitle}</h2>
            <span className="drawer-task-state">
              <CheckCircle2 size={13} />
              {" "}
              {listMode === "history"
                ? `本会话累计 ${task.deliverables.length} 份历史交付物`
                : `任务状态：${task.statusLabel} · 新交付 ${task.newDeliverables.length} 份`}
            </span>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭交付物预览">
            <X size={20} />
          </button>
        </header>

        {empty ? (
          <div className="artifact-empty">
            <TreasureVisual state="none" size="card" />
            <strong>{listMode === "new" ? "暂时没有新的交付物" : "暂时没有历史交付物"}</strong>
            <p>
              {listMode === "new"
                ? "Agent 产出文件后，新交付物会出现在宝箱里；本会话全部交付物可在左侧「历史交付物」查看。"
                : "Agent 通过 [SEND:] 交付文件后，会按当前会话累计在这里。"}
            </p>
          </div>
        ) : (
          <>
            <div className="artifact-files">
              {fileNames.map((file, index) => (
                <button
                  type="button"
                  className={selectedFile === index ? "active" : ""}
                  key={file}
                  onClick={() => setSelectedFile(index)}
                >
                  {fileIcon(file)}
                  <span>{file}</span>
                  <ChevronRight size={15} />
                </button>
              ))}
            </div>

            <div className="document-preview">
              <div className="document-toolbar">
                <span className="document-toolbar-label" title={revealPath || selectedName}>
                  预览 · {selectedName || "未选择"}
                </span>
                <div className="document-toolbar-actions">
                  <button
                    type="button"
                    disabled={!revealPath || revealBusy}
                    onClick={handleReveal}
                    title={revealPath ? (revealBusy ? "正在打开…" : "在文件夹中显示") : "无磁盘路径，无法定位"}
                    aria-label={`在文件夹中显示 ${selectedName}`}
                  >
                    <FolderOpen size={16} />
                  </button>
                  <button
                    type="button"
                    disabled={!selectedBlob?.data.trim() && !selectedBlob?.path && !task.deliverablePaths[selectedName]}
                    onClick={handleDownload}
                    aria-label={`下载 ${selectedName}`}
                    title="下载"
                  >
                    <Download size={16} />
                  </button>
                </div>
              </div>
              {selectedBlob?.data.trim() ? (
                <ArtifactFileBody key={`${selectedBlob.name}:${selectedBlob.data.slice(0, 32)}`} file={selectedBlob} />
              ) : loading ? (
                <div className="artifact-preview-missing">
                  <FileText size={28} />
                  <strong>正在从海豚工作室读取…</strong>
                </div>
              ) : (
                <div className="artifact-preview-missing">
                  <FileText size={28} />
                  <strong>暂无文件内容可预览</strong>
                  <p>
                    {loadError
                      ?? "本会话尚未收到该文件的附件数据，且无法从海豚工作室路径读取。请让 Agent 再次通过 [SEND:] 交付。"}
                  </p>
                </div>
              )}
            </div>

            <footer className="drawer-footer">
              <button type="button" className="secondary-button" disabled={accepting} onClick={() => onRevise(task)}><MessageCircle size={16} /> 让 Agent 修改</button>
              {showSave ? (
                <button type="button" className={`gold-button ${accepting ? "accepting" : ""}`} disabled={accepting || task.deliveryState === "saved"} onClick={acceptWithCelebration}>
                  <TreasureVisual state={task.deliveryState} size="mini" opening={accepting} />
                  {task.deliveryState === "saved" ? "已保存到成果库" : accepting ? "正在保存成果" : "保存到成果库"}
                </button>
              ) : (
                <button type="button" className="secondary-button" onClick={onClose}>关闭</button>
              )}
            </footer>
            {accepting && (
              <div className="accept-celebration" aria-live="polite">
                <div className="celebration-glow" />
                <TreasureVisual state="ready" size="hero" opening />
                <div className="celebration-coins" aria-hidden="true">
                  {Array.from({ length: 14 }, (_, index) => <i key={index} />)}
                </div>
                <strong>{task.newDeliverables.length} 份新交付物已保存</strong>
                <span>历史交付物列表仍会保留本会话全部产出</span>
              </div>
            )}
          </>
        )}
      </aside>
    </div>
  );
}

export function SidebarSettings({
  notificationsEnabled,
  hapticsEnabled,
  onToggleNotifications,
  onToggleHaptics,
  onAction,
  onClose,
}: {
  notificationsEnabled: boolean;
  hapticsEnabled: boolean;
  onToggleNotifications: () => void;
  onToggleHaptics: () => void;
  onAction: (label: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="settings-popover" role="menu" aria-label="HaiTun Agent 设置">
      <header><span><Settings2 size={15} /> 设置</span><button type="button" onClick={onClose} aria-label="关闭设置"><X size={14} /></button></header>
      <button type="button" className="settings-toggle" onClick={onToggleNotifications} role="menuitem">
        <span><strong>通知与提醒</strong><em>任务状态更新时提醒</em></span><i className={notificationsEnabled ? "on" : ""} />
      </button>
      <button type="button" className="settings-toggle" onClick={onToggleHaptics} role="menuitem">
        <span><strong>动效与触觉反馈</strong><em>金币动画与手机轻震</em></span><i className={hapticsEnabled ? "on" : ""} />
      </button>
      <button type="button" className="settings-row" role="menuitem" onClick={() => onAction("默认交付位置：成果库")}><span><strong>默认交付位置</strong><em>成果库</em></span><ChevronRight size={14} /></button>
      <button type="button" className="settings-row" role="menuitem" onClick={() => onAction("语言与称呼：简体中文 · 您")}><span><strong>语言与称呼</strong><em>简体中文 · 您</em></span><ChevronRight size={14} /></button>
      <button type="button" className="settings-row" role="menuitem" onClick={() => onAction("快捷键：⌘/Ctrl K 搜索 · ⌘/Ctrl N 新建")}><span><strong>键盘快捷键</strong><em>⌘/Ctrl K 搜索 · ⌘/Ctrl N 新建</em></span><ChevronRight size={14} /></button>
      <button type="button" className="settings-row" role="menuitem" onClick={() => onAction("帮助与反馈入口已预留")}><span><strong>帮助与反馈</strong><em>产品说明与问题反馈</em></span><ChevronRight size={14} /></button>
      <footer>HaiTun Agent · Demo</footer>
    </div>
  );
}
