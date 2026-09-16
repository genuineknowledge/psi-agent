import { ArrowLeft, ArrowRight, Plus } from "lucide-react";
import { PanelLeftOpen } from "lucide-react";
import { brandMark } from "./brand";
import { TreasureVisual } from "./treasure";

export function ChatTopbar({
  title,
  sending,
  hasNewDeliveries,
  taskIndex,
  taskCount,
  contextCollapsed,
  onToggleContext,
  onPrevTask,
  onNextTask,
  onNewTask,
  onOpenDeliverables,
}: {
  title: string;
  sending: boolean;
  hasNewDeliveries: boolean;
  taskIndex: number;
  taskCount: number;
  contextCollapsed: boolean;
  onToggleContext: () => void;
  onPrevTask: () => void;
  onNextTask: () => void;
  onNewTask: () => void;
  onOpenDeliverables: () => void;
}) {
  return (
    <header className="cend2-chat-top">
      <div className="cend2-chat-heading">
        {contextCollapsed && (
          <button
            type="button"
            className="context-panel-toggle context-panel-toggle-in-chat"
            aria-label="展开任务上下文栏"
            title="展开任务上下文栏"
            onClick={onToggleContext}
          >
            <PanelLeftOpen size={15} />
          </button>
        )}
        {brandMark("mini")}
        <span>
          任务海豚工作室 <strong>「{title || "未命名任务"}」</strong>
        </span>
      </div>
      {taskCount > 1 && (
        <div className="cend2-chat-pager">
          <button
            type="button"
            className="cend2-pager-btn"
            aria-label="上一任务"
            disabled={taskIndex <= 0}
            onClick={onPrevTask}
          >
            <ArrowLeft size={14} />
          </button>
          <span>{String(taskIndex + 1).padStart(2, "0")} / {String(taskCount).padStart(2, "0")}</span>
          <button
            type="button"
            className="cend2-pager-btn"
            aria-label="下一任务"
            disabled={taskIndex >= taskCount - 1}
            onClick={onNextTask}
          >
            <ArrowRight size={14} />
          </button>
        </div>
      )}
      <div className="cend2-quick">
        {/*
          状态区只留**一个**运行指示器(圆点)。

          这里原本还有一只时钟, 与圆点一样跟 `sending` 走 —— 两个图标同时亮、同时灭, 摆在一
          起只会让人以为其中之一坏了(实测反馈「有点重复了」)。C 端之所以是两个, 是因为它那边
          各自语义不同(思考状态 / 执行状态); ToB 暂时只有「这一回合在不在跑」这一个信号,
          与其用同一个布尔量喂两个图标, 不如只留一个说清楚的那颗。

          要再分开, 前提是先有**两个不同的信号**(例如「正在等模型」与「正在跑工具」), 那时
          再各配一个图标 —— 别靠两个图标演同一个状态。
        */}
        <button
          type="button"
          className={`chat-top-icon agent-status-tooltip-wrap${sending ? " busy" : ""}`}
          aria-label={sending ? "Agent 正在思考执行任务" : "Agent 空闲"}
          title={sending ? "Agent 正在思考执行任务" : "Agent 空闲"}
        >
          <span className={`signal-orb ${sending ? "red" : "green"}`} />
        </button>
        <button
          type="button"
          className={`chat-top-icon${hasNewDeliveries ? " has-delivery" : ""}`}
          aria-label={hasNewDeliveries ? "查看新交付物" : "暂无新交付物"}
          title={hasNewDeliveries ? "查看新交付物" : "暂无新交付物"}
          onClick={onOpenDeliverables}
        >
          <TreasureVisual state={hasNewDeliveries ? "ready" : "none"} size="mini" />
        </button>
        <button type="button" className="cend2-newtask-mini" onClick={onNewTask}>
          <Plus size={13} />
          新建任务/聊天
        </button>
      </div>
    </header>
  );
}
