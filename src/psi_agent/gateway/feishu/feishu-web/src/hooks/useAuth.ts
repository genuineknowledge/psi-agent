import { useCallback, useEffect, useState } from "react";
import { getFeishuAppId, getMe, login, loginDevBypass, type Me } from "../api";
import { FeishuAuthUnavailable, requestFeishuCode } from "../services/feishuAuth";

type Status = "loading" | "ready" | "failed";

/**
 * 登录态。顺序: 先问 ``/feishu/auth/me``(已有 cookie 就免了一次 JSAPI) → 否则走免登。
 *
 * 失败**一定**留一个可见的 ``retry`` —— code 只活 5 分钟, 用户从后台切回来时上一个 code
 * 大概率已过期, 没有重试入口就只能刷页面。
 */
export function useAuth() {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    void (async () => {
      setStatus("loading");
      setError("");
      try {
        const already = await getMe().catch(() => null);
        if (already) {
          if (!alive) return;
          setMe(already);
          setStatus("ready");
          return;
        }
        const appId = await getFeishuAppId();
        const code = await requestFeishuCode(appId);
        const who = await login(code);
        if (!alive) return;
        setMe(who);
        setStatus("ready");
      } catch (err) {
        if (!alive) return;
        // 不在飞书客户端内: 试一次后端的开发旁路。它只有在后端显式设了
        // PSI_FEISHU_DEV_OPEN_ID 时才会成功, 默认配置下照样失败 —— 所以这不是
        // 「静默冒充身份」, 身份完全由后端决定。
        // ``requestFeishuCode`` 的抛错顺序决定这个分支够不着还是够得着: 它必须先
        // ``sdkReady()`` 再查 app_id, 否则本地开发的默认组合(app_id 空 + 客户端外)抛的是
        // 普通 Error, 这里判假, 整段旁路被跳过。见那边的注释。
        if (err instanceof FeishuAuthUnavailable) {
          const dev = await loginDevBypass().catch(() => null);
          if (dev && alive) {
            setMe(dev);
            setStatus("ready");
            return;
          }
        }
        setError(err instanceof Error ? err.message : String(err));
        setStatus("failed");
      }
    })();
    return () => {
      alive = false;
    };
  }, [attempt]);

  return { status, me, error, retry };
}
