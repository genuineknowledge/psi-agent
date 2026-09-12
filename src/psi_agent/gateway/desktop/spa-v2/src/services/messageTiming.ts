/**
 * Format Session display timing for spa-v2 bubbles /「已思考」.
 * ``created_at`` is ISO-8601 UTC from JSONL; ``thinking_ms`` is whole-turn wall ms.
 */

export function formatThinkingDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return ''
  const totalSec = Math.max(0, Math.round(ms / 1000))
  if (totalSec < 60) return `${totalSec}秒`
  const minutes = Math.floor(totalSec / 60)
  const seconds = totalSec % 60
  if (minutes < 60) {
    return seconds > 0 ? `${minutes}分${seconds}秒` : `${minutes}分`
  }
  const hours = Math.floor(minutes / 60)
  const remMin = minutes % 60
  return remMin > 0 ? `${hours}小时${remMin}分` : `${hours}小时`
}

/** Local wall-clock label for a message (today → HH:mm; older → short date). */
export function formatMessageClock(
  iso: string,
  now: Date = new Date(),
): string {
  const parsed = Date.parse(iso)
  if (!Number.isFinite(parsed)) return ''
  const then = new Date(parsed)
  const pad = (n: number) => String(n).padStart(2, '0')
  const hm = `${pad(then.getHours())}:${pad(then.getMinutes())}`

  const startOfDay = (d: Date) =>
    new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const dayDiff = Math.round((startOfDay(now) - startOfDay(then)) / 86_400_000)

  if (dayDiff === 0) return hm
  if (dayDiff === 1) return `昨天 ${hm}`
  if (then.getFullYear() === now.getFullYear()) {
    return `${then.getMonth() + 1}月${then.getDate()}日 ${hm}`
  }
  return `${then.getFullYear()}/${then.getMonth() + 1}/${then.getDate()} ${hm}`
}

export function thinkingHeaderWithDuration(
  baseLabel: string,
  thinkingMs: number | undefined,
): string {
  if (typeof thinkingMs !== 'number' || thinkingMs < 0) return baseLabel
  const duration = formatThinkingDuration(thinkingMs)
  return duration ? `${baseLabel} · ${duration}` : baseLabel
}
