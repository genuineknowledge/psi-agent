/**
 * Display helpers for assistant bubbles (presentation-only; history/JSONL unchanged).
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
