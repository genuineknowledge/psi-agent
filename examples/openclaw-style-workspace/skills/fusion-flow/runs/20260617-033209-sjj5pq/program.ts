// PRIMITIVES: pmap, session, agent, save, output, input
// SCENARIO: PostgreSQL MVCC 三机制并行调研——写入、读取、vacuum，合并为综合报告
// AUTHORED: 2026-06-17 03:30:00 by Fuclaw authoring mode from intent: "并行研究 PostgreSQL MVCC 的写入、读取、vacuum 三个机制"

import { run } from "../runtime/agent-flow-core.bundle.mjs";

await run(
  async ({ flow, save }) => {
    // researcher agent: 深度调研单个 MVCC 子机制
    const researcher = flow.agent({
      name: "mvcc_researcher",
      system: [
        "你是 PostgreSQL 内核专家，精通 MVCC（多版本并发控制）机制。",
        "给定一个具体机制，请深度调研并给出以下内容（使用中文）：",
        "",
        "## 1. 核心原理",
        "  用清晰的文字解释该机制在 PostgreSQL 中的工作原理，",
        "  涉及的关键数据结构（如 HeapTupleHeader、xmin/xmax、clog、visibility map 等）。",
        "",
        "## 2. 关键流程",
        "  逐步描述该机制的执行流程，尽可能详细。",
        "",
        "## 3. 重要参数与配置",
        "  列出与该机制相关的重要 GUC 参数及其含义和推荐值。",
        "",
        "## 4. 性能影响与优化",
        "  该机制对性能的影响，以及常见的优化手段。",
        "",
        "## 5. 常见问题与排查",
        "  该机制常见的故障场景和排查方法。",
        "",
        "## 6. 与其他数据库的对比",
        "  简要对比 MySQL（InnoDB）或 Oracle 在对应机制上的异同。",
        "",
        "要求：信息准确、有深度，不要泛泛而谈。每个部分都要有实质内容。",
      ].join("\n"),
      contextSchema: ["topic"] as const,
    });

    // 3 个主题
    const topics = [
      { key: "write", label: "写入（INSERT/UPDATE/DELETE）" },
      { key: "read", label: "读取（SELECT / 快照隔离）" },
      { key: "vacuum", label: "Vacuum（垃圾回收与空间管理）" },
    ];

    // 并行调研：pmap 对每个 topic 用同一个 researcher 分别调研
    const reports = await flow.pmap(
      topics,
      async (topic) => {
        const report = await flow.session(
          researcher,
          `请深度调研 PostgreSQL MVCC 中的「${topic.label}」机制。`,
          { topic: topic.label },
        );
        await save(`research-${topic.key}`, report);
        return { key: topic.key, label: topic.label, report };
      },
    );

    // 合并为综合报告
    const synthesizer = flow.agent({
      name: "synthesizer",
      system: [
        "你是技术报告撰写专家。你拿到 3 份独立的 PostgreSQL MVCC 子机制调研报告，",
        "需要将它们合并为一份结构清晰、逻辑连贯的综合报告。要求：",
        "",
        "## 最终报告结构（Markdown）：",
        "",
        "### 一、总览",
        "  PostgreSQL MVCC 的总体设计哲学，三机制之间的关系。",
        "",
        "### 二、写入机制",
        "  基于调研材料，提炼核心要点。",
        "",
        "### 三、读取机制",
        "  基于调研材料，提炼核心要点。",
        "",
        "### 四、Vacuum 机制",
        "  基于调研材料，提炼核心要点。",
        "",
        "### 五、三机制协同全景",
        "  写入→读取→vacuum 三者如何协同工作，形成完整的 MVCC 生命周期。",
        "  画一张 ASCII 流程图描述数据从写入到最终清理的全过程。",
        "",
        "### 六、最佳实践总结",
        "  基于三份调研，给出 5-8 条可操作的运维建议。",
        "",
        "风格：专业、有深度，面向有 PostgreSQL 使用经验的 DBA/开发者。",
        "不要简单拼接原文，要有提炼和串联。中文为主，术语保留英文。",
      ].join("\n"),
      contextSchema: ["write_report", "read_report", "vacuum_report"] as const,
    });

    const final = await flow.session(
      synthesizer,
      "请将以下三份 MVCC 子机制调研报告合并为一份综合报告。",
      {
        write_report: reports.find((r) => r.key === "write")?.report ?? "",
        read_report: reports.find((r) => r.key === "read")?.report ?? "",
        vacuum_report: reports.find((r) => r.key === "vacuum")?.report ?? "",
      },
    );

    await flow.output("final", final);

    console.log("\n========== PostgreSQL MVCC 综合报告 ==========\n");
    console.log(final);
    console.log("\n==============================================\n");
  },
  {
    programPath: new URL(import.meta.url).pathname.replace(
      /^\/([A-Za-z]):/,
      "$1",
    ),
  },
);
