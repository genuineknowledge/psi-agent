import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getMonthlyStats,
  getSessionTodos,
  listSummaries,
  listTodoSegments,
  type MonthlyStats,
  type SessionTodo,
  type SessionInfo,
  type TodoSegmentSummary,
  type TodoSummary,
} from "../api";
import type { Task } from "../types";
import { pendingDeliveriesFor, subscribePendingDeliveries } from "../services/pendingDeliveries";
import { sortTasksByPin } from "../services/pinnedTasks";
import { buildTask, countTasks, filterTasks } from "../services/taskModel";

/**
 * 任务总览的数据。每个会话要单独打 ``/todos`` 与 ``/todo-segments``, 所以并发拉取后
 * 按 id 归并 —— 串行会随会话数线性变慢。
 *
 * ``pinnedIds`` 是纯前端偏好(见 ``services/pinnedTasks.ts``): 传进来只做两件事 ——
 * 排在最前、打 ``pinned`` 标记。它**不进后端**, 也不参与过滤计数。
 *
 * ``live`` 是回合的实时信号(来自 ``useChatTurn``):
 *
 * * ``sendingSessionId``: 哪条会话正在跑 → 那条的 todo/子任务**每 2.5 秒重拉一次**。
 *   没有这一步, 左侧任务上下文就只在挂载与回合结束时更新 —— 表现是「执行过程中一直
 *   待继续/0%, 任务做完了才跳成已完成」(实测反馈)。C 端是同一套做法, 间隔也一样。
 * * ``settledBySession``: 哪条会话跑完过一轮 → 无 todo 的会话据此显示「已完成」而不是
 *   「待开始」(见 ``taskModel.statusOf``)。
 */
export function useTasks(
  sessions: SessionInfo[],
  titles: Record<string, string>,
  deliverables: Record<string, { files: string[]; paths: Record<string, string>; replied?: boolean }> = {},
  pinnedIds: string[] = [],
  live: { sendingSessionId?: string; settledBySession?: Record<string, boolean> } = {},
) {
  const [todos, setTodos] = useState<Record<string, TodoSummary>>({});
  const [todoItems, setTodoItems] = useState<Record<string, SessionTodo[]>>({});
  const [segments, setSegments] = useState<Record<string, TodoSegmentSummary[]>>({});
  const [summaries, setSummaries] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [pendingRevision, setPendingRevision] = useState(0);
  const sendingSessionId = live.sendingSessionId || "";

  useEffect(() => subscribePendingDeliveries(() => setPendingRevision((n) => n + 1)), []);

  const refresh = useCallback(async () => {
    if (!sessions.length) {
      setTodos({});
      setTodoItems({});
      setSegments({});
      return;
    }
    const results = await Promise.all(
      sessions.map(async (s) => {
        const [todoResp, segs] = await Promise.all([
          getSessionTodos(s.id).catch(() => null),
          listTodoSegments(s.id).catch(() => [] as TodoSegmentSummary[]),
        ]);
        return { id: s.id, todo: todoResp?.summary, items: todoResp?.todos ?? [], segs };
      }),
    );
    const nextTodos: Record<string, TodoSummary> = {};
    const nextItems: Record<string, SessionTodo[]> = {};
    const nextSegs: Record<string, TodoSegmentSummary[]> = {};
    for (const r of results) {
      if (r.todo) nextTodos[r.id] = r.todo;
      nextItems[r.id] = r.items;
      nextSegs[r.id] = r.segs;
    }
    setTodos(nextTodos);
    setTodoItems(nextItems);
    setSegments(nextSegs);
  }, [sessions]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /**
   * 只重拉**一条**会话的 todo/子任务 —— 回合进行中的轮询用它。
   *
   * 不复用上面的 ``refresh``: 那个会把所有会话全打一遍(会话多时每 2.5 秒几十个请求),
   * 而回合进行中真正会变的只有当前这条。
   */
  const refreshOne = useCallback(async (sessionId: string) => {
    if (!sessionId) return;
    const [todoResp, segs] = await Promise.all([
      getSessionTodos(sessionId).catch(() => null),
      listTodoSegments(sessionId).catch(() => [] as TodoSegmentSummary[]),
    ]);
    if (todoResp?.summary) {
      setTodos((prev) => ({ ...prev, [sessionId]: todoResp.summary }));
    }
    setTodoItems((prev) => ({ ...prev, [sessionId]: todoResp?.todos ?? [] }));
    setSegments((prev) => ({ ...prev, [sessionId]: segs }));
  }, []);

  /**
   * 本月执行: 由后端**一次**算好(``GET /feishu/stats/monthly``), 不再对每个会话各打一次
   * ``/todo-segments`` 自己数 —— 那条老路只看得见 todo 段, 于是「直接回答、不写清单」的会话
   * 会被算成没干活, 而列表里它们的状态早就显示「已完成」了。
   *
   * 声明在轮询 effect **之前**: 那个 effect 每 2.5 秒也要刷这一格(本回合要是这个会话本月第一次
   * 干活, 数字该当场 +1), 而 ``const`` 提升不了。
   */
  const [monthlyStats, setMonthlyStats] = useState<MonthlyStats | null>(null);
  const refreshMonthly = useCallback(async () => {
    try {
      // 传**浏览器本地月**: 「月」是用户日历上的月, 服务端时区未必与用户一致。
      const now = new Date();
      const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
      setMonthlyStats(await getMonthlyStats(month));
    } catch {
      // 拿不到就保持上一次的数字 —— 显示 0 会被读成「本月什么都没跑」。
    }
  }, []);

  useEffect(() => {
    void refreshMonthly();
  }, [refreshMonthly]);

  /*
   * 回合进行中: 每 2.5 秒重拉当前会话的 todo 与子任务, 并**立刻**拉一次。
   *
   * 立刻那一次不能省: 用户按下发送到第一次轮询之间有两秒多, 而「正在处理」这一步是靠
   * 前端信号给出的(见 taskModel), todo 只是随后补上 —— 但工具的第一次写入往往就在那一刻
   * 发生。间隔取 2.5s: 与 C 端 `HaiTuanAgentWorkspace` 的 todo 轮询一致, 再密就是白打请求。
   */
  useEffect(() => {
    if (!sendingSessionId) return;
    void refreshOne(sendingSessionId);
    // 「本月执行」跟着一起刷: 本回合要是这个会话本月第一次干活, 数字该当场 +1(一次请求, 便宜)。
    void refreshMonthly();
    const timer = window.setInterval(() => {
      void refreshOne(sendingSessionId);
      void refreshMonthly();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [sendingSessionId, refreshOne, refreshMonthly]);

  useEffect(() => {
    void listSummaries()
      .then(setSummaries)
      .catch(() => setSummaries({}));
  }, []);

  const tasks = useMemo<Task[]>(() => {
    const pinnedSet = new Set(pinnedIds);
    const built = sessions.map((session) => {
      // 本轮流式里刚收到、还没写进历史的交付物。
      const newDeliverables = pendingDeliveriesFor(session.id);
      /*
       * 交付物 = 历史恢复的附件（``[SEND:]`` 解析路径）+ 上面那几个。
       *
       * 两处**合成一份** ``files``: 只给历史那一份的话, 刚交付完的会话在左侧「历史交付物」
       * 一栏会显示「0 份」, 而右侧交付物抽屉取的是两者并集 —— 同一条会话两处口径不一致,
       * 用户会以为文件没生成。
       */
      const files = [...new Set([...(deliverables[session.id]?.files ?? []), ...newDeliverables])];
      /*
       * 「跑完过一轮」有两个来源, 取或:
       *   * 本浏览器里刚跑完(``settledBySession``) —— 刷新页面就没了;
       *   * 历史里已经有助手回复(``deliverables[..].replied``) —— 持久, 重开也在。
       * 两个都要: 只靠前者会让「昨天跑完的任务」重开后回到「待开始」, 只靠后者则刚发出去
       * 还没写进历史的这一轮判不出来。
       */
      const settled = live.settledBySession?.[session.id] === true || deliverables[session.id]?.replied === true;
      return buildTask({
        session,
        title: titles[session.id] || "",
        summary: summaries[session.id],
        todos: todos[session.id],
        todoItems: todoItems[session.id] || [],
        segments: segments[session.id] || [],
        files,
        newDeliverables,
        fromIm: session.from_im === true,
        readOnly: session.read_only === true,
        pinned: pinnedSet.has(session.id),
        streaming: session.id === sendingSessionId,
        turnSettled: settled,
      });
    });
    /*
     * 排序: 置顶 > IM 共用那条 > 其余。
     *
     * IM 那条(``from_im``)就是与飞书机器人对话本身, 是用户进这个页面时唯一确定「刚才在
     * 飞书里聊的那件事」的入口 —— 它必须露在列表最上面, 否则首屏会是一堆网页新建的会话,
     * 用户得自己找那条。``Array.prototype.sort`` 是稳定的, 所以只在 ``from_im`` 上分胜负,
     * 组内保持后端给的顺序; 置顶仍然压过它: 那是用户自己排的。
     */
    const imFirst = built.length
      ? [...built].sort((a, b) => Number(b.fromIm) - Number(a.fromIm))
      : built;
    return sortTasksByPin(imFirst, pinnedIds);
  }, [
    sessions,
    titles,
    summaries,
    todos,
    todoItems,
    segments,
    deliverables,
    pendingRevision,
    pinnedIds,
    sendingSessionId,
    live.settledBySession,
  ]);

  const filtered = useMemo(() => filterTasks(tasks, filter, search), [tasks, filter, search]);
  const counts = useMemo(() => countTasks(tasks), [tasks]);
  const monthlyRuns = monthlyStats?.count ?? 0;

  return {
    tasks,
    filtered,
    counts,
    monthlyRuns,
    /** 两个桶, 给提示用: 有清单的 / 只回了话的。 */
    monthlyStats,
    filter,
    setFilter,
    search,
    setSearch,
    segments,
    todoItems,
    refresh,
    /** 回合进行中由轮询写入; 回合结束后 App 仍调 ``refresh`` 兜一次全量。 */
    refreshOne,
    refreshMonthly,
  };
}
