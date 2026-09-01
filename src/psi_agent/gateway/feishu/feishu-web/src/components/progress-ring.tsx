import type { CSSProperties } from "react";
import { Clock3 } from "lucide-react";

export function ProgressRing({
  value,
  continuous = false,
  size = "md",
  showValue = true,
  label,
}: {
  value: number;
  continuous?: boolean;
  size?: "micro" | "sm" | "md" | "lg";
  showValue?: boolean;
  label?: string;
}) {
  const text = label?.trim() || (showValue ? `${value}` : null);
  return (
    <span
      className={`progress-ring ${size} ${continuous ? "continuous" : ""}`}
      style={{ "--progress": continuous ? undefined : `${value * 3.6}deg` } as CSSProperties}
      aria-label={
        continuous
          ? "任务处理中"
          : label?.trim()
            ? `进度 ${label.trim()}`
            : `进度 ${value}%`
      }
    >
      <span>{continuous && !label ? <Clock3 size={["micro", "sm"].includes(size) ? 11 : 15} /> : text ?? <i />}</span>
    </span>
  );
}
