const TOOL_CALL_FULL = /\[Tool Call:\s*([A-Za-z0-9_.-]+)\(([\s\S]*)\)\]\s*$/;

export const TURN_PROGRESS = {
  planning: "规划下一步…",
  writing: "撰写回复…",
  toolGeneric: "调用工具",
} as const;

export type ProgressLog = {
  lines: string[];
  current: string;
};

export function progressLogStart(): ProgressLog {
  return { lines: [], current: TURN_PROGRESS.planning };
}

function basename(path: string): string {
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
  const parts = normalized.split("/");
  return parts[parts.length - 1] || path;
}

function asString(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

function parseToolArgs(raw: string): Record<string, unknown> {
  try {
    const v = JSON.parse(raw) as unknown;
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function pathArg(args: Record<string, unknown>): string {
  return (
    asString(args.path) ||
    asString(args.file) ||
    asString(args.filename) ||
    asString(args.target) ||
    asString(args.file_path)
  );
}

function queryArg(args: Record<string, unknown>): string {
  return asString(args.query) || asString(args.pattern) || asString(args.q) || asString(args.glob);
}

function commandArg(args: Record<string, unknown>): string {
  return asString(args.command) || asString(args.cmd) || asString(args.c);
}

export function summarizeToolCall(name: string, argsRaw: string): string {
  const key = name.trim().toLowerCase();
  const args = parseToolArgs(argsRaw);
  const path = pathArg(args);
  const file = path ? basename(path) : "";
  const query = queryArg(args);
  const cmd = commandArg(args);

  if (key === "read") return file ? `读取 \`${file}\`` : "读取文件";
  if (key === "write") return file ? `写入 \`${file}\`` : "写入文件";
  if (key === "edit") return file ? `编辑 \`${file}\`` : "编辑文件";
  if (key === "list_dir") return file ? `浏览 \`${file}\`` : "浏览目录";
  if (key === "find_files") return query ? `查找 ${query}` : "查找文件";
  if (key === "bash" || key === "powershell") {
    if (!cmd) return "执行命令";
    const short = cmd.length > 36 ? `${cmd.slice(0, 36)}…` : cmd;
    return `执行 \`${short}\``;
  }
  if (key === "todo") return "更新任务清单";
  if (key === "search" || key === "web_search") return query ? `检索 ${query}` : "网页检索";
  if (key === "fetch") return "拉取网页";
  if (key === "clarify") return "等待确认";
  if (key === "skill_manage") return "管理技能";
  if (key === "schedule_manage") return "安排定时任务";
  if (key === "flow_manage") return "编排流程";

  const prefix = key.split("_")[0] ?? key;
  if (prefix === "browser") return "浏览页面";
  if (prefix === "feishu") return "飞书操作";
  if (prefix === "wiki" || prefix === "goal") return "更新知识";
  if (prefix === "memory") return "读写记忆";
  if (prefix === "session" || prefix === "sessions") return "会话操作";
  if (!key) return TURN_PROGRESS.toolGeneric;
  return `调用 ${name}`;
}

export function summarizeToolCallText(text: string): string | null {
  const m = text.match(TOOL_CALL_FULL) ?? text.match(/\[Tool Call:\s*([A-Za-z0-9_.-]+)/);
  if (!m) return null;
  return summarizeToolCall(m[1] ?? "", m[2] ?? "{}");
}

function pushSummary(log: ProgressLog, summary: string): ProgressLog {
  const last = log.lines[log.lines.length - 1];
  if (last === summary) {
    return { lines: log.lines, current: TURN_PROGRESS.planning };
  }
  return {
    lines: [...log.lines, summary],
    current: TURN_PROGRESS.planning,
  };
}

export function applyProgressEvent(
  log: ProgressLog,
  kind: string | undefined,
  text: string,
): ProgressLog {
  if (kind === "tool_call") {
    const summary = summarizeToolCallText(text) ?? TURN_PROGRESS.toolGeneric;
    return pushSummary(log, summary);
  }
  if (kind === "tool_result") {
    return { lines: log.lines, current: TURN_PROGRESS.planning };
  }
  if (kind === "content") {
    return { lines: log.lines, current: TURN_PROGRESS.writing };
  }
  if (log.current === TURN_PROGRESS.writing) {
    return { lines: log.lines, current: TURN_PROGRESS.writing };
  }
  return { lines: log.lines, current: TURN_PROGRESS.planning };
}
