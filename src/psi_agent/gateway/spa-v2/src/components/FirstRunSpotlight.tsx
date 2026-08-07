import { useEffect, useState } from "react";
import "./first-run-spotlight.css";

type Props = {
  step: 1 | 2;
  onConfirm: () => void;
};

const STEP_TARGETS: Record<1 | 2, string> = {
  1: ".signal-controls",
  2: ".user-hub-shortcuts",
};

const STEP_COPY: Record<1 | 2, { title: string; desc: string }> = {
  1: {
    title: "任务提醒区",
    desc: "待您处理：显示待处理任务数，新交付物：显示新成果数。",
  },
  2: {
    title: "模型池与设置",
    desc: "模型池：用于选择可用模型，设置：可切换海豚工作室与 Agent 包。",
  },
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export default function FirstRunSpotlight({ step, onConfirm }: Props) {
  const [rect, setRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    const update = () => {
      const el = document.querySelector(STEP_TARGETS[step]);
      setRect(el ? el.getBoundingClientRect() : null);
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [step]);

  const copy = STEP_COPY[step];

  return (
    <div className="spotlight-layer" role="dialog" aria-modal="true" aria-label={copy.title}>
      {rect && (
        <div
          className="spotlight-hole"
          style={{
            left: rect.left - 6,
            top: rect.top - 6,
            width: rect.width + 12,
            height: rect.height + 12,
          }}
        />
      )}
      {rect && (
        <div
          className={`spotlight-tooltip ${step === 1 ? "below" : "above"}`}
          style={{
            left: clamp(rect.left - 10, 16, Math.max(16, window.innerWidth - 520)),
            top:
              step === 1
                ? rect.bottom + 18
                : clamp(rect.top - 244, 16, Math.max(16, window.innerHeight - 244)),
          }}
        >
          <div className="spotlight-tooltip-body">
            <h3>{copy.title}</h3>
            <p>{copy.desc}</p>
          </div>
          <div className="spotlight-actions">
            <button type="button" className="spotlight-confirm" onClick={onConfirm}>
              知道了
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
