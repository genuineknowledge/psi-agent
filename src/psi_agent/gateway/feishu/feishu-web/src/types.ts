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
  /**
   * 整回合墙钟耗时(毫秒), 用于「思考过程 · N秒」。
   *
   * 两个来源: 历史来自后端 ``thinking_ms``(JSONL 的 display-only 字段), 刚跑完的那一回合
   * 由前端自己量 —— 见 ``services/messageTiming.ts`` 的模块头。
   */
  thinkingMs?: number;
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
  /**
   * 组织共享的调度会话: **只读**(历史能看, 消息发不出去)。
   *
   * 由后端下发的 ``read_only`` 派生。列表上打角标、对话里把输入框换成一句说明 —— 这类会话
   * 在界面上与用户自己的会话长得一模一样, 不标出来就只能靠发一条消息去吃 403 才发现。
   */
  readOnly: boolean;
  /** 这条会话会一直变长(只有 IM 那条会), 提示用户开新会话。不代表已接近上下文上限。 */
  contextWarning: boolean;
  /** 用户置顶 —— 纯前端偏好(localStorage), 后排在最前。 */
  pinned: boolean;
}
