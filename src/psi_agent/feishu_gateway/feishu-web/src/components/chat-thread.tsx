import { FocusChatThread } from "../cend/haitun-agent/focus-chat-thread";
import type { ChatMessage as CendChatMessage } from "../cend/haitun-agent/model";
import type { ChatMessage } from "../types";

export function ChatThread({
  messages,
  typing,
  title,
  liveThinking,
  progressLog,
  filePathOf,
  fileDataOf,
  onFeedback,
  onRegenerate,
}: {
  messages: ChatMessage[];
  typing: boolean;
  title: string;
  liveThinking: string;
  progressLog?: { lines: string[]; current: string } | null;
  filePathOf: (name: string) => string | undefined;
  fileDataOf: (name: string) => string | undefined;
  onFeedback: (index: number, kind: "up" | "down") => void;
  onRegenerate: (index: number) => void;
}) {
  const mapped: CendChatMessage[] = messages.map((m) => {
    const files = (m.files || []).map((name) => {
      const path = filePathOf(name);
      const data = fileDataOf(name) || "";
      return { name, data, ...(path ? { path } : {}) };
    });
    return {
      role: m.role === "user" ? "user" : "agent",
      text: m.text,
      ...(m.interimText ? { interimText: m.interimText } : {}),
      ...(m.reasoning ? { reasoning: m.reasoning } : {}),
      ...(m.tools && m.tools.length ? { tools: m.tools } : {}),
      ...(files.length ? { files } : {}),
      ...(m.feedback ? { feedback: m.feedback } : {}),
      ...(m.failed ? { failed: true, failedReason: m.failedReason || ("incomplete" as const) } : {}),
      ...(m.stopped ? { stopped: true } : {}),
    };
  });

  return (
    <FocusChatThread
      messages={mapped}
      typing={typing}
      title={title}
      liveThinking={liveThinking}
      progressLog={progressLog || null}
      onFeedback={onFeedback}
      onRegenerate={onRegenerate}
    />
  );
}
