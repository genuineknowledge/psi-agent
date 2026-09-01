import { FileText, X } from "lucide-react";
import type { Task } from "../types";

export function DeliverablesDrawer({ tasks, onClose }: { tasks: Task[]; onClose: () => void }) {
  const allFiles = tasks.flatMap((t) => t.files.map((f) => ({ task: t, file: f })));
  return (
    <div className="ht-overlay" onClick={onClose}>
      <aside className="ht-drawer ht-deliverables-drawer" role="dialog" aria-label="全部交付物" onClick={(e) => e.stopPropagation()}>
        <div className="ht-drawer-head">
          <div><h3>全部交付物</h3><p>{tasks.length} 个任务 · {allFiles.length} 份文件</p></div>
          <button type="button" className="ht-iconbtn" aria-label="关闭" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="ht-drawer-body">
          {allFiles.map((d, i) => (
            <div key={i} className="ht-drawer-file">
              <FileText size={16} />
              <div><strong>{d.file}</strong><em>{d.task.title} · {d.task.updated}</em></div>
              <button type="button">查看</button>
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
