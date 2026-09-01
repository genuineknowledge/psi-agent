export function brandMark(size?: string) {
  return (
    <span className={`brand-logo brand-logo-${size || "mini"}`} aria-hidden="true">
      <span className="brand-logo-art" />
    </span>
  );
}

export function statusPill(status: string) {
  const cls = status === "已完成" ? "done" : status === "进行中" ? "" : "warn";
  return <span className={`ht-pill ${cls}`}>{status}</span>;
}

export function statCell(num: string, label: string) {
  return (
    <div className="ht-stat">
      <strong>{num}</strong>
      <em>{label}</em>
    </div>
  );
}

export function stepChip(step: { t: string; s: string }) {
  const cls = step.s || "waiting";
  return (
    <div className={`cend2-step ${cls}`}>
      <span className="cend2-step-marker">
        {cls === "done" ? <CheckIcon /> : <i className={`cend2-step-dot${cls === "working" ? "" : " off"}`} />}
      </span>
      <span className="cend2-step-label">{step.t}{cls === "working" ? <em>进行中</em> : ""}</span>
    </div>
  );
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}
