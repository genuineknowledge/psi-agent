import type { LucideIcon } from "lucide-react";

export type TaskStatus = "working" | "attention" | "completed" | "continuous";
export type DeliveryState = "none" | "generating" | "ready" | "saved";

export type TaskStep = {
  label: string;
  state: "done" | "working" | "waiting";
};

export type Task = {
  id: string;
  title: string;
  shortTitle: string;
  category: string;
  summary: string;
  progress: number;
  status: TaskStatus;
  statusLabel: string;
  eta: string;
  updated: string;
  accent: string;
  deliverables: string[];
  deliveryState: DeliveryState;
  steps: TaskStep[];
};

export type ChatFile = {
  name: string
  /** Base64 payload (with or without data-URL prefix). */
  data: string
}

export type MessageFeedback = "up" | "down" | "";

/** Why a user turn failed (parity with spa v1 `failedReason`). */
export type FailedReason = "error" | "stopped" | "incomplete";

export type ChatMessage = {
  role: "agent" | "user";
  text: string;
  files?: ChatFile[];
  /** Local-only: like / dislike on agent replies (spa v1 parity). */
  feedback?: MessageFeedback;
  /** User turn did not get a complete agent reply. */
  failed?: boolean;
  failedReason?: FailedReason;
  /** Agent reply was aborted mid-stream. */
  stopped?: boolean;
};

export type SidebarPanel = "pending" | "deliveries" | "history" | null;
export type MainView = "workspace" | "new-task" | "templates";

export type InboxItem = {
  id: string;
  taskId: string;
  title: string;
  detail: string;
  kind: "attention" | "delivery" | "update";
  time: string;
  unread: boolean;
};

export type TaskTemplate = {
  id: string;
  title: string;
  category: string;
  description: string;
  starterPrompt: string;
  deliverables: string[];
  cadence: string;
  icon: LucideIcon;
};

export type CardTransition = {
  from: number;
  direction: "next" | "previous";
  token: number;
  fromExpanded: boolean;
};

export type FocusHistoryItem = {
  id: string;
  kind: "status" | "attention" | "delivery" | "update" | "conversation";
  title: string;
  detail: string;
  time: string;
};

export const OVERVIEW_LABEL = "任务总览";
export const PENDING_LABEL = "待您处理";
export const DELIVERY_LABEL = "新交付物";
