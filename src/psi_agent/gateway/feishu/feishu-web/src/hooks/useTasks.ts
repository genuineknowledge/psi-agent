import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getSessionTodos,
  listSummaries,
  listTodoSegments,
  type SessionInfo,
  type TodoSegmentSummary,
  type TodoSummary,
} from "../api";
import type { Task } from "../types";
import { buildTask, countTasks, filterTasks } from "../services/taskModel";

/**
 * 任务总览的数据。每个会话要单独打 ``/todos`` 与 ``/todo-segments``, 所以并发拉取后
 * 按 id 归并 —— 串行会随会话数线性变慢。
 */
export function useTasks(sessions: SessionInfo[], titles: Record<string, string>) {
  const [todos, setTodos] = useState<Record<string, TodoSummary>>({});
  const [segments, setSegments] = useState<Record<string, TodoSegmentSummary[]>>({});
  const [summaries, setSummaries] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");

  const refresh = useCallback(async () => {
    if (!sessions.length) {
      setTodos({});
      setSegments({});
      return;
    }
    const results = await Promise.all(
      sessions.map(async (s) => {
        const [todo, segs] = await Promise.all([
          getSessionTodos(s.id).catch(() => null),
          listTodoSegments(s.id).catch(() => [] as TodoSegmentSummary[]),
        ]);
        return { id: s.id, todo, segs };
      }),
    );
    const nextTodos: Record<string, TodoSummary> = {};
    const nextSegs: Record<string, TodoSegmentSummary[]> = {};
    for (const r of results) {
      if (r.todo) nextTodos[r.id] = r.todo.summary;
      nextSegs[r.id] = r.segs;
    }
    setTodos(nextTodos);
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

  const tasks = useMemo<Task[]>(
    () =>
      sessions.map((session) =>
        buildTask({
          session,
          title: titles[session.id] || "",
          summary: summaries[session.id],
          todos: todos[session.id],
          segments: segments[session.id] || [],
          // 交付物清单目前只有流式 blob 事件这一个来源, 历史里的附件由 historyMap 单独喂
          // 给会话视图。后端补上「按会话列交付物」的路由后在这里接。
          files: [],
          newDeliverables: [],
          fromIm: session.from_im === true,
        }),
      ),
    [sessions, titles, summaries, todos, segments],
  );

  const filtered = useMemo(() => filterTasks(tasks, filter, search), [tasks, filter, search]);
  const counts = useMemo(() => countTasks(tasks), [tasks]);

  return { tasks, filtered, counts, filter, setFilter, search, setSearch, segments, refresh };
}
