export type ContentSegments = {
  sealed: string[];
  current: string;
};

export function contentSegmentsStart(): ContentSegments {
  return { sealed: [], current: "" };
}

export function appendContentSegment(seg: ContentSegments, delta: string): ContentSegments {
  if (!delta) return seg;
  return { sealed: seg.sealed, current: seg.current + delta };
}

export function sealContentBeforeTools(seg: ContentSegments): ContentSegments {
  if (!seg.current.trim()) {
    return { sealed: seg.sealed, current: "" };
  }
  return { sealed: [...seg.sealed, seg.current], current: "" };
}

export function streamSegmentBodies(seg: ContentSegments): {
  interimText: string;
  text: string;
} {
  const sealed = seg.sealed.map((s) => s.trim()).filter(Boolean);
  if (sealed.length === 0) {
    return { interimText: "", text: seg.current };
  }
  return {
    interimText: sealed.join("\n\n"),
    text: seg.current,
  };
}

export function settleContentSegments(seg: ContentSegments): { finalText: string } {
  let finalText = seg.current.trim();
  if (!finalText) {
    const sealed = seg.sealed.map((s) => s.trim()).filter(Boolean);
    finalText = sealed.at(-1) ?? "";
  }
  return { finalText };
}
