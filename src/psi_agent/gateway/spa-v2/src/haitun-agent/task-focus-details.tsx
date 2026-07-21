import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronRight,
  FileArchive,
  FileText,
  History,
  MessageCircle,
  Sparkles,
  Zap,
} from "lucide-react";
import type { FocusHistoryItem, InboxItem, Task } from "./model";
import { ProgressRing, TreasureVisual } from "./primitives";

function FocusHistoryIcon({ item, task }: { item: FocusHistoryItem; task: Task | null }) {
  if (item.kind === "attention") return <AlertCircle size={15} />;
  if (item.kind === "delivery") return <TreasureVisual state="ready" size="mini" />;
  if (item.kind === "conversation") return <MessageCircle size={15} />;
  if (item.kind === "update") return <History size={15} />;
  if (task?.status === "completed") return <CheckCircle2 size={15} />;
  return <ProgressRing value={task?.progress ?? 68} continuous={task?.status === "continuous"} size="sm" showValue={false} />;
}

function deliveryFileDescription(fileName: string) {
  if (fileName.endsWith(".pdf")) return "文档摘要、关键结论与证据页可继续展开查看";
  if (fileName.endsWith(".xlsx")) return "字段、名单或证据明细已按结构化表格整理";
  if (fileName.endsWith(".docx")) return "正文、批注与可继续编辑的内容已经保留";
  return "任务生成的可复用文件，已关联到本次执行记录";
}

/**
 * Split-mode left pane: task context / history / deliverables.
 * Chat transcript lives on the right (FocusChatThread) — not duplicated here.
 */
export function TaskFocusDetails({
  task,
  tasks,
  inboxItems,
  onOpenArtifact,
}: {
  task: Task | null;
  tasks: Task[];
  inboxItems: InboxItem[];
  onOpenArtifact: (task: Task, fileName?: string) => void;
}) {
  const workingStep = task?.steps.find((step) => step.state === "working");
  const activeStep = workingStep
    ? (workingStep.detail?.trim()
      ? `${workingStep.label} · ${workingStep.detail.trim()}`
      : workingStep.label)
    : (task?.status === "completed" ? "全部执行步骤已完成" : "等待下一步");
  const taskNotices = task ? inboxItems.filter((item) => item.taskId === task.id) : inboxItems;
  const historyItems: FocusHistoryItem[] = [
    ...(task ? [{
      id: `status-${task.id}`,
      kind: "status" as const,
      title: `${task.statusLabel} · ${task.progress}%`,
      detail: task.summary,
      time: task.updated,
    }] : tasks.slice(0, Math.max(0, 3 - taskNotices.length)).map((item) => ({
      id: `status-${item.id}`,
      kind: "status" as const,
      title: `${item.shortTitle} · ${item.statusLabel}`,
      detail: item.summary,
      time: item.updated,
    }))),
    ...taskNotices.map((item) => ({
      id: `notice-${item.id}`,
      kind: item.kind,
      title: item.title,
      detail: item.detail,
      time: item.time,
    })),
  ].slice(0, 8);

  const finiteTasks = tasks.filter((item) => item.status !== "continuous");
  const overall = Math.round(finiteTasks.reduce((sum, item) => sum + item.progress, 0) / Math.max(finiteTasks.length, 1));
  // Historical = all session deliverables (not only "new"/ready).
  const historicalDeliveryTasks = task
    ? (task.deliverables.length ? [task] : [])
    : tasks.filter((item) => item.deliverables.length);
  const generatingTasks = task
    ? []
    : tasks.filter((item) => item.deliveryState === "generating");
  const historicalFileCount = historicalDeliveryTasks.reduce((sum, item) => sum + item.deliverables.length, 0);
  const emptyDeliveryCopy = !task
    ? "还没有历史交付物；Agent 通过 [SEND:] 交付文件后，会按会话累计显示在这里。"
    : task.status === "completed"
      ? "该任务会话尚无文件交付物。完成结果已记录在任务历史中。"
      : task.status === "continuous"
        ? "本会话暂无交付物，Agent 交付文件后会保留在这里。"
        : "本会话尚未形成交付物；生成后会累计出现在这里（刷新后仍保留列表）。";

  return (
    <div className="focus-detail-panel">
      <section className="focus-state-banner">
        <div>
          <span><Sparkles size={13} /> 当前任务上下文</span>
          <strong>{task ? task.title : "今天全部任务的执行上下文"}</strong>
          <p>{task ? task.summary : `共 ${tasks.length} 个任务，综合进度 ${overall}%。您可以基于任一历史动作继续补充要求。`}</p>
        </div>
        <div className="focus-state-grid">
          <span><em>状态</em><strong>{task?.statusLabel ?? `${tasks.length} 个任务`}</strong></span>
          <span><em>{task?.status === "continuous" ? "本轮进度" : "进度"}</em><strong>{task ? `${task.progress}%` : `${overall}%`}</strong></span>
          <span><em>当前阶段</em><strong>{task ? activeStep : `${tasks.filter((item) => item.status === "attention").length} 项待您处理`}</strong></span>
          <span><em>最近更新</em><strong>{task?.updated ?? "刚刚同步"}</strong></span>
        </div>
      </section>

      <section className="focus-execution-path" aria-label={task ? "任务执行路径" : "任务状态列表"}>
        <header><span><Zap size={13} />{task ? "执行路径" : "任务运行状态"}</span><em>新要求会追加到当前上下文，不覆盖既有结果</em></header>
        <div>
          {(task ? task.steps : tasks.slice(0, 4).map((item) => ({ label: item.shortTitle, state: item.status === "completed" ? "done" as const : item.status === "attention" ? "waiting" as const : "working" as const, detail: undefined as string | undefined }))).map((step, index) => (
            <span className={step.state} key={`${index}-${step.label}`}>
              <i>{step.state === "done" ? <Check size={10} /> : null}</i>
              <strong>{step.label}</strong>
              <em>
                {step.state === "done"
                  ? "已完成"
                  : step.state === "working"
                    ? (step.detail?.trim() || "进行中")
                    : "待推进"}
              </em>
            </span>
          ))}
        </div>
      </section>

      <div className="focus-detail-columns">
        <section className="focus-task-history">
          <header><div><History size={14} /><strong>{task ? "任务历史" : "今天的任务记录"}</strong></div><span>{historyItems.length} 条记录</span></header>
          <div className="focus-history-list">
            {historyItems.map((item) => (
              <div className={`focus-history-item ${item.kind}`} key={item.id}>
                <span className="focus-history-icon"><FocusHistoryIcon item={item} task={task} /></span>
                <div><strong>{item.title}</strong><p>{item.detail}</p><em>{item.time}</em></div>
              </div>
            ))}
            {historyItems.length === 0 && (
              <div className="focus-history-empty">暂无额外记录</div>
            )}
          </div>
        </section>

        <section className="focus-delivery-history">
          <header><div><FileArchive size={14} /><strong>{task?.deliveryState === "generating" ? "交付物进度" : "历史交付物"}</strong></div><span>{task?.deliveryState === "generating" ? "生成中" : `${historicalFileCount} 份`}</span></header>
          {historicalDeliveryTasks.length ? (
            <div className="focus-delivery-groups">
              {historicalDeliveryTasks.map((owner) => {
                const hasNew = owner.newDeliverables.length > 0;
                const stateCopy = owner.deliveryState === "saved"
                  ? "历史交付 · 已保存过成果库"
                  : hasNew
                    ? "含未确认的新交付物"
                    : owner.deliverables.length
                      ? "本会话历史交付物"
                      : "预计交付 · 正在生成";
                return (
                  <div className="focus-delivery-group" key={owner.id}>
                    {!task && <span className="focus-delivery-owner">{owner.shortTitle}</span>}
                    {owner.deliverables.map((file) => (
                      <button
                        type="button"
                        key={file}
                        onClick={() => onOpenArtifact(owner, file)}
                        aria-label={`查看历史交付物 ${file}`}
                      >
                        <span className="focus-file-preview" aria-hidden="true"><FileText size={15} /><i /><i /><i /></span>
                        <span className="focus-file-copy"><strong>{file}</strong><em>{stateCopy} · {owner.updated}</em><small>{deliveryFileDescription(file)}</small></span>
                        <ChevronRight size={15} />
                      </button>
                    ))}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="focus-delivery-empty"><TreasureVisual state="none" size="compact" /><p>{emptyDeliveryCopy}</p></div>
          )}
          {generatingTasks.map((owner) => (
            <div className="focus-pending-delivery" key={owner.id}><TreasureVisual state="generating" size="mini" /><span><strong>{owner.shortTitle}</strong><em>预计交付 {owner.deliverables.length} 份 · 生成中</em></span></div>
          ))}
        </section>
      </div>
    </div>
  );
}
