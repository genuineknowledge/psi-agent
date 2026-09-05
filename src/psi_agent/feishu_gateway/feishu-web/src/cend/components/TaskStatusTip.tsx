import { useEffect, useState } from "react";
import "./task-status-tip.css";

type Props = {
  onClose: () => void;
};

type TargetRect = {
  left: number;
  top: number;
  width: number;
  height: number;
  bottom: number;
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export default function TaskStatusTip({ onClose }: Props) {
  const [rect, setRect] = useState<TargetRect | null>(null);

  useEffect(() => {
    const update = () => {
      const icons = Array.from(
        document.querySelectorAll(".quick-actions .agent-status-tooltip-wrap"),
      );
      if (icons.length === 0) {
        setRect(null);
        return;
      }
      const rects = icons.map((el) => el.getBoundingClientRect());
      const left = Math.min(...rects.map((r) => r.left));
      const top = Math.min(...rects.map((r) => r.top));
      const right = Math.max(...rects.map((r) => r.right));
      const bottom = Math.max(...rects.map((r) => r.bottom));
      setRect({ left, top, width: right - left, height: bottom - top, bottom });
    };
    update();
    const retry = window.setInterval(update, 250);
    const timer = window.setTimeout(() => window.clearInterval(retry), 5000);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.clearInterval(retry);
      window.clearTimeout(timer);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, []);

  if (!rect) return null;

  const tooltipLeft = clamp(
    rect.left + rect.width / 2 - 166,
    12,
    Math.max(12, window.innerWidth - 356),
  );
  const arrowLeft = clamp(
    rect.left + rect.width / 2 - tooltipLeft - 9,
    14,
    300,
  );

  return (
    <>
      <span
        className="task-status-tip-frame"
        style={{ left: rect.left - 6, top: rect.top - 6, width: rect.width + 12, height: rect.height + 12 }}
      />
      <div
        className="task-status-tip"
        role="tooltip"
        style={{
          left: tooltipLeft,
          top: rect.bottom + 10,
        }}
      >
        <span className="task-status-tip-arrow" style={{ left: arrowLeft }} />
        <h3>当前任务状态区</h3>
        <p>依次标识 Agent 思考状态、执行状态与当前任务的新交付物。</p>
        <button type="button" onClick={onClose}>知道了</button>
      </div>
    </>
  );
}
