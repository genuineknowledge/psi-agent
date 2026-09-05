export type FailedReason = "error" | "stopped" | "incomplete";

export type TurnMessageLike = {
  role: "user" | "assistant";
  text: string;
  files?: string[];
  stopped?: boolean;
  failed?: boolean;
  failedReason?: FailedReason;
};

export function stripErrorAnnotations(text: string): string {
  if (!text) return "";
  return String(text)
    .replace(/\n?\[Error:[^\]]*\]/g, "")
    .replace(/\n?\[错误\][^\n]*/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function isCompleteAgent(msg: TurnMessageLike | null | undefined): boolean {
  if (!msg || msg.role !== "assistant") return false;
  if (msg.stopped) return false;
  const text = typeof msg.text === "string" ? msg.text : "";
  const hasFiles = Array.isArray(msg.files) && msg.files.length > 0;
  const clean = stripErrorAnnotations(text);
  return !!clean || hasFiles;
}

export function inferFailedReason(agentMsg: TurnMessageLike | null | undefined): FailedReason {
  if (!agentMsg || agentMsg.role !== "assistant") return "incomplete";
  if (agentMsg.stopped) return "stopped";
  const text = typeof agentMsg.text === "string" ? agentMsg.text : "";
  const hasFiles = Array.isArray(agentMsg.files) && agentMsg.files.length > 0;
  const clean = stripErrorAnnotations(text);
  if (!clean && !hasFiles && (text.includes("[Error:") || text.includes("[错误]"))) return "error";
  return "incomplete";
}

export const FAILED_REASON_LABEL: Record<FailedReason, string> = {
  error: "未收到回复（请求异常）",
  stopped: "未收到完整回复（已停止）",
  incomplete: "未收到回复",
};

export function normalizeFailedTurns(msgs: TurnMessageLike[]): TurnMessageLike[] {
  if (!Array.isArray(msgs) || !msgs.length) return [];

  const out: TurnMessageLike[] = [];
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    if (!m || typeof m !== "object") continue;

    if (m.role === "assistant") {
      if (isCompleteAgent(m)) out.push({ ...m, failed: false });
      continue;
    }

    if (m.role !== "user") {
      out.push(m);
      continue;
    }

    const next = msgs[i + 1];
    if (isCompleteAgent(next)) {
      out.push({ ...m, failed: false });
      out.push({ ...next!, failed: false });
      i++;
      continue;
    }

    out.push({
      ...m,
      failed: true,
      failedReason: inferFailedReason(next?.role === "assistant" ? next : null),
    });
    if (next?.role === "assistant") i++;
  }
  return out;
}
