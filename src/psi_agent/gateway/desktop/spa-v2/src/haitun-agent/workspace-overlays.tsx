import {
  CheckCircle2,
  ChevronRight,
  Download,
  FileArchive,
  FileText,
  FolderOpen,
  Grid2X2,
  Settings2,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ArtifactFileBody } from "../components/ArtifactFileBody";
import {
  downloadChatFile,
  ensureChatFileData,
  findDeliverableFile,
  revealDeliverableInFolder,
} from "../utils/filePreviewUtils";
import type { ChatFile, Task } from "./model";
import { TreasureVisual } from "./primitives";
import { useI18n } from "../i18n";
import { displayTaskStatusLabel } from "../services/sessionBridge";

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
}: {
  task: Task;
  /** Live SSE blob payloads keyed by deliverable basename (may be empty after reload). */
  files?: ChatFile[];
  /** ``new`` = unread chest; ``history`` = all session deliverables. */
  listMode?: "new" | "history";
  initialFile?: string;
  workspaceRoot?: string;
  onClose: () => void;
}) {
  const { t, language } = useI18n();
  const fileNames = useMemo(() => {
    if (listMode === "new") {
      return task.newDeliverables.length ? task.newDeliverables : [];
    }
    return task.deliverables;
  }, [listMode, task.deliverables, task.newDeliverables]);

  const empty = fileNames.length === 0;
  const [selectedFile, setSelectedFile] = useState(0);
  const [loadedFiles, setLoadedFiles] = useState<ChatFile[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [revealBusy, setRevealBusy] = useState(false);
  const previewErrorText = (err: unknown) => {
    const msg = err instanceof Error ? err.message : String(err)
    return msg === '没有可打开的文件路径。'
      ? t('fileError.noPath')
      : msg === '历史记录中没有该文件的路径，无法从磁盘读取预览。'
        ? t('fileError.readPath')
        : msg
  }

  useEffect(() => {
    setSelectedFile(() => {
      if (initialFile) {
        const idx = fileNames.indexOf(initialFile);
        if (idx >= 0) return idx;
      }
      return 0;
    });
  }, [fileNames, initialFile, listMode]);

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
      setLoadError(t("drawer.loadErrorRead"));
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
        setLoadError(previewErrorText(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedName, selectedBlob?.data, selectedBlob?.path, task.deliverablePaths, workspaceRoot]);

  const handleDownload = () => {
    if (!selectedName) return;
    const blob = selectedBlob;
    if (blob?.data.trim()) {
      downloadChatFile(blob);
      return;
    }
    const path = blob?.path || task.deliverablePaths[selectedName];
    if (!path) {
      setLoadError(t("drawer.loadErrorDownload"));
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
        setLoadError(previewErrorText(e));
      });
  };

  const revealPath = selectedBlob?.path?.trim() || task.deliverablePaths[selectedName] || "";
  const handleReveal = () => {
    if (!revealPath || revealBusy) return;
    setRevealBusy(true);
    setLoadError(null);
    void revealDeliverableInFolder(revealPath, workspaceRoot)
      .catch((e) => {
        setLoadError(previewErrorText(e));
      })
      .finally(() => setRevealBusy(false));
  };

  const kicker = empty
    ? t("drawer.kickerDeliverables")
    : listMode === "new"
      ? t("drawer.kickerReady")
      : t("drawer.kickerHistory");

  return (
    <div className="drawer-layer" role="dialog" aria-modal="true" aria-label={t("drawer.ariaPreview", { title: task.shortTitle })}>
      <button className="drawer-backdrop" type="button" onClick={onClose} aria-label={t("drawer.closePreviewAria")} />
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
                ? t("drawer.stateHistory", { count: task.deliverables.length })
                : t("drawer.stateNew", { status: displayTaskStatusLabel(task.status, task.statusLabel, language), count: task.newDeliverables.length })}
            </span>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label={t("drawer.closeDrawerAria")}>
            <X size={20} />
          </button>
        </header>

        {empty ? (
          <div className="artifact-empty">
            <TreasureVisual state="none" size="card" />
            <strong>{listMode === "new" ? t("drawer.emptyNew") : t("drawer.emptyHistory")}</strong>
            <p>
              {listMode === "new"
                ? t("drawer.emptyNewDesc")
                : t("drawer.emptyHistoryDesc")}
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
                  {t("drawer.previewLabel", { name: selectedName || t("drawer.notSelected") })}
                </span>
                <div className="document-toolbar-actions">
                  <button
                    type="button"
                    disabled={!revealPath || revealBusy}
                    onClick={handleReveal}
                    title={revealPath ? (revealBusy ? t("chat.opening") : t("chat.showInFolder")) : t("drawer.noPath")}
                    aria-label={t("chat.showInFolderAria", { name: selectedName })}
                  >
                    <FolderOpen size={16} />
                  </button>
                  <button
                    type="button"
                    disabled={!selectedBlob?.data.trim() && !selectedBlob?.path && !task.deliverablePaths[selectedName]}
                    onClick={handleDownload}
                    aria-label={t("drawer.downloadAria", { name: selectedName })}
                    title={t("drawer.download")}
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
                  <strong>{t("drawer.reading")}</strong>
                </div>
              ) : (
                <div className="artifact-preview-missing">
                  <FileText size={28} />
                  <strong>{t("drawer.noPreview")}</strong>
                  <p>
                    {loadError ?? t("drawer.noPreviewDesc")}
                  </p>
                </div>
              )}
            </div>
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
  const { t, language } = useI18n();
  return (
    <div className="settings-popover" role="menu" aria-label={t("drawer.settingsAria")}>
      <header><span><Settings2 size={15} /> {t("app.settings")}</span><button type="button" onClick={onClose} aria-label={t("drawer.closeSettingsAria")}><X size={14} /></button></header>
      <button type="button" className="settings-toggle" onClick={onToggleNotifications} role="menuitem">
        <span><strong>{t("drawer.notifications")}</strong><em>{t("drawer.notificationsDesc")}</em></span><i className={notificationsEnabled ? "on" : ""} />
      </button>
      <button type="button" className="settings-toggle" onClick={onToggleHaptics} role="menuitem">
        <span><strong>{t("drawer.haptics")}</strong><em>{t("drawer.hapticsDesc")}</em></span><i className={hapticsEnabled ? "on" : ""} />
      </button>
      <button type="button" className="settings-row" role="menuitem" onClick={() => onAction(t("drawer.actionDeliveryLocation"))}><span><strong>{t("drawer.deliveryLocation")}</strong><em>{t("drawer.library")}</em></span><ChevronRight size={14} /></button>
      <button type="button" className="settings-row" role="menuitem" onClick={() => onAction(t("drawer.actionLanguage"))}><span><strong>{t("drawer.languageAndName")}</strong><em>{language === "zh-TW" ? t("drawer.languageValueTw") : language === "en-US" ? t("drawer.languageValueEn") : t("drawer.languageValueZh")}</em></span><ChevronRight size={14} /></button>
      <button type="button" className="settings-row" role="menuitem" onClick={() => onAction(t("drawer.actionShortcuts"))}><span><strong>{t("drawer.shortcuts")}</strong><em>{t("drawer.shortcutsValue")}</em></span><ChevronRight size={14} /></button>
      <button type="button" className="settings-row" role="menuitem" onClick={() => onAction(t("drawer.actionHelp"))}><span><strong>{t("drawer.help")}</strong><em>{t("drawer.helpDesc")}</em></span><ChevronRight size={14} /></button>
      <footer>HaiTun Agent · Demo</footer>
    </div>
  );
}
