import { AlertCircle, CheckCircle2, Clock3, Sparkles } from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { mobileHaptic, prefersReducedMotion } from "./client-feedback";
import type { DeliveryState, Task } from "./model";

export function BrandLogo({ size = "sidebar" }: { size?: "mini" | "sidebar" | "hero" }) {
  return (
    <span className={`brand-logo brand-logo-${size}`} aria-hidden="true">
      <span className="brand-logo-art" />
    </span>
  );
}

export function AgentMark() {
  return <BrandLogo size="mini" />;
}

export function ProgressRing({
  value,
  continuous = false,
  size = "md",
  showValue = true,
}: {
  value: number;
  continuous?: boolean;
  size?: "micro" | "sm" | "md" | "lg";
  showValue?: boolean;
}) {
  return (
    <span
      className={`progress-ring ${size} ${continuous ? "continuous" : ""}`}
      style={{ "--progress": `${value * 3.6}deg` } as CSSProperties}
      aria-label={continuous ? "持续任务运行中" : `进度 ${value}%`}
    >
      <span>{continuous ? <Clock3 size={["micro", "sm"].includes(size) ? 11 : 15} /> : showValue ? `${value}` : <i />}</span>
    </span>
  );
}

export function StatusPill({ task }: { task: Task }) {
  const { status, statusLabel } = task;
  const Icon = status === "attention" ? AlertCircle : CheckCircle2;
  return (
    <span className={`status-pill ${status}`}>
      {["working", "continuous"].includes(status) ? <ProgressRing value={task.progress} continuous={status === "continuous"} size="micro" showValue={false} /> : <Icon size={14} strokeWidth={2.4} />}
      {statusLabel}
    </span>
  );
}

export function TreasureVisual({
  state,
  size = "card",
  opening = false,
}: {
  state: DeliveryState;
  size?: "mini" | "compact" | "card" | "hero";
  opening?: boolean;
}) {
  const gold = state === "ready" || state === "saved";
  return (
    <span className={`treasure-visual ${size} ${gold ? "gold" : "gray"} ${state === "saved" ? "saved" : ""} ${opening ? "opening" : ""}`} aria-hidden="true">
      <span className="treasure-assembly">
        <span className="treasure-lid" />
        <span className="treasure-body"><span className="treasure-lock" /></span>
        <span className="treasure-coins">{Array.from({ length: 9 }, (_, index) => <i key={index} />)}</span>
      </span>
      {gold && <Sparkles className="treasure-spark one" size={size === "mini" ? 7 : 12} />}
      {gold && <Sparkles className="treasure-spark two" size={size === "mini" ? 6 : 9} />}
    </span>
  );
}

export function TreasureButton({
  task,
  onOpen,
  compact = false,
}: {
  task: Task;
  onOpen: (task: Task) => void;
  compact?: boolean;
}) {
  const [opening, setOpening] = useState(false);
  const openTimer = useRef<number | null>(null);
  useEffect(() => () => {
    if (openTimer.current) window.clearTimeout(openTimer.current);
  }, []);

  const hasFiles = task.deliverables.length > 0;
  /** Gold only when there are real deliverables; otherwise black chest. */
  const visualState: DeliveryState = hasFiles
    ? (task.deliveryState === "saved" ? "saved" : "ready")
    : "none";

  const openTreasure = () => {
    if (opening) return;
    setOpening(true);
    mobileHaptic(12);
    openTimer.current = window.setTimeout(() => {
      onOpen(task);
      setOpening(false);
    }, prefersReducedMotion() ? 20 : 430);
  };

  return (
    <button
      type="button"
      className={`treasure-button ${hasFiles ? "ready" : "locked"} ${task.deliveryState === "saved" ? "settled" : ""} ${compact ? "compact" : ""} ${opening ? "opening" : ""}`}
      onClick={(event) => {
        event.stopPropagation();
        openTreasure();
      }}
      aria-label={hasFiles ? `打开 ${task.shortTitle} 的交付物` : `查看 ${task.shortTitle} 的交付物（暂无）`}
      title={hasFiles ? (task.deliveryState === "saved" ? "已保存到成果库，点击查看" : "新交付物已就绪，点击查看") : "暂时没有新的交付物，点击查看"}
    >
      <TreasureVisual state={visualState} size={compact ? "compact" : "card"} opening={opening} />
    </button>
  );
}
