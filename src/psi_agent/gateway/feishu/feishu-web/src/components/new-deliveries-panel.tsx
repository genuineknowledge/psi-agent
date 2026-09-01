import { createPortal } from "react-dom";
import { ChevronRight, X } from "lucide-react";
import type { Task } from "../types";
import { TreasureVisual } from "./treasure";

export function NewDeliveriesPanel({
  tasks,
  onOpen,
  onClose,
}: {
  tasks: Task[];
  onOpen: (taskId: string) => void;
  onClose: () => void;
}) {
  return createPortal(
    <div className="preview-drawer-shell">
      <button type="button" className="preview-scrim" aria-label="关闭新交付物" onClick={onClose} />
      <aside className="new-deliveries-drawer" role="dialog" aria-modal="true" aria-label="新交付物">
        <header className="preview-drawer-header">
          <div className="preview-title-wrap">
            <TreasureVisual state="ready" size="mini" />
            <div className="preview-title">新交付物</div>
            <em className="preview-task-name">{tasks.length} 个任务待确认</em>
          </div>
          <div className="preview-actions">
            <button type="button" className="preview-icon-btn" title="关闭" onClick={onClose} aria-label="关闭"><X size={16} /></button>
          </div>
        </header>
        <div className="new-deliveries-body">
          {tasks.length === 0 ? (
            <div className="new-deliveries-empty">暂无未确认的新交付物</div>
          ) : (
            tasks.map((task) => (
              <button key={task.id} type="button" className="new-delivery-row" onClick={() => onOpen(task.id)}>
                <TreasureVisual state="ready" size="compact" />
                <div><strong>{task.title}</strong><em>{task.newDeliverables.length} 份待确认 · {task.updated}</em></div>
                <ChevronRight size={15} />
              </button>
            ))
          )}
        </div>
      </aside>
    </div>,
    document.body,
  );
}
