export function brandMark(size?: string) {
  return (
    <span className={`brand-logo brand-logo-${size || "mini"}`} aria-hidden="true">
      <span className="brand-logo-art" />
    </span>
  );
}

/**
 * 状态胶囊。四种状态各有自己的颜色, 别再靠「是不是已完成」二分:
 * 运行中(蓝) / 已完成(绿) / 待处理(橙) / 待开始与进行中(中性)。
 *
 * 「运行中」是 2026-09-16 加的: 它由回合信号给出(见 taskModel.statusOf), 是列表上唯一能
 * 看出「这条正在干活」的标记 —— 此前一律显示「进行中/待处理」那种 todo 状态, 于是执行中
 * 与停在那儿看起来一样。
 */
export function statusPill(status: string) {
  const cls =
    status === "已完成"
      ? "done"
      : status === "运行中"
        ? "running"
        : status === "待处理"
          ? "warn"
          : status === "待开始"
            ? "idle"
            : "";
  return <span className={`ht-pill ${cls}`}>{status}</span>;
}

/** ``title`` 用来交代这一格的口径 —— 四个数字都是算出来的, 悬停能看清怎么算的。 */
export function statCell(num: string, label: string, title?: string) {
  return (
    <div className="ht-stat" title={title}>
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
