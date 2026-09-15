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
import { buildTask, countTasks, filterTasks } from "../services/taskModel";

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
    const built = sessions.map((session) =>
      buildTask({
        session,
        title: titles[session.id] || "",
        summary: summaries[session.id],
        todos: todos[session.id],
        todoItems: todoItems[session.id] || [],
        segments: segments[session.id] || [],
        // 交付物 = 历史记录恢复的附件（[SEND:] 解析路径）+ 流式轮次里收到的新文件。
        files: deliverables[session.id]?.files ?? [],
        newDeliverables: pendingDeliveriesFor(session.id),
        fromIm: session.from_im === true,
        pinned: pinnedSet.has(session.id),
      }),
    );
    // 置顶的排在最前(按置顶顺序), 其余保持原顺序 —— 见 sortTasksByPin。
    return sortTasksByPin(built, pinnedIds);
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

  return { tasks, filtered, counts, filter, setFilter, search, setSearch, segments, todoItems, refresh };
}
