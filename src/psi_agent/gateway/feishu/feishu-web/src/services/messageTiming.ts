/**
 * 「已思考 N 秒」的时长格式化。
 *
 * 数据源是两个, 刻意都收进来:
 * * **历史**: 后端 ``thinking_ms``(整回合墙钟毫秒, JSONL 的 display-only 字段, 见
 *   ``session/agent.py`` 的 ``THINKING_MS_KEY``), 随 ``/feishu/sessions/{id}/history`` 下来。
 * * **本回合**: SSE 不带这个字段, 由前端从发出到 ``[DONE]`` 自己量(见 ``useChatTurn``)。
 *   不量的话刚跑完的这一条要等下次拉历史才有耗时, 看起来像坏了。
 */

export function formatThinkingDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  const totalSec = Math.max(0, Math.round(ms / 1000));
  if (totalSec < 60) return `${totalSec}秒`;
  const minutes = Math.floor(totalSec / 60);
  const seconds = totalSec % 60;
  if (minutes < 60) return seconds > 0 ? `${minutes}分${seconds}秒` : `${minutes}分`;
  const hours = Math.floor(minutes / 60);
  const remMin = minutes % 60;
  return remMin > 0 ? `${hours}小时${remMin}分` : `${hours}小时`;
}

/** 把时长拼到「思考过程」这类标题后面; 没有时长就原样返回, 不留一个孤零零的分隔点。 */
export function thinkingHeaderWithDuration(baseLabel: string, thinkingMs: number | undefined): string {
  if (typeof thinkingMs !== "number" || thinkingMs < 0) return baseLabel;
  const duration = formatThinkingDuration(thinkingMs);
  return duration ? `${baseLabel} · ${duration}` : baseLabel;
}
