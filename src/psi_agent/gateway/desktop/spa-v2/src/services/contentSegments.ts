/**
 * Split streamed assistant content across tool rounds.
 *
 * Before each tool_call, the current segment is sealed into the temporary
 * bubble (concatenated). After the last tools, the growing segment is the
 * final bubble. On settle the temporary bubble is discarded - not moved into
 * tools/thinking disclosures.
 */

export type ContentSegments = {
  /** Sealed step-between narration (raw, may include trailing whitespace). */
  sealed: string[]
  /** Growing segment since the last tool_call (or turn start). */
  current: string
}

export function contentSegmentsStart(): ContentSegments {
  return { sealed: [], current: '' }
}

export function appendContentSegment(seg: ContentSegments, delta: string): ContentSegments {
  if (!delta) return seg
  return { sealed: seg.sealed, current: seg.current + delta }
}

/** Call when a tool_call arrives - park current prose into the temporary bubble. */
export function sealContentBeforeTools(seg: ContentSegments): ContentSegments {
  if (!seg.current.trim()) {
    return { sealed: seg.sealed, current: '' }
  }
  return { sealed: [...seg.sealed, seg.current], current: '' }
}

/**
 * Live dual-bubble bodies:
 * - No seals yet -> all content streams as text (may become the only final).
 * - After seals -> interimText is sealed notes joined; text is the new segment.
 */
export function streamSegmentBodies(seg: ContentSegments): {
  interimText: string
  text: string
} {
  const sealed = seg.sealed.map((s) => s.trim()).filter(Boolean)
  if (sealed.length === 0) {
    return { interimText: '', text: seg.current }
  }
  return {
    interimText: sealed.join('\n\n'),
    text: seg.current,
  }
}

/** End of turn: keep only the last segment; temporary sealed notes are dropped. */
export function settleContentSegments(seg: ContentSegments): { finalText: string } {
  let finalText = seg.current.trim()
  if (!finalText) {
    const sealed = seg.sealed.map((s) => s.trim()).filter(Boolean)
    finalText = sealed.at(-1) ?? ''
  }
  return { finalText }
}
