/**
 * Display helpers for assistant bubbles / card previews (presentation-only;
 * history/JSONL unchanged).
 */

/**
 * If the model put a short plan above a markdown thematic break (--- / *** / ___),
 * prefer the body below — Cursor-style chat shows the result, not the preamble.
 */
export function preferResultBelowRule(text: string): string {
  const parts = text.split(/\n(?:---|\*\*\*|___)\s*\n/);
  if (parts.length < 2) return text;
  const head = (parts[0] ?? "").trim();
  const tail = parts.slice(1).join("\n---\n").trim();
  if (!tail) return text;
  // Only strip when the head looks like a short plan, not a real sectioned doc.
  if (head.length > 0 && head.length <= 800) return tail;
  return text;
}

/**
 * Flatten Markdown to plain text for task card / context / history previews.
 * Keeps readable words; drops `#` / `**` / `` ` `` / link targets / fences.
 */
export function plainTextFromMarkdown(text: string): string {
  let s = typeof text === "string" ? text.replace(/\r\n/g, "\n") : "";
  if (!s) return "";

  s = s.replace(/```[\w+-]*\n?([\s\S]*?)```/g, (_m, code: string) => ` ${String(code).trim()} `);
  s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");
  s = s.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
  s = s.replace(/^#{1,6}\s+/gm, "");
  // Bold/italic before leftover markers (no lookbehind — broader target support).
  s = s.replace(/\*\*([^*]+)\*\*/g, "$1");
  s = s.replace(/__([^_]+)__/g, "$1");
  s = s.replace(/\*([^*\n]+)\*/g, "$1");
  s = s.replace(/_([^_\n]+)_/g, "$1");
  s = s.replace(/`([^`]+)`/g, "$1");
  s = s.replace(/```+/g, "");
  s = s.replace(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/gm, " ");
  s = s.replace(/[*_#>`]+/g, " ");
  s = s.replace(/\s+/g, " ").trim();
  return s;
}

/** Clip a Markdown reply into a short plain preview (ellipsis when truncated). */
export function plainTextPreview(text: string, maxLen = 120): string {
  const plain = plainTextFromMarkdown(text);
  if (plain.length <= maxLen) return plain;
  return `${plain.slice(0, maxLen)}…`;
}
