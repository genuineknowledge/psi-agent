// PRIMITIVES: parallel, session, agent, save, output
// SCENARIO: 并行调查 6 大开源项目的 Issue 解决周期和 PR 合并率，产出排名对比报告
// AUTHORED: 2026-06-17 03:08:00 by Fuclaw authoring mode from intent: "并行调查主流开源项目的 issue 解决周期和 PR 合并率"

import { run } from "../runtime/agent-flow-core.bundle.mjs";

await run(
  async ({ flow, save }) => {
    const projects = [
      { name: "Kubernetes", repo: "kubernetes/kubernetes" },
      { name: "React", repo: "facebook/react" },
      { name: "VS Code", repo: "microsoft/vscode" },
      { name: "TensorFlow", repo: "tensorflow/tensorflow" },
      { name: "Rust", repo: "rust-lang/rust" },
      { name: "Linux Kernel", repo: "torvalds/linux" },
    ];

    // Researcher agent: 每个项目共用同一个 agent 定义
    const researcher = flow.agent({
      name: "researcher",
      system: [
        "你是开源社区分析师。针对给定项目，基于你的知识输出一份结构化分析报告。",
        "",
        "## 输出格式（Markdown）",
        "",
        "### 1. Issue 解决周期",
        "- 平均解决时间（估算，说明依据）",
        "- 典型 issue 生命周期（从提交到关闭的流程）",
        "- 是否有 SLA 或响应时间承诺",
        "- 长期未解决的 issue 占比（估算）",
        "",
        "### 2. PR 合并率",
        "- 估算的 PR 合并率（merged / total closed PRs）",
        "- 典型 PR 审查周期（从提交到合并）",
        "- 审查流程特点（需要几个 reviewer、是否有 CI 门槛等）",
        "- 被拒绝/关闭的 PR 主要原因",
        "",
        "### 3. 社区治理特点",
        "- 维护者团队规模和工作方式",
        "- 是否有定期发布周期",
        "- 社区贡献者 onboarding 难度",
        "",
        "### 4. 数据来源说明",
        "- 说明你的估算依据（公开报告、社区讨论、已知统计数据等）",
        "- 明确指出哪些是精确数据、哪些是估算",
        "",
        "要求：信息不足时诚实说明，不编造数据。用中文回答。",
      ].join("\n"),
    });

    // 6 个项目并行研究
    const results = await flow.parallel(
      projects.map((p) => async () => {
        const report = await flow.session(
          researcher,
          `请分析 ${p.name} (${p.repo}) 的 Issue 解决周期和 PR 合并率。`,
        );
        await save(`research-${p.name}`, report);
        return { project: p.name, repo: p.repo, report };
      }),
    );

    // Synthesizer: 合并 6 份报告 → 一份综合对比
    const synthesizer = flow.agent({
      name: "synthesizer",
      system: [
        "你是开源社区对比分析师。基于各项目的独立研究报告，产出综合对比报告。",
        "",
        "## 输出格式",
        "",
        "### 🏆 综合排名表",
        "| 排名 | 项目 | Issue 解决周期 | PR 合并率 | PR 审查速度 | 社区响应 | 综合评级 |",
        "|------|------|---------------|----------|------------|---------|---------|",
        "（每个维度用 ★★★☆☆ 打分）",
        "",
        "### 📊 关键数据速览",
        "用简洁的对比表列出每个项目的核心数字（解决周期、合并率、审查周期）",
        "",
        "### 💡 关键发现（3-5 条）",
        "- 跨项目洞察",
        "",
        "### 🏅 最佳实践总结",
        "- 哪些项目在哪方面做得最好？",
        "- 对其他开源项目的启示",
        "",
        "### ⚠️ 数据可信度",
        "- 各项目数据可靠性（精确统计 / 公开发布 / 估算 / 推测）",
        "",
        "用中文回答，≤ 1000 字。格式整洁、便于阅读。",
      ].join("\n"),
      contextSchema: ["reports"] as const,
    });

    const reportsCombined = results
      .map((r) => `## ${r.project} (${r.repo})\n\n${r.report}`)
      .join("\n\n---\n\n");

    const final = await flow.session(
      synthesizer,
      "基于以下各项目的研究报告，产出综合对比分析报告。",
      { reports: reportsCombined },
    );

    await flow.output("final", final);

    console.log("\n========== Final Report ==========\n");
    console.log(final);
    console.log("\n==================================\n");
  },
  {
    programPath: new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"),
  },
);
