// PRIMITIVES: agent, session, pmap, reduce, save, output, input
// SCENARIO: Stack Overflow 2024 不同编程语言开发者薪资分布并行分析
// AUTHORED: 2026-06-17 02:57:00 by Fuclaw authoring mode from intent: "并行分析 Stack Overflow 2024 不同编程语言开发者的薪资分布"

import { run } from "../runtime/agent-flow-core.bundle.mjs";

await run(
  async ({ flow, save }) => {
    const languagesRaw = await flow.input(
      "languages",
      [
        "Python",
        "JavaScript",
        "TypeScript",
        "Java",
        "C#",
        "Go",
        "Rust",
        "C++",
      ].join("\n"),
    );
    const languages = languagesRaw
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);

    // Researcher agent — 每个语言并行调研 Stack Overflow 2024 薪资数据
    const researcher = flow.agent({
      name: "researcher",
      system: [
        "你是 Stack Overflow 开发者调查数据分析师。",
        "基于 Stack Overflow 2024 Annual Developer Survey 数据，对指定编程语言给出薪资分析：",
        "",
        "## 薪资概览",
        "- 全球年收入中位数（USD）",
        "- 25th / 75th 百分位区间",
        "- 该语言开发者平均工作经验年数",
        "",
        "## 地区差异",
        "- 薪资最高的 3 个国家/地区及对应中位数",
        "",
        "## 职业维度",
        "- 使用该语言的 Top 3 职业角色及对应薪资",
        "",
        "## 关键洞察",
        "- 与上一年（2023）对比的趋势变化（涨/跌/持平）",
        "- 与其他语言的相对排名",
        "",
        "输出为结构化 markdown，数据不足时明确标注「数据不可得」，不编造数字。",
        "注：Stack Overflow 2024 Survey 实际样本约 65,000+ 开发者，薪资数据以 USD 报告。",
      ].join("\n"),
      contextSchema: ["language"] as const,
    });

    // pmap: 并行调研每个语言
    console.log(`\n🔍 并行调研 ${languages.length} 个编程语言...\n`);
    const reports = await flow.pmap(languages, async (language, index) => {
      const report = await flow.session(
        researcher,
        `请分析 Stack Overflow 2024 调查中「${language}」语言的开发者薪资分布。`,
        { language },
      );
      await save(`research-${language}`, report);
      console.log(`  ✅ [${index + 1}/${languages.length}] ${language} 完成`);
      return { language, report };
    });

    // Synthesizer agent — 汇总所有语言报告
    const synthesizer = flow.agent({
      name: "synthesizer",
      system: [
        "你是数据分析报告撰写人。拿到 N 个编程语言的 Stack Overflow 2024 薪资分析后，合并为一份完整对比报告。",
        "",
        "报告结构：",
        "",
        "## 1. 执行摘要（≤ 150 字）",
        "- 整体结论：哪个语言薪资最高/最低，哪个增长最快",
        "",
        "## 2. 薪资排行总表",
        "| 排名 | 语言 | 全球中位数(USD) | 25th 百分位 | 75th 百分位 | 同比趋势 | Top 地区 |",
        "按中位数降序排列",
        "",
        "## 3. 地区薪资热力图（文字描述）",
        "- 哪个地区对哪些语言给薪最高",
        "",
        "## 4. 职业角色 × 语言交叉分析",
        "- 哪些语言+角色组合薪资最高",
        "",
        "## 5. 关键趋势与洞察（≤ 5 条）",
        "- 2023→2024 变化方向",
        "- 新兴/衰退语言信号",
        "- 对开发者的 actionable 建议",
        "",
        "## 6. 数据说明",
        "- 数据来源：Stack Overflow 2024 Annual Developer Survey",
        "- 样本量、统计口径说明",
        "",
        "要求：数据驱动，对比清晰，结论有据。",
      ].join("\n"),
      contextSchema: ["reports_text"] as const,
    });

    // reduce: 将各语言报告合并后交给 synthesizer 出总报告
    const reportsText = reports
      .map((r) => `## ${r.language}\n\n${r.report}`)
      .join("\n\n---\n\n");

    const final = await flow.session(
      synthesizer,
      "请基于以下各语言的 Stack Overflow 2024 薪资分析，生成完整对比报告。",
      { reports_text: reportsText },
    );

    await flow.output("final", final);
    console.log("\n========== 最终报告 ==========\n");
    console.log(final);
    console.log("\n===============================\n");
  },
  {
    programPath: new URL(import.meta.url).pathname.replace(
      /^\/([A-Za-z]):/,
      "$1",
    ),
  },
);
