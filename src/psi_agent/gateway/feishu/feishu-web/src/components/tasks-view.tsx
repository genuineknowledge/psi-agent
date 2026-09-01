import { Check, Download, MessageCircle, Plus, Search, Trash2, Workflow } from "lucide-react";
import type { Task } from "../types";
import { statCell, statusPill } from "./brand";
import { TreasureVisual } from "./treasure";

export interface TasksViewProps {
  tasks: Task[];
  filtered: Task[];
  counts: Record<string, number>;
  selected?: Task;
  filter: string;
  search: string;
  onFilter: (f: string) => void;
  onSearch: (v: string) => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onOpenChat: (id: string) => void;
  onOpenNewDeliverables: () => void;
  newDeliveryCount: number;
  onNewTask: () => void;
}

export function TasksView(props: TasksViewProps) {
  const { filtered, counts, selected, filter, search, onFilter, onSearch, onSelect, onDelete, onOpenChat, onOpenNewDeliverables, newDeliveryCount, onNewTask } = props;
  const filters = [["all", "全部"], ["working", "进行中"], ["attention", "待处理"], ["done", "已完成"]] as const;
  return (
    <>
      <div className="ht-dt-head">
        <div><h2>任务总览</h2><p>跨群任务与交付物统一管理</p></div>
        <div className="ht-actions">
          <button type="button" className="ht-btn"><Download size={14} />导出</button>
          <button type="button" className="ht-btn primary" onClick={onNewTask}><Plus size={14} />新建任务</button>
        </div>
      </div>
      <div className="ht-stat-row">
        {statCell(String(counts.working), "进行中")}
        {statCell(String(counts.attention), "待处理")}
        <button type="button" className="ht-stat ht-stat-action" onClick={onOpenNewDeliverables}>
          <TreasureVisual state={newDeliveryCount > 0 ? "ready" : "none"} size="compact" /><strong>{newDeliveryCount}</strong><em>新交付物</em>
        </button>
        {statCell("128", "本月执行")}
      </div>
      <div className="ht-task-toolbar">
        <div className="ht-filter-chips">
          {filters.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`ht-chip${filter === key ? " primary" : ""}`}
              aria-pressed={filter === key}
              onClick={() => onFilter(key)}
            >
              {label} {counts[key]}
            </button>
          ))}
        </div>
        <label className="ht-task-search"><Search size={13} /><input placeholder="搜索任务或交付物" value={search} onChange={(e) => onSearch(e.target.value)} /></label>
      </div>
      <div className="ht-dt-split">
        <div>
          <div className="ht-table-wrap">
            <table className="ht-table">
              <thead><tr><th>任务</th><th>状态</th><th>进度</th><th>流程</th><th>负责人</th><th>更新时间</th><th></th></tr></thead>
              <tbody>
                {filtered.length === 0 && <tr><td colSpan={7} className="ht-table-empty">没有找到匹配的任务</td></tr>}
                {filtered.map((t) => (
                  <tr key={t.id} onClick={() => onSelect(t.id)} aria-selected={selected?.id === t.id}>
                    <td>
                      <div className="ht-cell-main">
                        <strong>{t.title}</strong>
                        {t.fromIm && (
                          <span className="ht-badge-im" title="这条会话与飞书 IM 里的对话共通, 双向可见">
                            来自飞书对话
                          </span>
                        )}
                        <em>{t.sop}</em>
                      </div>
                    </td>
                    <td>{statusPill(t.status)}</td>
                    <td><div className="ht-cell-progress"><div className="ht-bar"><i style={{ width: `${t.progress}%` }} /></div><small>{t.progress}%</small></div></td>
                    <td><span className="ht-cell-sop"><Workflow size={12} />{t.sop}</span></td>
                    <td><span className="ht-avatars"><span className="ht-avatar navy">海</span></span></td>
                    <td className="ht-muted">{t.updated}</td>
                    <td>
                      <button type="button" className="ht-row-delete" aria-label="删除任务" title="删除任务" onClick={(e) => { e.stopPropagation(); onDelete(t.id); }}><Trash2 size={14} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <aside className="ht-detail-side ht-task-aside">
          {selected && (
            <>
              <div className="ht-card">
                <div className="ht-section-label"><span>当前任务</span><span className="ht-version">{selected.sop}</span></div>
                <h3>{selected.title}</h3>
                <p>{selected.owner} · {selected.updated}</p>
                {selected.contextWarning && (
                  <p className="ht-card-hint">
                    这条会话与飞书对话共用同一份上下文, 会越来越长。建议点上方「新建任务」开一个新会话。
                  </p>
                )}
                <div className="ht-bar" role="progressbar" aria-valuenow={selected.progress} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${selected.progress}%` }} /></div>
                <div className="ht-steps">{selected.steps.map((s, i) => <div key={i} className={`ht-step ${s.s}`}><span>{s.s === "done" ? <Check size={12} /> : i + 1}</span><em>{s.t}</em></div>)}</div>
                <div className="ht-actions">
                  <button type="button" className="ht-btn primary" onClick={() => onOpenChat(selected.id)}><MessageCircle size={13} />继续对话</button>
                  <button type="button" className="ht-btn" onClick={() => onDelete(selected.id)}><Trash2 size={13} />删除</button>
                </div>
              </div>
              <div className="ht-card">
                <div className="ht-section-label"><span>新交付物</span><em>{newDeliveryCount}</em></div>
                <p>点击统计数字或下方按钮，从右侧打开待确认的新交付物。</p>
                <button type="button" className="ht-btn soft" onClick={onOpenNewDeliverables}><TreasureVisual state={newDeliveryCount > 0 ? "ready" : "none"} size="mini" />打开新交付物</button>
              </div>
            </>
          )}
        </aside>
      </div>
    </>
  );
}
