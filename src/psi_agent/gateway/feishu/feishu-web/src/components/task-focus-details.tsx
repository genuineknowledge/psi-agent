import { CheckCircle2, FileText, ListTodo } from "lucide-react";
import type { TodoSegmentSummary } from "../api";
import type { Task } from "../types";
import { ProgressRing } from "./progress-ring";
import { statusPill } from "./brand";
import { TreasureVisual } from "./treasure";

/**
 * 任务详情侧栏: 进度 + 子任务(todo segment)时间线 + 交付物入口。
 *
 * 比 PR 版窄一圈, 有意的: PR 那 320 行里大部分分支读的是 ``task.phase`` / ``task.steps``,
 * 而这两个字段**没有任何数据源** —— 后端没有「阶段」概念, steps 永远是空数组, 那些分支
 * 全是死代码。这里只渲染真有数据的部分 (progress / todo 汇总 / segment 列表)。
 */
function formatSegmentTime(iso: string): string {
  const raw = (iso || "").trim();
  if (!raw) return "";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function segmentProgressLabel(summary: TodoSegmentSummary["summary"] | undefined): string {
  if (!summary || summary.total <= 0) return "";
  return `${summary.completed}/${summary.total}`;
}

export function TaskFocusDetails({
  task,
  todoSegments = [],
  selectedSegmentId = "live",
  onSelectTodoSegment,
  onOpenArtifact,
}: {
  task: Task | null;
  todoSegments?: TodoSegmentSummary[];
  selectedSegmentId?: string;
  onSelectTodoSegment?: (segmentId: string) => void;
  onOpenArtifact: (task: Task, fileName?: string) => void;
}) {
  if (!task) {
    return <aside className="focus-details"><div className="focus-details-empty">选择一个任务查看详情</div></aside>;
  }

  return (
    <aside className="focus-details">
      <section className="focus-details-head">
        <ProgressRing
          value={task.progress}
          continuous={task.indeterminate || false}
          size="lg"
          label={task.hasTodoTrack ? task.progressLabel : undefined}
        />
        <div className="focus-details-headtext">
          <h3>{task.title}</h3>
          <div className="focus-details-meta">
            {statusPill(task.status)}
            {task.updated ? <em>{task.updated}</em> : null}
          </div>
        </div>
      </section>

      {task.summary ? <p className="focus-details-summary">{task.summary}</p> : null}

      <section className="focus-details-block">
        <h4><ListTodo size={14} /> 子任务</h4>
        {todoSegments.length === 0 ? (
          <div className="focus-details-empty">这个任务还没有子任务清单</div>
        ) : (
          <div className="focus-segment-list">
            {todoSegments.map((seg) => {
              const isOpen = !seg.closed_at;
              const id = isOpen ? "live" : seg.id;
              const label = segmentProgressLabel(seg.summary);
              const complete = seg.summary && seg.summary.total > 0 && seg.summary.completed >= seg.summary.total;
              return (
                <button
                  key={seg.id}
                  type="button"
                  className={`focus-segment-row${selectedSegmentId === id ? " is-active" : ""}`}
                  onClick={() => onSelectTodoSegment?.(id)}
                >
                  {complete ? <CheckCircle2 size={15} /> : <ListTodo size={15} />}
                  <div>
                    <strong>{seg.label || "子任务"}</strong>
                    <em>
                      {[isOpen ? "当前" : "已归档", label ? `清单 ${label}` : ""].filter(Boolean).join(" · ")}
                    </em>
                  </div>
                  <span className="focus-segment-time">{formatSegmentTime(seg.updated_at || seg.created_at)}</span>
                </button>
              );
            })}
          </div>
        )}
      </section>

      <section className="focus-details-block">
        <h4><TreasureVisual state={task.deliveryState} size="mini" /> 交付物</h4>
        {task.files.length === 0 ? (
          <div className="focus-details-empty">暂无交付物</div>
        ) : (
          <div className="focus-delivery-list">
            {task.files.map((f) => (
              <button key={f} type="button" className="focus-delivery-row" onClick={() => onOpenArtifact(task, f)}>
                <FileText size={15} />
                <span>{f}</span>
              </button>
            ))}
          </div>
        )}
      </section>
    </aside>
  );
}
