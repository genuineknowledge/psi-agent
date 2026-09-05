import type { Task } from "./model";
import { DELIVERY_LABEL, PENDING_LABEL } from "./model";

/**
 * Shared inbox signals for overview metrics + sidebar topline.
 *
 * **pending（待您处理）**：今天只过滤 `status === "attention"`。
 * Gateway/Session 尚未写入该状态（联调路径几乎恒为空）——接口先留好，
 * 后续 clarify / 权限申请等「等人」信号接到 `attention` 即可，无需再分叉 UI。
 */
export type TaskSignalKind = "working" | "pending" | "deliveries";

export const WORKING_LABEL = "运行中";

export function signalLabel(kind: TaskSignalKind): string {
  switch (kind) {
    case "working":
      return WORKING_LABEL;
    case "pending":
      return PENDING_LABEL;
    case "deliveries":
      return DELIVERY_LABEL;
  }
}

export function filterTasksBySignal(tasks: Task[], kind: TaskSignalKind): Task[] {
  switch (kind) {
    case "working":
      return tasks.filter((task) => task.status === "working" || task.status === "continuous");
    case "pending":
      return tasks.filter((task) => task.status === "attention");
    case "deliveries":
      return tasks.filter((task) => task.newDeliverables.length > 0);
  }
}
