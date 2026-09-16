import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getSessionTodos,
  listSummaries,
  listTodoSegments,
  type SessionTodo,
  type SessionInfo,
  type TodoSegmentSummary,
  type TodoSummary,
} from "../api";
import type { Task } from "../types";
import { pendingDeliveriesFor, subscribePendingDeliveries } from "../services/pendingDeliveries";
import { sortTasksByPin } from "../services/pinnedTasks";
import { buildTask, countMonthlyRuns, countTasks, filterTasks } from "../services/taskModel";

/**
 * 任务总览的数据。每个会话要单独打 ``/todos`` 与 ``/todo-segments``, 所以并发拉取后
 * 按 id 归并 —— 串行会随会话数线性变慢。
 *
 * ``pinnedIds`` 是纯前端偏好(见 ``services/pinnedTasks.ts``): 传进来只做两件事 ——
 * 排在最前、打 ``pinned`` 标记。它**不进后端**, 也不参与过滤计数。
 */
export function useTasks(
  sessions: SessionInfo[],
  titles: Record<string, string>,
  deliverables: Record<string, { files: string[]; paths: Record<string, string> }> = {},
  pinnedIds: string[] = [],
) {
  const [todos, setTodos] = useState<Record<string, TodoSummary>>({});
  const [todoItems, setTodoItems] = useState<Record<string, SessionTodo[]>>({});
  const [segments, setSegments] = useState<Record<string, TodoSegmentSummary[]>>({});
  const [summaries, setSummaries] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [pendingRevision, setPendingRevision] = useState(0);

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
  ]);

  const filtered = useMemo(() => filterTasks(tasks, filter, search), [tasks, filter, search]);
  const counts = useMemo(() => countTasks(tasks), [tasks]);
  /** 本月执行: 统计口径见 taskModel.countMonthlyRuns(按会话去重, 取 todo 段时间戳)。 */
  const monthlyRuns = useMemo(() => countMonthlyRuns(segments), [segments]);

  return {
    tasks,
    filtered,
    counts,
    monthlyRuns,
    filter,
    setFilter,
    search,
    setSearch,
    segments,
    todoItems,
    refresh,
  };
}
