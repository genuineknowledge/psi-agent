export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  interimText?: string;
  reasoning?: string;
  tools?: string[];
  progress?: string[];
  files?: string[];
  feedback?: "up" | "down";
  failed?: boolean;
  failedReason?: "error" | "stopped" | "incomplete";
  stopped?: boolean;
}

export interface Task {
  id: string;
  title: string;
  summary?: string;
  status: string;
  newDeliverables: string[];
  deliveryState: "none" | "generating" | "ready" | "saved";
  progress: number;
  indeterminate?: boolean;
  progressLabel?: string;
  hasTodoTrack?: boolean;
  phase?: "advance" | "deliver" | "done";
  phaseLabel?: string;
  sop: string;
  owner: string;
  updated: string;
  files: string[];
  steps: Array<{ t: string; s: string; detail?: string }>;
}
