import {
  AlertCircle,
  ArrowRight,
  Check,
  Trash2,
} from "lucide-react";
import type { CSSProperties } from "react";
import { DELIVERY_LABEL, OVERVIEW_LABEL, PENDING_LABEL, type Task } from "./model";
import { ProgressRing, TreasureButton, TreasureVisual } from "./primitives";

export function TaskRow({
  task,
  active,
  onSelect,
  onOpenArtifact,
  onDelete,
}: {
  task: Task;
  active: boolean;
  onSelect: () => void;
  onOpenArtifact: (task: Task) => void;
  onDelete?: (task: Task) => void;
}) {
  return (
    <div className={`task-row ${active ? "active" : ""}`}>
      <button type="button" className="task-row-select" onClick={onSelect} aria-label={`打开任务：${task.title}`}>
        <span className="task-row-main">
          <span className="task-row-progress-line">
            <ProgressRing value={task.progress} continuous={task.status === "continuous"} size="sm" />
            {task.status === "attention" ? (
              <span className="mini-alert" title="需要处理">
                <AlertCircle size={13} />
              </span>
            ) : null}
          </span>
          <strong>{task.title}</strong>
        </span>
      </button>
      <div className="task-row-actions">
        {onDelete && (
          <button
            type="button"
            className="task-row-delete"
            title="删除任务"
            aria-label={`删除任务：${task.title}`}
            onClick={(event) => {
              event.stopPropagation();
              onDelete(task);
            }}
          >
            <Trash2 size={14} />
          </button>
        )}
        <TreasureButton task={task} onOpen={onOpenArtifact} compact />
      </div>
    </div>
  );
}

export function OverviewCard({ tasks, onHandleNext }: { tasks: Task[]; onHandleNext: () => void }) {
  const finiteTasks = tasks.filter((task) => task.status !== "continuous");
  const overall = Math.round(finiteTasks.reduce((sum, task) => sum + task.progress, 0) / Math.max(finiteTasks.length, 1));
  const working = tasks.filter((task) => ["working", "continuous"].includes(task.status)).length;
  const attention = tasks.filter((task) => task.status === "attention").length;
  const completed = tasks.filter((task) => task.status === "completed").length;
  const newDeliveries = tasks.filter((task) => task.deliveryState === "ready").length;

  return (
    <article className="focus-card overview-card">
      <div className="card-orbit orbit-one" />
      <div className="card-orbit orbit-two" />
      <div className="overall-dial" style={{ "--progress": `${overall * 3.6}deg` } as CSSProperties} aria-label={`综合进度 ${overall}%`}>
        <div>
          <strong>{overall}%</strong>
          <span>综合进度</span>
        </div>
      </div>
      <header className="card-header">
        <span className="eyebrow">{OVERVIEW_LABEL} · 7 月 17 日</span>
      </header>

      <div className="overview-hero">
        <div>
          <span className="live-label"><span /> Agent 工作台</span>
          <h1>今天，已为您完成 {completed} 件事</h1>
          <p>同时推进 {working + attention} 件任务，并整理出 {newDeliveries} 组新交付物。</p>
        </div>
      </div>

      <div className="overview-metrics">
        <div className="metric-cell">
          <span className="metric-icon working"><ProgressRing value={overall} size="sm" showValue={false} /></span>
          <div><strong>{working}</strong><span>运行中</span></div>
        </div>
        <div className="metric-cell attention">
          <span className="metric-icon"><AlertCircle size={16} /></span>
          <div><strong>{attention}</strong><span>{PENDING_LABEL}</span></div>
        </div>
        <div className="metric-cell delivery">
          <span className="metric-icon treasure-metric"><TreasureVisual state="ready" size="mini" /></span>
          <div><strong>{newDeliveries}</strong><span>{DELIVERY_LABEL}</span></div>
        </div>
      </div>

      <div className="overview-bottom">
        <div>
          <span className="section-label">下一步</span>
          <strong>确认首批灰度体验名单</strong>
          <span>预计占用您 2 分钟</span>
        </div>
        <button type="button" className="primary-inline" onClick={onHandleNext}>
          去处理 <ArrowRight size={16} />
        </button>
      </div>
    </article>
  );
}

export function TaskCard({
  task,
  onOpenArtifact,
  onDelete,
}: {
  task: Task;
  onOpenArtifact: (task: Task) => void;
  onDelete?: (task: Task) => void;
}) {
  return (
    <article className="focus-card task-card" style={{ "--task-accent": task.accent } as CSSProperties}>
      <div className="task-accent-line" />
      <div
        className={`task-corner-progress ${task.status === "continuous" ? "continuous" : ""}`}
        style={{ "--progress": `${task.progress * 3.6}deg` } as CSSProperties}
        aria-label={`${task.status === "continuous" ? "本轮巡检进度" : "任务进度"} ${task.progress}%`}
      >
        <div>
          <strong>{task.progress}%</strong>
          <span>{task.status === "continuous" ? "巡检" : "进度"}</span>
        </div>
      </div>

      <div className="task-title-block">
        <div className="task-title-row">
          <h1>{task.title}</h1>
          {onDelete && (
            <button
              type="button"
              className="task-card-delete"
              title="删除任务"
              aria-label={`删除任务：${task.title}`}
              onClick={() => onDelete(task)}
            >
              <Trash2 size={16} />
            </button>
          )}
        </div>
        <p>{task.summary}</p>
      </div>

      <div className="task-steps">
        {task.steps.map((step) => (
          <div className={`task-step ${step.state}`} key={step.label}>
            <span className="step-marker">
              {step.state === "done" ? <Check size={16} /> : step.state === "working" ? <span /> : null}
            </span>
            <span className="task-step-label">
              {step.label}
              {step.state === "working" && <em>进行中</em>}
            </span>
          </div>
        ))}
      </div>

      <footer className="task-card-footer">
        <div className="task-delivery-slot">
          <span className="task-delivery-label">交付物</span>
          <TreasureButton task={task} onOpen={onOpenArtifact} />
        </div>
      </footer>
    </article>
  );
}

export function CompactTaskContext({
  task,
  onOpenArtifact,
  onDelete,
}: {
  task: Task;
  onOpenArtifact: (task: Task) => void;
  onDelete?: (task: Task) => void;
}) {
  return (
    <div className="compact-card-shell">
      <TaskCard task={task} onOpenArtifact={onOpenArtifact} onDelete={onDelete} />
    </div>
  );
}

export function CompactOverviewContext({
  tasks,
  onHandleNext,
}: {
  tasks: Task[];
  onHandleNext: () => void;
}) {
  return (
    <div className="compact-card-shell">
      <OverviewCard tasks={tasks} onHandleNext={onHandleNext} />
    </div>
  );
}
