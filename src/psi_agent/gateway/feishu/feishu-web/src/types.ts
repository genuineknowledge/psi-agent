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
  /** 是否 IM 里那条会话 —— 卡片上打「来自飞书对话」角标, 让用户知道它与 IM 共通。 */
  fromIm: boolean;
  /** 这条会话会一直变长(只有 IM 那条会), 提示用户开新会话。不代表已接近上下文上限。 */
  contextWarning: boolean;
}
