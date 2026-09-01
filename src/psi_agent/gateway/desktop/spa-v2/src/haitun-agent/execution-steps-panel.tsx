import { useState } from "react";
import { Check, ChevronRight } from "lucide-react";
import type { TaskStep } from "./model";

/**
 * 独立折叠面板：聊天消息底部、输入框上方。
 * 有 todo 清单时展示与任务上下文一致的执行步骤；无 todo 时由调用方隐藏。
 */
export function ExecutionStepsPanel({
  steps,
}: {
  steps: TaskStep[];
}) {
  const [open, setOpen] = useState(false);

  return (
    <section className={`execution-steps-panel${open ? " is-open" : ""}`} aria-label="执行步骤">
      <button
        type="button"
        className="execution-steps-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <ChevronRight size={14} className="execution-steps-chevron" aria-hidden />
        <span className="execution-steps-title">执行步骤</span>
        <em>
          {steps.length > 0 ? `${steps.length} 步` : "暂无步骤"}
        </em>
      </button>
      <div className="execution-steps-body" role="list" aria-label="任务执行步骤" aria-live="polite">
        {steps.length > 0 ? (
          steps.map((step, index) => (
            <div className={`execution-steps-card ${step.state}`} role="listitem" key={`${index}-${step.label}`}>
              <div className="execution-steps-main">
                <span className="execution-steps-name">{step.label}</span>
                {step.detail?.trim() ? <em className="execution-steps-detail">{step.detail.trim()}</em> : null}
              </div>
              <span className="execution-steps-check" aria-hidden="true">
                {step.state === "done" ? <Check size={12} /> : null}
              </span>
            </div>
          ))
        ) : (
          <div className="execution-steps-empty">暂无执行步骤</div>
        )}
      </div>
    </section>
  );
}
