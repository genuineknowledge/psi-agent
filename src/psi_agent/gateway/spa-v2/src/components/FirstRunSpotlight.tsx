import { useEffect, useState, type CSSProperties } from "react";
import "./first-run-spotlight.css";

type SpotlightStep = 1 | 2 | 3 | 4;

type Props = {
  step: SpotlightStep;
  onConfirm: () => void;
  onSkip: () => void;
};

const STEP_TARGETS: Record<SpotlightStep, string> = {
  1: ".signal-controls > button:nth-child(1)",
  2: ".signal-controls > button:nth-child(2)",
  3: ".user-hub-shortcuts > button:nth-child(1)",
  4: ".user-hub-shortcuts > button:nth-child(2)",
};

const STEP_COPY: Record<SpotlightStep, { title: string; desc: string }> = {
  1: {
    title: "待您处理",
    desc: "显示待处理任务数，点击可查看需要您介入的任务。",
  },
  2: {
    title: "新交付物",
    desc: "显示新成果数，点击可查看 Agent 刚生成的交付物。",
  },
  3: {
    title: "模型池",
    desc: "用于选择可用模型，也可以在这里连接新的大模型。",
  },
  4: {
    title: "设置",
    desc: "可切换海豚工作室与 Agent 包。",
  },
};

const TOOLTIP_WIDTH = 504;
const TOOLTIP_MARGIN = 16;
const TOOLTIP_HEIGHT = 244;

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export default function FirstRunSpotlight({ step, onConfirm, onSkip }: Props) {
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
  const below = step <= 2;
  const tooltipWidth = Math.min(TOOLTIP_WIDTH, window.innerWidth - TOOLTIP_MARGIN * 2);
  const tooltipStyle: CSSProperties = { left: TOOLTIP_MARGIN, top: TOOLTIP_MARGIN };
  if (rect) {
    const left = clamp(
      rect.left + rect.width / 2 - tooltipWidth / 2,
      TOOLTIP_MARGIN,
      Math.max(TOOLTIP_MARGIN, window.innerWidth - tooltipWidth - TOOLTIP_MARGIN),
    );
    const arrowLeft = clamp(rect.left + rect.width / 2 - left, 26, Math.max(26, tooltipWidth - 26));
    tooltipStyle.left = left;
    tooltipStyle.top = below
      ? rect.bottom + 18
      : clamp(rect.top - TOOLTIP_HEIGHT, TOOLTIP_MARGIN, Math.max(TOOLTIP_MARGIN, window.innerHeight - TOOLTIP_HEIGHT));
    (tooltipStyle as Record<string, string | number>)["--spotlight-arrow-left"] = `${arrowLeft}px`;
  }

  return (
    <div className="spotlight-layer" role="dialog" aria-modal="true" aria-label={copy.title}>
      <button type="button" className="spotlight-skip" onClick={onSkip}>
        跳过新手引导
      </button>
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
        <div className={`spotlight-tooltip ${below ? "below" : "above"}`} style={tooltipStyle}>
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
