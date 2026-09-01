import { Sparkles } from "lucide-react";

export type DeliveryState = "none" | "generating" | "ready" | "saved";

export function TreasureVisual({
  state,
  size = "card",
}: {
  state: DeliveryState;
  size?: "mini" | "compact" | "card" | "hero";
}) {
  const gold = state === "ready" || state === "saved";
  return (
    <span className={`treasure-visual ${size} ${gold ? "gold" : "gray"} ${state === "saved" ? "saved" : ""}`} aria-hidden="true">
      <span className="treasure-assembly">
        <span className="treasure-lid" />
        <span className="treasure-body"><span className="treasure-lock" /></span>
      </span>
      {gold && <Sparkles className="treasure-spark one" size={size === "mini" ? 7 : 12} />}
      {gold && <Sparkles className="treasure-spark two" size={size === "mini" ? 6 : 9} />}
    </span>
  );
}
