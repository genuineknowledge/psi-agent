/** Strip [SEND: ...] / [RECV: ...] markers before bubble render / copy. */
export function stripTransferMarkers(text: string): string {
  if (!text) return "";
  return String(text)
    .replace(/\[\s*SEND\s*:[^\]]*\]/gi, "")
    .replace(/\[\s*RECV\s*:[^\]]*\]/gi, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
