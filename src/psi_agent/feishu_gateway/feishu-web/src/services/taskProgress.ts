import type { SessionTodo } from "../api";

export type TaskPhase = "advance" | "deliver" | "done";

export type TaskStep = {
  label: string;
  state: "done" | "working" | "waiting";
  detail?: string;
};

export type ProgressInput = {
  streaming: boolean;
  turnSettled: boolean;
  todos: SessionTodo[];
  hasDeliverables: boolean;
};

export type ProgressProjection = {
  phase: TaskPhase;
  steps: TaskStep[];
  progress: number;
  indeterminate: boolean;
  progressLabel: string;
  hasTodoTrack: boolean;
  updated: string;
  phaseLabel: string;
};

function activeTodos(todos: SessionTodo[]): SessionTodo[] {
  return todos.filter((t) => t.status !== "cancelled");
}

function todoMiddle(
  active: SessionTodo[],
): { label: string; detail?: string; done: boolean; completed: number; total: number } {
  const total = active.length;
  const completed = active.filter((t) => t.status === "completed").length;
  const inProgIdx = active.findIndex((t) => t.status === "in_progress");
  if (inProgIdx >= 0) {
    return {
      label: `${inProgIdx + 1}/${total}`,
      detail: active[inProgIdx]?.content,
      done: false,
      completed,
      total,
    };
  }
  if (completed >= total) {
    return { label: `${total}/${total}`, done: true, completed, total };
  }
  const nextIdx = active.findIndex((t) => t.status === "pending");
  const current = nextIdx >= 0 ? nextIdx + 1 : Math.min(completed + 1, total);
  return {
    label: `${current}/${total}`,
    detail: nextIdx >= 0 ? active[nextIdx]?.content : undefined,
    done: false,
    completed,
    total,
  };
}

function todoStatusToStepState(status: string): TaskStep["state"] {
  if (status === "completed") return "done";
  if (status === "in_progress") return "working";
  return "waiting";
}

function activityCopy(input: {
  phase: TaskPhase;
  streaming: boolean;
  hasDeliverables: boolean;
}): { label: string; detail?: string; state: TaskStep["state"]; updated: string } {
  if (input.phase === "done") {
    return { label: "本轮已完成", state: "done", updated: "本轮回复已完成" };
  }
  if (input.phase === "deliver") {
    return {
      label: "正在整理交付",
      detail: input.hasDeliverables ? "交付物生成中" : undefined,
      state: "working",
      updated: "正在产出",
    };
  }
  if (input.streaming) {
    return { label: "正在处理", state: "working", updated: "Agent 处理中" };
  }
  return { label: "待继续", detail: "等待你的下一条", state: "waiting", updated: "待继续" };
}

export function resolveTaskProgress(input: ProgressInput): ProgressProjection {
  const active = activeTodos(input.todos);
  const hasTodoTrack = active.length > 0;
  const middle = hasTodoTrack
    ? todoMiddle(active)
    : { label: "", detail: undefined, done: false, completed: 0, total: 0 };

  let phase: TaskPhase;
  if (input.streaming) {
    if (hasTodoTrack && middle.done) {
      phase = "deliver";
    } else if (!hasTodoTrack && input.hasDeliverables) {
      phase = "deliver";
    } else {
      phase = "advance";
    }
  } else if (input.turnSettled) {
    phase = "done";
  } else {
    phase = "advance";
  }

  let steps: TaskStep[];
  let progress: number;
  let indeterminate: boolean;
  let progressLabel: string;
  let phaseLabel: string;
  let updated: string;

  if (hasTodoTrack) {
    steps = active.map((t) => ({
      label: t.content,
      state: todoStatusToStepState(t.status),
    }));
    if (phase === "deliver") {
      steps = [...steps, { label: "产出与确认", state: "working" }];
    }

    progress = Math.round((middle.completed / Math.max(middle.total, 1)) * 100);
    if (phase === "deliver") progress = Math.max(progress, 85);
    indeterminate = false;
    progressLabel = middle.label;
    phaseLabel =
      phase === "done"
        ? middle.done
          ? "已完成"
          : `本轮已回复 · ${middle.label}`
        : phase === "deliver"
          ? "产出与确认"
          : middle.detail
            ? `${middle.label} · ${middle.detail}`
            : middle.label;
    updated =
      phase === "done"
        ? middle.done
          ? "本轮回复已完成"
          : `本轮已回复 · 清单 ${middle.label}`
        : phase === "deliver"
          ? "正在产出"
          : "已从 todo 同步进度";
  } else {
    const activity = activityCopy({
      phase,
      streaming: input.streaming,
      hasDeliverables: input.hasDeliverables,
    });
    steps = [{ label: activity.label, state: activity.state, detail: activity.detail }];
    progress = phase === "done" ? 100 : 0;
    indeterminate = phase === "advance" || phase === "deliver"
      ? input.streaming || phase === "deliver"
      : false;
    if (phase === "advance" && !input.streaming) {
      indeterminate = false;
    }
    progressLabel = "";
    phaseLabel = activity.label;
    updated = activity.updated;
  }

  return {
    phase,
    steps,
    progress: Number.isFinite(progress) ? progress : 0,
    indeterminate,
    progressLabel,
    hasTodoTrack,
    updated,
    phaseLabel,
  };
}
