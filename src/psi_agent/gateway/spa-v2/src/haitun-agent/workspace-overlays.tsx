import {
  AlertCircle,
  Bell,
  CheckCircle2,
  ChevronRight,
  Download,
  FileArchive,
  FileText,
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
  findDeliverableFile,
} from "../utils/filePreviewUtils";
import { mobileHaptic, prefersReducedMotion } from "./client-feedback";
import type { ChatFile, InboxItem, Task } from "./model";
import { ProgressRing, TreasureVisual } from "./primitives";

function fileIcon(name: string) {
  const n = name.toLowerCase();
  if (n.endsWith(".xlsx") || n.endsWith(".xls") || n.endsWith(".csv")) return <Grid2X2 size={17} />;
  if (n.endsWith(".pdf") || n.endsWith(".md") || n.endsWith(".markdown") || n.endsWith(".txt")) {
    return <FileText size={17} />;
  }
  return <FileArchive size={17} />;
}

export function ArtifactDrawer({
  task,
  files = [],
  onClose,
  onSave,
  onRevise,
  onDownload,
}: {
  task: Task;
  /** Live SSE blob payloads keyed by deliverable basename (may be empty after reload). */
  files?: ChatFile[];
  onClose: () => void;
  onSave: (task: Task) => void;
  onRevise: (task: Task) => void;
  onDownload?: (fileName: string) => void;
}) {
  const [selectedFile, setSelectedFile] = useState(0);
  const [accepting, setAccepting] = useState(false);
  const acceptTimer = useRef<number | null>(null);
  const empty = task.deliverables.length === 0;

  useEffect(() => {
    setSelectedFile((i) => Math.min(i, Math.max(0, task.deliverables.length - 1)));
  }, [task.deliverables]);

  useEffect(() => () => {
    if (acceptTimer.current) window.clearTimeout(acceptTimer.current);
  }, []);

  const selectedName = task.deliverables[selectedFile] ?? "";
  const selectedBlob = useMemo(
    () => (selectedName ? findDeliverableFile(selectedName, files) : undefined),
    [selectedName, files],
  );

  const acceptWithCelebration = () => {
    if (accepting || empty) return;
    setAccepting(true);
    mobileHaptic([10, 28, 16]);
    acceptTimer.current = window.setTimeout(
      () => onSave(task),
      prefersReducedMotion() ? 30 : 820,
    );
  };

  const handleDownload = () => {
    if (selectedBlob) {
      downloadChatFile(selectedBlob);
      return;
    }
    onDownload?.(selectedName);
  };

  return (
    <div className="drawer-layer" role="dialog" aria-modal="true" aria-label={`${task.shortTitle}交付物预览`}>
      <button className="drawer-backdrop" type="button" onClick={onClose} aria-label="关闭预览" />
      <aside className="artifact-drawer">
        <header className="drawer-header">
          <div>
            <span className="gold-kicker">
              <Sparkles size={14} />{" "}
              {empty ? "交付物" : task.deliveryState === "saved" ? "已保存的交付物" : "新交付物已就绪"}
            </span>
            <h2>{task.shortTitle}</h2>
            <span className="drawer-task-state"><CheckCircle2 size={13} /> 任务状态：{task.statusLabel} · 交付物状态独立记录</span>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭交付物预览">
            <X size={20} />
          </button>
        </header>

        {empty ? (
          <div className="artifact-empty">
            <TreasureVisual state="none" size="card" />
            <strong>暂时没有新的交付物</strong>
            <p>Agent 产出文件后，这里会出现可查看的宝箱内容。</p>
          </div>
        ) : (
          <>
            <div className="artifact-files">
              {task.deliverables.map((file, index) => (
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
                <span className="document-toolbar-label" title={selectedName}>
                  预览 · {selectedName || "未选择"}
                </span>
                <button
                  type="button"
                  disabled={!selectedBlob && !onDownload}
                  onClick={handleDownload}
                  aria-label={`下载 ${selectedName}`}
                >
                  <Download size={16} />
                </button>
              </div>
              {selectedBlob ? (
                <ArtifactFileBody key={`${selectedBlob.name}:${selectedBlob.data.slice(0, 32)}`} file={selectedBlob} />
              ) : (
                <div className="artifact-preview-missing">
                  <FileText size={28} />
                  <strong>暂无文件内容可预览</strong>
                  <p>
                    本会话尚未收到该文件的附件数据（例如刷新后历史不带回 blob）。
                    请让 Agent 再次通过 <code>[SEND:]</code> 交付，或从对话气泡中打开预览。
                  </p>
                </div>
              )}
            </div>

            <footer className="drawer-footer">
              <button type="button" className="secondary-button" disabled={accepting} onClick={() => onRevise(task)}><MessageCircle size={16} /> 让 Agent 修改</button>
              <button type="button" className={`gold-button ${accepting ? "accepting" : ""}`} disabled={accepting || task.deliveryState === "saved"} onClick={acceptWithCelebration}>
                <TreasureVisual state={task.deliveryState} size="mini" opening={accepting} />
                {task.deliveryState === "saved" ? "已保存到成果库" : accepting ? "正在保存成果" : "保存到成果库"}
              </button>
            </footer>
            {accepting && (
              <div className="accept-celebration" aria-live="polite">
                <div className="celebration-glow" />
                <TreasureVisual state="ready" size="hero" opening />
                <div className="celebration-coins" aria-hidden="true">
                  {Array.from({ length: 14 }, (_, index) => <i key={index} />)}
                </div>
                <strong>{task.deliverables.length} 份交付物已保存</strong>
                <span>Agent 会保留本次成果，随时可以继续迭代</span>
              </div>
            )}
          </>
        )}
      </aside>
    </div>
  );
}
export function InboxDrawer({
  items,
  tasks,
  onClose,
  onMarkAllRead,
  onOpenItem,
}: {
  items: InboxItem[];
  tasks: Task[];
  onClose: () => void;
  onMarkAllRead: () => void;
  onOpenItem: (item: InboxItem) => void;
}) {
  const unread = items.filter((item) => item.unread).length;
  return (
    <div className="drawer-layer inbox-layer" role="dialog" aria-modal="true" aria-label="收件箱">
      <button className="drawer-backdrop" type="button" onClick={onClose} aria-label="关闭收件箱" />
      <aside className="inbox-drawer">
        <header className="inbox-header">
          <div><span className="eyebrow">任务动态</span><h2>收件箱</h2></div>
          <div>
            <button type="button" onClick={onMarkAllRead} disabled={!unread}>全部标为已读</button>
            <button type="button" className="icon-button" onClick={onClose} aria-label="关闭收件箱"><X size={19} /></button>
          </div>
        </header>
        <div className="inbox-summary"><Bell size={15} /><span>{unread ? `${unread} 条未读动态` : "所有动态均已读"}</span></div>
        <div className="inbox-list">
          {items.map((item) => {
            const task = tasks.find((candidate) => candidate.id === item.taskId);
            return (
              <button type="button" className={`inbox-item ${item.unread ? "unread" : ""}`} key={item.id} onClick={() => onOpenItem(item)}>
                <span className={`inbox-kind ${item.kind}`}>
                  {item.kind === "attention" ? <AlertCircle size={17} /> : item.kind === "delivery" ? <TreasureVisual state="ready" size="mini" /> : <ProgressRing value={task?.progress ?? 0} size="sm" showValue={false} />}
                </span>
                <span className="inbox-copy"><strong>{item.title}</strong><span>{item.detail}</span><em>{task?.shortTitle} · {item.time}</em></span>
                {item.unread && <i className="unread-dot" />}
                <ChevronRight size={16} />
              </button>
            );
          })}
        </div>
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
        <span><strong>通知与提醒</strong><em>任务动态进入收件箱</em></span><i className={notificationsEnabled ? "on" : ""} />
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
