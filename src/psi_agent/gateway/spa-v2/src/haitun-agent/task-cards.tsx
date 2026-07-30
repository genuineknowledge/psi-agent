import {
  AlertCircle,
  Check,
  ChevronLeft,
  ChevronRight,
  Trash2,
} from "lucide-react";
import { type CSSProperties, useEffect, useState, type MouseEvent } from "react";
import { plainTextFromMarkdown } from "../services/assistantDisplay";
import { DELIVERY_LABEL, OVERVIEW_LABEL, PENDING_LABEL, type Task, type TaskStep } from "./model";
import { ProgressRing, TreasureButton, TreasureVisual } from "./primitives";
import { WORKING_LABEL, filterTasksBySignal, type TaskSignalKind } from "./taskSignals";

/** 3 columns × 2 rows — middle step viewport stays fixed; overflow pages. */
const STEPS_COLS = 3;
const STEPS_ROWS = 2;
const STEPS_PER_PAGE = STEPS_COLS * STEPS_ROWS;

/** Local calendar day as ``M 月 D 日`` (zh-CN overview eyebrow). */
export function formatOverviewDay(date: Date = new Date()): string {
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
}

/** Re-render when the local calendar day changes (checks every minute + at midnight). */
function useLiveOverviewDay(): string {
  const [label, setLabel] = useState(() => formatOverviewDay());

  useEffect(() => {
    const sync = () => setLabel(formatOverviewDay());
    sync();

    const minuteId = window.setInterval(sync, 60_000);
    const now = new Date();
    const nextMidnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 1);
    const midnightId = window.setTimeout(() => {
      sync();
    }, Math.max(1000, nextMidnight.getTime() - now.getTime()));

    const onVisible = () => {
      if (document.visibilityState === "visible") sync();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      window.clearInterval(minuteId);
      window.clearTimeout(midnightId);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return label;
}

export function TaskRow({
  task,
  active,
  onSelect,
  onPrefetch,
  onOpenArtifact,
  onDelete,
}: {
  task: Task;
  active: boolean;
  onSelect: () => void;
  onPrefetch?: () => void;
  onOpenArtifact: (task: Task, fileName?: string) => void;
  onDelete?: (task: Task) => void;
}) {
  return (
    <div
      className={`task-row ${active ? "active" : ""}`}
      onPointerEnter={onPrefetch}
    >
      <button type="button" className="task-row-select" onClick={onSelect} aria-label={`打开任务：${task.title}`}>
        <span className="task-row-main">
          <span className="task-row-progress-line">
            <ProgressRing
              value={task.progress}
              continuous={task.status === "continuous" || !!task.progressIndeterminate}
              size="sm"
              showValue={task.hasTodoTrack || task.phase === "done"}
              label={task.hasTodoTrack ? task.progressLabel : undefined}
            />
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

export function OverviewCard({
  tasks,
  onOpenChat,
  onOpenSignal,
}: {
  tasks: Task[];
  onOpenChat?: () => void;
  /** Same inbox API as sidebar topline signals (working / pending / deliveries). */
  onOpenSignal?: (kind: TaskSignalKind) => void;
}) {
  const dayLabel = useLiveOverviewDay();
  const finiteTasks = tasks.filter((task) => task.status !== "continuous");
  const tracked = finiteTasks.filter((task) => task.hasTodoTrack);
  const overall = tracked.length
    ? Math.round(tracked.reduce((sum, task) => sum + task.progress, 0) / tracked.length)
    : Math.round((finiteTasks.filter((task) => task.phase === "done" || task.status === "completed").length / Math.max(finiteTasks.length, 1)) * 100);
  const working = filterTasksBySignal(tasks, "working").length;
  const attention = filterTasksBySignal(tasks, "pending").length;
  const completed = tasks.filter((task) => task.status === "completed").length;
  const newDeliveries = filterTasksBySignal(tasks, "deliveries").length;

  const openSignal = (kind: TaskSignalKind) => (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    onOpenSignal?.(kind);
  };

  return (
    <article
      className={`focus-card overview-card${onOpenChat ? " card-open-chat" : ""}`}
      onClick={onOpenChat}
      onKeyDown={onOpenChat ? (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpenChat();
        }
      } : undefined}
      role={onOpenChat ? "button" : undefined}
      tabIndex={onOpenChat ? 0 : undefined}
      aria-label={onOpenChat ? "打开任务总览对话" : undefined}
    >
      <div className="card-orbit orbit-one" />
      <div className="card-orbit orbit-two" />
      <div className="overall-dial" style={{ "--progress": `${overall * 3.6}deg` } as CSSProperties} aria-label={`综合进度 ${overall}%`}>
        <div>
          <strong>{overall}%</strong>
          <span>综合进度</span>
        </div>
      </div>
      <header className="card-header">
        <span className="eyebrow">{OVERVIEW_LABEL} · {dayLabel}</span>
      </header>

      <div className="overview-hero">
        <div>
          <span className="live-label"><span /> Agent 工作台</span>
          <h1>今天，已为您完成 {completed} 件事</h1>
          <p>同时推进 {working + attention} 件任务，并整理出 {newDeliveries} 组新交付物。</p>
        </div>
      </div>

      <div className="overview-metrics" role="group" aria-label="任务信号">
        <button
          type="button"
          className="metric-cell"
          data-card-interactive
          disabled={!onOpenSignal}
          onClick={openSignal("working")}
          aria-label={`${WORKING_LABEL} ${working}`}
        >
          <span className="metric-icon working"><ProgressRing value={overall} size="sm" showValue={false} /></span>
          <div><strong>{working}</strong><span>{WORKING_LABEL}</span></div>
        </button>
        <button
          type="button"
          className="metric-cell attention"
          data-card-interactive
          disabled={!onOpenSignal}
          onClick={openSignal("pending")}
          aria-label={`${PENDING_LABEL} ${attention}`}
        >
          <span className="metric-icon"><AlertCircle size={16} /></span>
          <div><strong>{attention}</strong><span>{PENDING_LABEL}</span></div>
        </button>
        <button
          type="button"
          className="metric-cell delivery"
          data-card-interactive
          disabled={!onOpenSignal}
          onClick={openSignal("deliveries")}
          aria-label={`${DELIVERY_LABEL} ${newDeliveries}`}
        >
          <span className="metric-icon treasure-metric"><TreasureVisual state="ready" size="mini" /></span>
          <div><strong>{newDeliveries}</strong><span>{DELIVERY_LABEL}</span></div>
        </button>
      </div>
    </article>
  );
}

function StepChip({ step, showBusyHint }: { step: TaskStep; showBusyHint: boolean }) {
  return (
    <div className={`task-step ${step.state}`}>
      <span className="step-marker">
        {step.state === "done" ? <Check size={16} /> : step.state === "working" ? <span /> : null}
      </span>
      <span className="task-step-label">
        {step.label}
        {step.state === "working" && <em>{step.detail?.trim() || (showBusyHint ? "进行中" : "")}</em>}
        {step.state === "waiting" && step.detail?.trim() ? <em>{step.detail.trim()}</em> : null}
        {step.state === "done" && step.detail?.trim() ? <em>{step.detail.trim()}</em> : null}
      </span>
    </div>
  );
}

function TaskStepsPanel({ task }: { task: Task }) {
  const steps = task.steps;
  const pageCount = Math.max(1, Math.ceil(steps.length / STEPS_PER_PAGE));
  const [page, setPage] = useState(0);

  useEffect(() => {
    setPage(0);
  }, [task.id]);

  useEffect(() => {
    setPage((p) => Math.min(p, pageCount - 1));
  }, [pageCount]);

  const safePage = Math.min(page, pageCount - 1);
  const start = safePage * STEPS_PER_PAGE;
  const visible = steps.slice(start, start + STEPS_PER_PAGE);
  const showPager = steps.length > STEPS_PER_PAGE;
  const isActivity = !task.hasTodoTrack;

  return (
    <div className={`task-steps-panel ${isActivity ? "is-activity" : ""}`}>
      <div className="task-steps-toolbar">
        <span className="task-steps-caption">
          {isActivity ? "活动状态" : "执行步骤"}
          {!isActivity && task.progressLabel ? <em>{task.progressLabel}</em> : null}
        </span>
        {showPager ? (
          <div className="task-steps-pager" role="group" aria-label="步骤翻页">
            <button
              type="button"
              className="task-steps-page-btn"
              disabled={safePage <= 0}
              aria-label="上一页步骤"
              data-card-interactive=""
              onClick={(event) => {
                event.stopPropagation();
                setPage((p) => Math.max(0, p - 1));
              }}
            >
              <ChevronLeft size={16} />
            </button>
            <span aria-live="polite">{safePage + 1}/{pageCount}</span>
            <button
              type="button"
              className="task-steps-page-btn"
              disabled={safePage >= pageCount - 1}
              aria-label="下一页步骤"
              data-card-interactive=""
              onClick={(event) => {
                event.stopPropagation();
                setPage((p) => Math.min(pageCount - 1, p + 1));
              }}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        ) : null}
      </div>
      <div className="task-steps-viewport">
        <div className={`task-steps ${isActivity ? "task-steps-activity" : ""}`}>
          {visible.map((step, index) => (
            <StepChip
              key={`${safePage}-${start + index}-${step.label}`}
              step={step}
              showBusyHint={!!task.hasTodoTrack}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function TaskLinearProgress({ task }: { task: Task }) {
  const busy = !!task.progressIndeterminate;
  // CSS `.done` forces bar width 100% — only apply when checklist (or no-todo turn) is truly complete.
  const barComplete = task.hasTodoTrack
    ? !!task.steps.length && task.steps.every((step) => step.state === "done")
    : task.phase === "done";
  const label = task.hasTodoTrack
    ? "步骤进度"
    : busy
      ? "处理中"
      : task.phase === "done"
        ? "本轮进度"
        : "活动";
  const valueText = task.hasTodoTrack
    ? (task.progressLabel || `${task.progress}%`)
    : busy
      ? "…"
      : task.phase === "done"
        ? "完成"
        : "待续";
  const width = busy && !task.hasTodoTrack ? 42 : Math.max(0, Math.min(100, task.progress));

  return (
    <div
      className={`task-linear-progress ${busy ? "indeterminate" : ""} ${barComplete ? "done" : ""}`}
      aria-label={`${label} ${valueText}`}
    >
      <div className="task-linear-progress-meta">
        <span>{label}</span>
        <strong>{valueText}</strong>
      </div>
      <div className="task-linear-track">
        <span style={busy && !task.hasTodoTrack ? undefined : { width: `${width}%` }} />
      </div>
    </div>
  );
}

export function TaskCard({
  task,
  onOpenArtifact,
  onDelete,
  onOpenChat,
}: {
  task: Task;
  onOpenArtifact: (task: Task, fileName?: string) => void;
  onDelete?: (task: Task) => void;
  /** Overview swipe surface: open split chat (same as clicking the dialogue strip). */
  onOpenChat?: () => void;
}) {
  return (
    <article
      className={`focus-card task-card${onOpenChat ? " card-open-chat" : ""}`}
      style={{ "--task-accent": task.accent } as CSSProperties}
      onClick={onOpenChat}
      onKeyDown={onOpenChat ? (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpenChat();
        }
      } : undefined}
      role={onOpenChat ? "button" : undefined}
      tabIndex={onOpenChat ? 0 : undefined}
      aria-label={onOpenChat ? `打开任务对话：${task.title}` : undefined}
    >
      <div className="task-accent-line" />

      <div
        className="task-corner-treasure"
        data-card-interactive=""
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
      >
        <span className="task-corner-treasure-label">交付物</span>
        <TreasureButton task={task} onOpen={onOpenArtifact} />
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
              data-card-interactive=""
              onClick={(event) => {
                event.stopPropagation();
                onDelete(task);
              }}
            >
              <Trash2 size={16} />
            </button>
          )}
        </div>
        <p>{plainTextFromMarkdown(task.summary)}</p>
      </div>

      <TaskStepsPanel task={task} />

      <footer className="task-card-footer">
        <TaskLinearProgress task={task} />
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
  onOpenArtifact: (task: Task, fileName?: string) => void;
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
  onOpenSignal,
}: {
  tasks: Task[];
  onOpenSignal?: (kind: TaskSignalKind) => void;
}) {
  return (
    <div className="compact-card-shell">
      <OverviewCard tasks={tasks} onOpenSignal={onOpenSignal} />
    </div>
  );
}
