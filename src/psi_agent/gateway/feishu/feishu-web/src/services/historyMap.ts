import type { HistoryMessage } from "../api";
import type { ChatMessage } from "../types";
import { normalizeFailedTurns, type TurnMessageLike } from "./messageTurn";

/**
 * 后端 history 的形状 → 前端 ChatMessage。
 *
 * 后端把工具调用给成 ``{name, arguments}`` 对象数组, UI 要的是可直接显示的字符串行;
 * 附件给成 ``{name, path}``, UI 分成 files (名字) 和一张单独的路径表。
 */
export function toolLine(tool: { name: string; arguments?: string }): string {
  const args = (tool.arguments || "").trim();
  if (!args || args === "{}") return tool.name;
  const oneLine = args.replace(/\s+/g, " ");
  return `${tool.name} ${oneLine.length > 120 ? `${oneLine.slice(0, 120)}…` : oneLine}`;
}

export function mapHistory(raw: HistoryMessage[]): {
  messages: ChatMessage[];
  filePaths: Record<string, string>;
} {
  const filePaths: Record<string, string> = {};
  const mapped: ChatMessage[] = [];

  for (const item of raw) {
    // 调度静默记录不进会话气泡, 也不该出现在历史交付物里。
    if (item.kind === "schedule.silent") continue;
    const role = item.role === "user" ? "user" : "assistant";
    // history 里可能有 system / tool 之类的角色, UI 只展示这两种。
    if (item.role !== "user" && item.role !== "assistant") continue;

    const files: string[] = [];
    for (const f of item.files || []) {
      if (!f?.name) continue;
      files.push(f.name);
      if (f.path) filePaths[f.name] = f.path;
    }
    // 后端 history 的实际形状是 ``sends: [绝对路径]``(``[SEND:]`` 解析产物);
    // 刷新后不从这里恢复, 交付物列表和消息里的文件芯片都会丢。
    for (const rawPath of item.sends || []) {
      const path = typeof rawPath === "string" ? rawPath.trim() : "";
      if (!path) continue;
      const name = path.replace(/\\/g, "/").split("/").pop() || path;
      files.push(name);
      filePaths[name] = path;
    }
    const uniqueFiles = [...new Set(files)];

    mapped.push({
      role,
      text: typeof item.text === "string" ? item.text : "",
      ...(item.reasoning ? { reasoning: item.reasoning } : {}),
      ...(item.tools?.length ? { tools: item.tools.map(toolLine) } : {}),
      ...(uniqueFiles.length ? { files: uniqueFiles } : {}),
      // 整回合耗时: 后端在 JSONL 里记了(display-only), 只在正数时才带上 —— 0 与缺失
      // 都表现为"不显示", 但显式带上 0 会让「思考过程 · 0秒」这种噪音出现在快回合上。
      ...(typeof item.thinking_ms === "number" && item.thinking_ms > 0
        ? { thinkingMs: item.thinking_ms }
        : {}),
    });
  }

  // 复用 PR 里那套「用户发了但没收到完整回复」的判定, 它是纯函数。
  const normalized = normalizeFailedTurns(mapped as TurnMessageLike[]) as ChatMessage[];
  return { messages: normalized, filePaths };
}
