import type { SessionInfo, SessionTodo, TodoSegmentSummary, TodoSummary } from "../api";
import type { Task } from "../types";

/**
 * 会话 + todo 汇总 → UI 的 Task 模型。
 *
 * 「任务」在后端没有独立实体: 一个会话就是一个任务, 进度来自它的 todo 汇总。这里是纯函数,
 * 数据获取在 useTasks —— PR 版把这套推导直接写在 App.tsx 的 render 里, 每次输入都重算。
 */

export function progressOf(summary: TodoSummary | undefined): { progress: number; indeterminate: boolean } {
  const total = summary?.total || 0;
  if (!total) return { progress: 0, indeterminate: false };
  const done = (summary?.completed || 0) + (summary?.cancelled || 0);
  return { progress: Math.round((done / total) * 100), indeterminate: false };
}

export function statusOf(summary: TodoSummary | undefined): string {
  const total = summary?.total || 0;
  if (!total) return "待开始";
  const done = (summary?.completed || 0) + (summary?.cancelled || 0);
  if (done >= total) return "已完成";
  if (summary?.in_progress) return "进行中";
  return "待处理";
}

/** ISO 时间戳 → 列表里显示的相对时间。 */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

function stepStateOf(status: string): "done" | "working" | "waiting" {
  if (status === "completed") return "done";
  if (status === "in_progress") return "working";
  return "waiting";
}

export interface TaskSource {
  session: SessionInfo;
  title: string;
  summary?: string;
  todos: TodoSummary | undefined;
  todoItems: SessionTodo[];
  segments: TodoSegmentSummary[];
  files: string[];
  newDeliverables: string[];
  /** 会话是否来自 IM(``from_im``)。 */
  fromIm: boolean;
  /** 会话是否**只读**(组织共享的调度会话)。 */
  readOnly: boolean;
  /** 用户是否置顶了这条(纯前端偏好, 见 ``services/pinnedTasks.ts``)。 */
  pinned: boolean;
}

/**
 * IM 共用会话(``from_im``)的固定显示名。
 *
 * 这条会话**就是**与飞书机器人对话本身(``feishu-<open_id>``), 身份是固定的, 不该跟着
 * 生成式标题走: 后端对它没有标题, 原先落到 "未命名任务", 在列表里看不出它是谁, 用户会
 * 以为那是个空任务。名字与产品名对齐(「海豚一号」), 也与「海豚二号」那套部署区分开。
 *
 * 刻意**不**允许被标题覆盖: 它是身份, 不是话题。真要改这个显示名, 改这一个常量 ——
 * 列表和顶栏都从这里取。
 */
export const IM_SESSION_TITLE = "海豚一号";

/**
 * 会话显示名 —— **唯一入口**。任务列表与对话顶栏都走这里, 免得两处各判一次 ``from_im``
 * 而其中一处漏掉: 那会让同一个会话在列表里叫一个名字、在顶栏叫另一个。
 */
export function displayTitle(
  session: Pick<SessionInfo, "from_im"> | undefined,
  title: string | undefined,
): string {
  if (session?.from_im) return IM_SESSION_TITLE;
  return title || "未命名任务";
}

export function buildTask(src: TaskSource): Task {
  const { progress, indeterminate } = progressOf(src.todos);
  const status = statusOf(src.todos);
  const latest = src.segments.at(-1);
  const activeItems = (src.todoItems || []).filter((todo) => todo.status !== "cancelled");
  const todoSteps = activeItems.map((todo) => ({
    t: todo.content,
    s: stepStateOf(todo.status),
    ...(todo.status === "in_progress" ? { detail: todo.content } : {}),
  }));
  const working = todoSteps.some((step) => step.s === "working");
  const idle = activeItems.length === 0 && src.files.length === 0;
  const steps = activeItems.length
    ? todoSteps
    : src.files.length
      ? [{ t: "本轮已完成", s: "done" as const }]
      : [{ t: "待继续", s: "waiting" as const, detail: "等待你的下一条" }];
  const phase = idle || activeItems.length ? ("advance" as const) : ("done" as const);
  const phaseLabel = idle
    ? "待继续"
    : activeItems.length
      ? (latest?.label || "推进中")
      : "本轮已完成";
  return {
    id: src.session.id,
    title: displayTitle(src.session, src.title),
    ...(src.summary ? { summary: src.summary } : {}),
    status,
    newDeliverables: src.newDeliverables,
    deliveryState: src.newDeliverables.length ? "ready" : src.files.length ? "saved" : "none",
    progress,
    indeterminate,
    ...(src.todos?.total ? { progressLabel: `${src.todos.completed}/${src.todos.total}` } : {}),
    hasTodoTrack: activeItems.length > 0 || src.segments.length > 0,
    sop: latest?.label || "自动流程",
    owner: "海豚",
    updated: relativeTime(latest?.updated_at) || (idle ? "待继续" : working ? "进行中" : activeItems.length ? "已同步" : "本轮回复已完成"),
    files: src.files,
    steps,
    phase,
    phaseLabel,
    fromIm: src.fromIm,
    readOnly: src.readOnly,
    pinned: src.pinned,
    // 上下文将满只对 IM 那条有意义: 网页新建的会话各有独立 jsonl, 不会替别人长。
    // 判据先用「历史消息条数」的替身 —— 后端目前不下发 token 用量, 故以 ``from_im``
    // 为唯一触发条件, 提示文案写成「这条会话与飞书对话共用, 会一直变长」。
    // 只读的组织会话不提示这句: 它根本发不出消息, 提示「开个新会话」也对不上场景。
    contextWarning: src.fromIm && !src.readOnly,
  };
}

export const TASK_FILTERS = ["all", "working", "attention", "done"] as const;
export type TaskFilter = (typeof TASK_FILTERS)[number];

export function filterTasks(tasks: Task[], filter: string, search: string): Task[] {
  const q = search.trim().toLowerCase();
  return tasks.filter((t) => {
    if (filter === "working" && t.status !== "进行中") return false;
    if (filter === "attention" && t.status !== "待处理") return false;
    if (filter === "done" && t.status !== "已完成") return false;
    if (!q) return true;
    return (
      t.title.toLowerCase().includes(q) ||
      (t.summary || "").toLowerCase().includes(q) ||
      (t.sop || "").toLowerCase().includes(q) ||
      (t.owner || "").toLowerCase().includes(q) ||
      (t.files || []).some((f) => f.toLowerCase().includes(q))
    );
  });
}

export function countTasks(tasks: Task[]): Record<string, number> {
  return {
    all: tasks.length,
    working: tasks.filter((t) => t.status === "进行中").length,
    attention: tasks.filter((t) => t.status === "待处理").length,
    done: tasks.filter((t) => t.status === "已完成").length,
  };
}

/**
 * 「本月执行」—— 本自然月内**跑过**的会话数。
 *
 * 定义: 某个会话只要有一段 todo 段是本月创建或更新的, 就算它本月执行过一次。按会话去重,
 * 所以同一会话本月跑十次仍只算一次 —— 这格是「有几天在干活」的规模感, 不是调用次数。
 *
 * 取数用的是 ``todo-segments`` 的时间戳, 这是前端唯一能拿到的「什么时候跑过」: history 行
 * 里没有时间字段, ``SessionInfo`` 也不下发。代价是**全程没写过 todo 的会话不计入** ——
 * 那种会话在列表里也永远停在「待开始」, 两处口径一致。
 *
 * 传 ``now`` 是为了可测: 纯函数里不藏 ``new Date()``。
 */
export function countMonthlyRuns(
  segmentsBySession: Record<string, TodoSegmentSummary[]>,
  now: Date = new Date(),
): number {
  const year = now.getFullYear();
  const month = now.getMonth();
  let count = 0;
  for (const list of Object.values(segmentsBySession)) {
    const hit = (list || []).some((segment) => {
      const raw = segment.updated_at || segment.created_at;
      if (!raw) return false;
      const at = new Date(raw);
      if (Number.isNaN(at.getTime())) return false;
      return at.getFullYear() === year && at.getMonth() === month;
    });
    if (hit) count += 1;
  }
  return count;
}
