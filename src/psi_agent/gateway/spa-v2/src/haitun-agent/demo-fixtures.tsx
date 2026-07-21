import { FileText, Grid2X2, Layers3, MessageCircle, Search, Zap } from "lucide-react";
import {
  PENDING_LABEL,
  type ChatMessage,
  type InboxItem,
  type Task,
  type TaskTemplate,
} from "./model";

export const INITIAL_TASKS: Task[] = [
  {
    id: "launch-brief",
    title: "完成 HaiTun Agent 灰度发布方案",
    shortTitle: "灰度发布方案",
    category: "产品发布",
    summary:
      "核心框架与用户分层已完成，正在等待您确认首批邀请名单；确认后将自动生成执行排期与邀约物料。",
    progress: 72,
    status: "attention",
    statusLabel: PENDING_LABEL,
    eta: "确认后约 18 分钟完成",
    updated: "8 分钟前",
    accent: "#ff6b57",
    deliverables: ["灰度发布方案.docx", "首批用户名单.xlsx"],
    deliveryState: "generating",
    steps: [
      { label: "整理发布目标与成功指标", state: "done" },
      { label: "筛选首批体验用户", state: "working" },
      { label: "生成邀约与反馈回收物料", state: "waiting" },
    ],
  },
  {
    id: "market-weekly",
    title: "汇总本周 Agent 市场情报",
    shortTitle: "市场情报周报",
    category: "市场研究",
    summary:
      "已核验 26 个公开信号并形成 7 条有效判断。任务已经完成，3 份交付物已生成并可查看。",
    progress: 100,
    status: "completed",
    statusLabel: "已完成",
    eta: "已完成",
    updated: "今天 09:42",
    accent: "#d8a62a",
    deliverables: ["Agent 市场情报周报.pdf", "竞品动态证据表.xlsx", "领导摘要.docx"],
    deliveryState: "ready",
    steps: [
      { label: "采集并去重公开信息", state: "done" },
      { label: "核验来源并区分事实与推断", state: "done" },
      { label: "生成周报与领导摘要", state: "done" },
    ],
  },
  {
    id: "feedback-study",
    title: "分析首轮灰度用户反馈",
    shortTitle: "用户反馈分析",
    category: "用户研究",
    summary:
      "32 份反馈已完成清洗与聚类。Agent 正在对“首次任务成功率”和“中途接管原因”进行交叉分析。",
    progress: 46,
    status: "working",
    statusLabel: "分析中",
    eta: "预计 16:30 完成",
    updated: "刚刚更新",
    accent: "#007bff",
    deliverables: ["阶段性洞察摘要.pdf"],
    deliveryState: "ready",
    steps: [
      { label: "清洗问卷与访谈记录", state: "done" },
      { label: "聚类痛点与信任断点", state: "working" },
      { label: "形成可验证的产品问题", state: "waiting" },
    ],
  },
  {
    id: "meeting-followup",
    title: "同步产品例会行动项",
    shortTitle: "例会行动项同步",
    category: "会议协作",
    summary: "8 个行动项已同步到项目台账并通知负责人。本任务以系统内状态更新为结果，已经完成且无需生成文件。",
    progress: 100,
    status: "completed",
    statusLabel: "已完成",
    eta: "已完成",
    updated: "今天 10:18",
    accent: "#27a06b",
    deliverables: [],
    deliveryState: "none",
    steps: [
      { label: "提取会议结论与行动项", state: "done" },
      { label: "同步负责人和截止时间", state: "done" },
      { label: "发送任务提醒", state: "done" },
    ],
  },
  {
    id: "competitor-watch",
    title: "持续监测重点 Agent 产品动态",
    shortTitle: "竞品动态监测",
    category: "持续任务",
    summary:
      "正在监测 OpenClaw、Hermes 等 12 个重点信号源。今天已排除 18 条重复或低可信信息，暂无高优新增事项。",
    progress: 64,
    status: "continuous",
    statusLabel: "持续运行",
    eta: "下次巡检 14:30",
    updated: "3 分钟前巡检",
    accent: "#4d8eff",
    deliverables: [],
    deliveryState: "none",
    steps: [
      { label: "官方产品与发布页", state: "working" },
      { label: "开源仓库与社区动态", state: "working" },
      { label: "高价值信号触发提醒", state: "waiting" },
    ],
  },
];

export const INITIAL_MESSAGES: Record<string, ChatMessage[]> = {
  overview: [
    { role: "agent", text: "今天有 1 个事项需要您确认，另有 2 个任务产生了新交付物。" },
  ],
  "launch-brief": [
    { role: "agent", text: "首批名单已收敛到 12 人。您确认后，我会继续完成后续物料。" },
  ],
  "market-weekly": [
    { role: "agent", text: "周报已完成。我把 3 条最值得领导关注的变化放在了第一页。" },
  ],
  "feedback-study": [
    { role: "agent", text: "目前最明显的信号是：用户知道目标，但不清楚 Agent 为什么停下来。阶段性洞察摘要已经可以查看，任务仍在继续。" },
  ],
  "meeting-followup": [
    { role: "agent", text: "行动项已经同步到项目台账并通知负责人。本任务已完成，不产生文件交付物。" },
  ],
  "competitor-watch": [
    { role: "agent", text: "监测正常。本轮没有达到推送阈值的新信号。" },
  ],
};

export const INITIAL_INBOX: InboxItem[] = [
  {
    id: "notice-launch",
    taskId: "launch-brief",
    title: "需要您确认首批邀请名单",
    detail: "确认后将继续生成执行排期与邀约物料。",
    kind: "attention",
    time: "8 分钟前",
    unread: true,
  },
  {
    id: "notice-feedback",
    taskId: "feedback-study",
    title: "阶段性洞察摘要已生成",
    detail: "任务仍在运行，您现在就可以查看阶段交付物。",
    kind: "delivery",
    time: "12 分钟前",
    unread: true,
  },
  {
    id: "notice-monitor",
    taskId: "competitor-watch",
    title: "持续监测完成一轮巡检",
    detail: "本轮没有达到推送阈值的新增信号。",
    kind: "update",
    time: "今天 09:30",
    unread: false,
  },
];

export const QUICK_ACTIONS = ["查看当前阻塞", "催一下进度", "先给我结论"];

export const INITIAL_TEMPLATES: TaskTemplate[] = [
  {
    id: "leader-brief",
    title: "领导汇报材料整理",
    category: "内容整理",
    description: "把会议记录、数据和零散材料整理成结论前置的管理层汇报。",
    starterPrompt: "请将我提供的材料整理为面向管理层的汇报，先给核心结论、关键事实、待决策事项和下一步。",
    deliverables: ["一页摘要.docx", "汇报提纲.pptx"],
    cadence: "一次性",
    icon: Layers3,
  },
  {
    id: "market-research",
    title: "市场与竞品情报研究",
    category: "深度研究",
    description: "核验公开来源，区分事实、推断与待验证问题。",
    starterPrompt: "请研究以下市场或竞品，核验公开来源，并输出竞争格局、能力对比和证据链接表：",
    deliverables: ["市场竞争简报.pdf", "证据链接表.xlsx"],
    cadence: "一次性",
    icon: Search,
  },
  {
    id: "meeting-actions",
    title: "会议纪要转执行清单",
    category: "会议协作",
    description: "提取决定、负责人、截止时间、依赖项和风险。",
    starterPrompt: "请整理这份会议材料，输出会议结论、行动项、负责人、截止时间和仍待确认的问题。",
    deliverables: ["会议纪要.docx", "行动项清单.xlsx"],
    cadence: "一次性",
    icon: MessageCircle,
  },
  {
    id: "feedback-insight",
    title: "用户反馈分析",
    category: "用户研究",
    description: "聚类用户痛点、任务失败原因和中途接管原因。",
    starterPrompt: "请分析我提供的用户反馈，区分高频问题和关键问题，并形成可验证的产品问题清单。",
    deliverables: ["用户反馈洞察报告.pdf", "待验证问题.xlsx"],
    cadence: "一次性",
    icon: Grid2X2,
  },
  {
    id: "gray-release",
    title: "产品灰度发布方案",
    category: "产品发布",
    description: "设计目标、人群、规模、节奏、触发条件和反馈闭环。",
    starterPrompt: "请为以下产品设计小范围灰度发布方案，包括发布目标、首批用户、成功指标、执行节奏和反馈回收方式。",
    deliverables: ["灰度发布方案.docx", "首批用户名单.xlsx", "邀约话术.md"],
    cadence: "一次性",
    icon: FileText,
  },
  {
    id: "signal-watch",
    title: "重点产品持续监测",
    category: "持续任务",
    description: "按设定信号源持续巡检，没有高价值变化时不打扰。",
    starterPrompt: "请持续监测以下产品及信号源，仅在出现高价值新增变化时提醒我，并按周形成汇总：",
    deliverables: ["重点变化周报.pdf", "信号证据表.xlsx"],
    cadence: "每日巡检 / 每周汇总",
    icon: Zap,
  },
];

export const NEW_TASK_PRESETS = [
  { label: "做一份领导汇报", prompt: "请帮我整理一份面向管理层的汇报，材料包括：", category: "内容整理", icon: Layers3 },
  { label: "研究市场或竞品", prompt: "请研究以下市场或竞品，核验公开来源并给出证据：", category: "深度研究", icon: Search },
  { label: "整理会议与行动项", prompt: "请把以下会议材料整理为结论和行动项：", category: "会议协作", icon: MessageCircle },
  { label: "分析文件或用户反馈", prompt: "请分析我提供的文件或用户反馈，并形成可验证的问题清单：", category: "用户研究", icon: Grid2X2 },
  { label: "持续监测一个主题", prompt: "请持续监测以下主题，仅在出现高价值变化时提醒我：", category: "持续任务", icon: Zap },
];
