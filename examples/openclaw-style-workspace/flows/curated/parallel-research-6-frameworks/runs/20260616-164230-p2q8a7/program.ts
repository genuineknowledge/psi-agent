// PRIMITIVES: agent, session, parallel, reduce, save, output, input
// SCENARIO: 6 mainstream AI agent frameworks parallel research across 6 dimensions → merged comparison report
// AUTHORED: 2026-06-16 16:45:00 by Fuclaw authoring mode from intent: "并行调研 LangGraph/AutoGen/CrewAI/Dify/Coze/MetaGPT 六大框架，覆盖架构/协作/工具/部署/社区/LLM集成六个维度"

import { run } from "../../../skills/fusion-flow/runtime/agent-flow-core.bundle.mjs";

const FRAMEWORKS = ["LangGraph", "AutoGen", "CrewAI", "Dify", "Coze", "MetaGPT"] as const;

const DIMENSIONS = [
  "架构设计与核心抽象",
  "多智能体协作机制",
  "工具/插件生态",
  "部署与生产可用性",
  "社区活跃度与文档质量",
  "与主流LLM的集成方式",
] as const;

await run(
  async ({ flow, save }) => {
    // --- Phase 1: declare agents once ---
    const researcher = flow.agent({
      name: "researcher",
      system: [
        "你是 AI Agent 框架的技术调研分析师。请对一个指定框架进行深度调研，覆盖以下六个维度：",
        "",
        "1. **架构设计与核心抽象** — 框架的整体架构（分层/模块），核心抽象概念（Agent/Task/Graph/State/Flow 等），编程模型",
        "2. **多智能体协作机制** — 多 Agent 之间如何协作（顺序/并行/层次/辩论/群聊/事件驱动等），消息传递与状态共享方式，协作模式表达能力",
        "3. **工具/插件生态** — 内置工具数量与质量，第三方工具集成方式，是否支持自定义工具，MCP 协议支持情况，插件市场/生态丰富度",
        "4. **部署与生产可用性** — 部署方式（自托管/SaaS/Docker/K8s），可观测性（日志/tracing/metrics），容错/断点续跑，是否提供企业版",
        "5. **社区活跃度与文档质量** — GitHub Stars/活跃度，官方文档质量，教程/课程丰富度，社区渠道（Discord/Forum），更新发布频率",
        "6. **与主流LLM的集成方式** — 支持哪些模型提供商，模型切换是否便捷，是否支持本地模型，统一适配层设计",
        "",
        "输出要求：",
        "- 每个维度用 2-4 句话概括，给具体事实和数据（不凑数，信息不足就说明「信息公开较少」）",
        "- 用 Markdown 格式，以 ## 维度名 作为小标题",
        "- 最后给一个「一句话定位」总结这个框架最独特的价值",
      ].join("\n"),
      contextSchema: ["framework"] as const,
    });

    const synthesizer = flow.agent({
      name: "synthesizer",
      system: [
        "你是技术调研报告撰写人。收到 6 个框架各自在 6 个维度的调研结果后，请生成一份综合对比报告。",
        "",
        "报告结构：",
        "## 总览对比矩阵",
        "用 Markdown 表格列出 6 个框架在 6 个维度的各自一句话要点（尽量对仗，便于横向比较）。",
        "",
        "## 逐框架深度分析",
        "每个框架一个小节（## 框架名），按六个维度展开，保持原文关键事实但压缩冗余。",
        "",
        "## 综合选型建议",
        "按典型使用场景给出推荐框架（表格），每个场景 1-2 句理由。",
        "场景包括：快速搭建LLM应用、零代码/非开发者使用、复杂多Agent协作（精细控制）、角色扮演Agent团队、软件自动生成、企业级生产部署、学术研究/实验。",
        "",
        "## 关键差异与趋势",
        "跨框架横向对比，指出现阶段行业趋势（如 MCP 协议、A2A 互操作、维护模式迁移等）。",
        "",
        "格式：纯 Markdown，适合直接保存为 .md 文件。不要出现「```」代码块包裹。",
      ].join("\n"),
      contextSchema: ["all_reports"] as const,
    });

    // --- Phase 2: 6 researchers in parallel ---
    const reports: string[] = await flow.parallel(
      FRAMEWORKS.map((fw) => async () => {
        const ctx: Record<"framework", string> = { framework: fw };
        const report = await flow.session(
          researcher,
          `请对 **${fw}** 框架进行深度调研，覆盖全部六个维度。`,
          ctx,
        );
        await save(`research-${fw}`, report);
        return report;
      }),
    );

    // --- Phase 3: merge all reports ---
    const allReportsCombined = FRAMEWORKS.map((fw, i) =>
      `## ${fw}\n\n${reports[i]}\n`
    ).join("\n---\n\n");

    const finalReport = await flow.session(
      synthesizer,
      "请将以下 6 个框架的独立调研结果整合成一份综合对比报告。",
      { all_reports: allReportsCombined },
    );

    await flow.output("final", finalReport);

    console.log("\n========== Final Report ==========\n");
    console.log(finalReport);
    console.log("\n==================================\n");
  },
  {
    programPath: new URL(import.meta.url).pathname.replace(
      /^\/([A-Za-z]:)/,
      "$1",
    ),
    runsDir: new URL(
      "./runs",
      import.meta.url,
    ).pathname.replace(/^\/([A-Za-z]:)/, "$1"),
  },
);
