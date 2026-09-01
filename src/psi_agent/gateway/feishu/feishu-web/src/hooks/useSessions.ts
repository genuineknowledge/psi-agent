import { useCallback, useEffect, useRef, useState } from "react";
import {
  createSession,
  deleteSession,
  getFeishuDefaultAiId,
  getSessionHistory,
  listSessions,
  listTitles,
  setTitle,
  type SessionInfo,
} from "../api";

/**
 * 部署没配 AI 时的文案 —— 指向**部署配置**, 不是让用户自己去配模型。
 *
 * 飞书是 ToB: AI 由部署者用 `--feishu-ai-id` 定死, B 端用户既看不见也改不了, 所以这条提示
 * 必须让人去找管理员, 而不是去找一个并不存在的设置页。旧文案「没有可用模型, 无法新建会话」
 * 读起来像用户自己该去配点什么。
 */
const NO_AI_CONFIGURED =
  "本次部署未配置 AI 实例, 无法新建会话。请联系管理员为 Gateway 配置 --feishu-ai-id。";

/**
 * 会话列表 + 标题 + 当前选中会话。
 *
 * 从 PR 的 App.tsx 里拆出来的原因: 那边把会话列表、历史加载、流式收发、任务过滤全塞在
 * 一个 829 行的组件里, 状态互相串。这里一个 hook 只管一件事, 流式收发在 useChatTurn。
 */
export function useSessions() {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [currentId, setCurrentId] = useState<string>("");
  const [defaultAiId, setDefaultAiId] = useState<string>("");
  const [error, setError] = useState<string>("");

  const refresh = useCallback(async () => {
    try {
      const [list, titleMap] = await Promise.all([listSessions(), listTitles()]);
      setSessions(list);
      setTitles(titleMap);
      return list;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return [];
    }
  }, []);

  // 首屏: 会话列表 + 后端指定的缺省 AI (新建会话时的 backend_id)。
  //
  // **只认后端 ``/feishu/defaults`` 给的那一个 id**, 前端不挑也不兜底 —— 兜底触发就意味着
  // 网页应用悄悄换了个与机器人不同的模型, 静默走偏比直接报错难查。拿不到就留空, 由 create
  // 报 NO_AI_CONFIGURED, 会话列表本身照常显示。
  useEffect(() => {
    void (async () => {
      await refresh();
      try {
        setDefaultAiId(await getFeishuDefaultAiId());
      } catch {
        // 这一条拿不到不该挡住会话列表, 新建会话时再报。
      }
    })();
  }, [refresh]);

  const create = useCallback(async () => {
    if (!defaultAiId) {
      setError(NO_AI_CONFIGURED);
      return "";
    }
    try {
      // 不传 id → 后端发新 uuid → 新 jsonl。这是「网页里能开多个会话」的全部机制。
      const info = await createSession(defaultAiId);
      // 先落一个占位标题: 首轮结束后 App.tsx 会用首句 prompt 派生的标题覆盖它。没有占位
      // 的话列表里会是一排「未命名任务」, 多会话反而更难用。
      const placeholder = `新会话 ${new Date().toLocaleString("zh-CN", { hour12: false })}`;
      await setTitle(info.id, placeholder).catch(() => undefined);
      setTitles((prev) => ({ ...prev, [info.id]: placeholder }));
      await refresh();
      setCurrentId(info.id);
      return info.id;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return "";
    }
  }, [defaultAiId, refresh]);

  const remove = useCallback(
    async (id: string) => {
      try {
        await deleteSession(id);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return;
      }
      const list = await refresh();
      setCurrentId((cur) => (cur === id ? list[0]?.id || "" : cur));
    },
    [refresh],
  );

  return {
    sessions,
    titles,
    currentId,
    setCurrentId,
    defaultAiId,
    error,
    setError,
    refresh,
    create,
    remove,
    setTitles,
  };
}

/** 按会话 id 拉历史, 切换会话时自动取消上一次 (避免旧响应盖掉新会话)。 */
export function useSessionHistory(sessionId: string) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [raw, setRaw] = useState<Awaited<ReturnType<typeof getSessionHistory>>>([]);
  const seq = useRef(0);

  const reload = useCallback(async (id: string) => {
    const mine = ++seq.current;
    if (!id) {
      setRaw([]);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const data = await getSessionHistory(id);
      if (seq.current !== mine) return; // 已切走, 丢弃
      setRaw(data);
    } catch (err) {
      if (seq.current !== mine) return;
      setError(err instanceof Error ? err.message : String(err));
      setRaw([]);
    } finally {
      if (seq.current === mine) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload(sessionId);
  }, [sessionId, reload]);

  return { raw, setRaw, loading, error, reload };
}
