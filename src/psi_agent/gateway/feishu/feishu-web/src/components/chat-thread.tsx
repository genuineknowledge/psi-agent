import { useEffect, useRef } from "react";
import type { ChatMessage } from "../types";
import { ChatMessageItem } from "./chat-message-item";

/**
 * 会话消息列表。PR 版是 ToC 侧 haitun-agent/focus-chat-thread 的适配层 (把本地
 * ChatMessage 映射成那边的模型再转发), 这里直接渲染本地 ChatMessageItem ——
 * 否则就得把 ToC 的整棵组件树拷进来, 而那正是本次要去掉的死重量。
 */
export function ChatThread({
  messages,
  typing,
  userName,
  filePathOf,
  onFeedback,
  onRegenerate,
  onOpenFile,
  onRevealFile,
}: {
  messages: ChatMessage[];
  typing: boolean;
  userName: string;
  filePathOf: (name: string) => string | undefined;
  onFeedback: (index: number, kind: "up" | "down") => void;
  onRegenerate: (index: number) => void;
  onOpenFile: (name: string) => void;
  onRevealFile: (path: string) => void;
}) {
  const endRef = useRef<HTMLDivElement | null>(null);

  // 新消息 / 流式增量到达时贴住底部。
  //
  // 依赖里有 ``messages.at(-1)?.text`` —— 它每个流式 delta 都变, 所以这个 effect 是以
  // delta 的频率触发的, 而 ``scrollIntoView`` 每次都强制一次布局。合并到下一帧: cleanup
  // 取消上一帧还没跑的预约, 于是一帧最多滚一次, 增量再密也压不满布局。
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      endRef.current?.scrollIntoView({ block: "end" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [messages.length, typing, messages.at(-1)?.text, messages.at(-1)?.interimText]);

  return (
    <div className="focus-chat-thread">
      {messages.map((msg, i) => (
        <ChatMessageItem
          key={i}
          msg={msg}
          last={i === messages.length - 1}
          sending={typing}
          userName={userName}
          onFeedback={(kind) => onFeedback(i, kind)}
          onRegenerate={() => onRegenerate(i)}
          onOpenFile={onOpenFile}
          filePathOf={filePathOf}
          onRevealFile={onRevealFile}
        />
      ))}
      <div ref={endRef} />
    </div>
  );
}
