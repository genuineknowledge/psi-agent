import type { SessionInfo, SessionTodo, TodoSegmentSummary, TodoSummary } from "../api";
import type { Task } from "../types";

/**
 * 会话 + todo 汇总 → UI 的 Task 模型。
 *
 * 「任务」在后端没有独立实体: 一个会话就是一个任务, 进度来自它的 todo 汇总。这里是纯函数,
 * 数据获取在 useTasks —— PR 版把这套推导直接写在 App.tsx 的 render 里, 每次输入都重算。
 *
 * ## 状态与进度是**两个输入**的函数, 不只是 todo
 *
 * 只读 todo 会漏掉一整类会话: 跑完一轮却没写过 todo 的任务(agent 直接回答/直接调工具)。
 * 它们的 `summary.total` 恒为 0, 于是「刚回完一轮」和「从没动过」在界面上长得一模一样 ——
 * 都显示「待开始 / 0%」。实测有人因此认为左侧任务上下文完全没用。
 *
 * 所以这里照 C 端(spa-v2 的 `services/taskProgress.ts`)的同一套语义, 补两个前端信号:
 *
 * * `streaming`: 这一回合的 SSE 还在流(前端自己发的那次);
 * * `turnSettled`: 至少有一轮回复落定过(本浏览器里刚跑完, 或历史里已经有助手回复)。
 *
 * 无 todo 轨道时**不编造百分比**: 运行中给不确定态(转圈), 落定后才是 100%。
 */

export interface ProgressContext {
  /** 这一回合正在进行(SSE 还在流)。 */
  streaming: boolean;
  /** 至少有一轮回复落定过。 */
  turnSettled: boolean;
  /** 这个会话有没有交付物(决定「正在整理交付」还是「正在处理」)。 */
  hasDeliverables: boolean;
  /** 有没有活跃的 todo 清单(决定走清单轨道还是单行活动态)。 */
  hasTodoTrack: boolean;
}

export function progressOf(
  summary: TodoSummary | undefined,
  ctx: Pick<ProgressContext, "streaming" | "turnSettled">,
): { progress: number; indeterminate: boolean } {
  const total = summary?.total || 0;
  if (!total) {
    // 没有清单轨道: 不给假的百分比。运行中转圈; 落定了就是 100%。
    return { progress: ctx.turnSettled ? 100 : 0, indeterminate: ctx.streaming };
  }
  const done = (summary?.completed || 0) + (summary?.cancelled || 0);
  return { progress: Math.round((done / total) * 100), indeterminate: false };
}

/**
 * 列表里的状态文案。取值与 C 端一致(进行中 / 待您处理 / 已完成), 另外加一个**运行中** ——
 * 它是用户唯一能在列表上看出「这条正在干活」的信号, 而「进行中」与「待处理」是 todo 的状态,
 * 说不了这件事。
 */
export function statusOf(
  summary: TodoSummary | undefined,
  ctx: Pick<ProgressContext, "streaming" | "turnSettled">,
): string {
  if (ctx.streaming) return "运行中";
  const total = summary?.total || 0;
  if (!total) return ctx.turnSettled ? "已完成" : "待开始";
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
  /** 这一回合的 SSE 还在流(前端自己发的那次)。 */
  streaming: boolean;
  /** 至少有一轮回复落定过(本浏览器里刚跑完, 或历史里已有助手回复)。 */
  turnSettled: boolean;
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
  const activeItems = (src.todoItems || []).filter((todo) => todo.status !== "cancelled");
  const hasTodoTrack = activeItems.length > 0;
  const ctx: ProgressContext = {
    streaming: src.streaming,
    turnSettled: src.turnSettled,
    hasDeliverables: src.files.length > 0,
    hasTodoTrack,
  };
  const { progress, indeterminate } = progressOf(src.todos, ctx);
  const status = statusOf(src.todos, ctx);
  const latest = src.segments.at(-1);
  const todoSteps = activeItems.map((todo) => ({
    t: todo.content,
    s: stepStateOf(todo.status),
    ...(todo.status === "in_progress" ? { detail: todo.content } : {}),
  }));
  const checklistDone = todoSteps.length > 0 && todoSteps.every((step) => step.s === "done");
  /*
   * 生命周期阶段 —— 与 C 端 `resolveTaskProgress` 同一套:
   *   流式中 + 清单做完 / 无清单但有交付物 → deliver; 流式中 → advance; 落定 → done。
   * `done` 由「有没有落定过一轮」决定, **不再由「有没有交付物」决定** —— 后者会把
   * 「回完一轮但没产生文件」的会话永远留在「待继续」。
   */
  const phase: "advance" | "deliver" | "done" = src.streaming
    ? hasTodoTrack && checklistDone
      ? "deliver"
      : !hasTodoTrack && ctx.hasDeliverables
        ? "deliver"
        : "advance"
    : src.turnSettled
      ? "done"
      : "advance";
  /*
   * 无清单时的步骤就是**一行活动态**(C 端同款), 不编造三步轨道: 有清单时显示真实清单,
   * 没清单时显示「正在处理 / 正在整理交付 / 本轮已完成 / 待继续」。
   */
  const activity: { t: string; s: "done" | "working" | "waiting"; detail?: string } =
    phase === "done"
      ? { t: "本轮已完成", s: "done" }
      : phase === "deliver"
        ? { t: "正在整理交付", s: "working", ...(ctx.hasDeliverables ? { detail: "交付物生成中" } : {}) }
        : src.streaming
          ? { t: "正在处理", s: "working" }
          : { t: "待继续", s: "waiting", detail: "等待你的下一条" };
  const steps = hasTodoTrack
    ? phase === "deliver"
      ? [...todoSteps, { t: "正在整理交付", s: "working" as const }]
      : todoSteps
    : [activity];
  const phaseLabel = hasTodoTrack
    ? phase === "deliver"
      ? "正在整理交付"
      : (latest?.label || "推进中")
    : activity.t;
  const updated =
    phase === "done"
      ? "本轮回复已完成"
      : phase === "deliver"
        ? "正在产出"
        : src.streaming
          ? "Agent 处理中"
          : relativeTime(latest?.updated_at) || "待继续";
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
    hasTodoTrack: hasTodoTrack || src.segments.length > 0,
    sop: latest?.label || "自动流程",
    owner: "海豚",
    updated,
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
    // 「进行中」这一档把**运行中**也算进去 —— 用户按「进行中」是想看"还在动的那些",
    // 而在跑的那条恰恰是最该出现的; 单独给它一个筛选项反而多一次点击。
    if (filter === "working" && t.status !== "进行中" && t.status !== "运行中") return false;
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
    // 与 filterTasks 的 working 同一口径: 运行中 + 进行中。
    working: tasks.filter((t) => t.status === "进行中" || t.status === "运行中").length,
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
