// PRIMITIVES: agent, session, parallel, save, output, input
// SCENARIO: 并行评测 6 大主流 LLM 在代码生成、数学推理、多语言理解三个维度的表现
// AUTHORED: 2026-06-17 03:05:00 by Fuclaw authoring mode from intent: "并行评测主流 LLM 在代码生成、数学推理、多语言理解三个维度的表现"

import { run } from "../runtime/agent-flow-core.bundle.mjs";

const MODELS = [
  "GPT-4o (OpenAI)",
  "Claude 3.5 Sonnet (Anthropic)",
  "Gemini 1.5 Pro (Google)",
  "DeepSeek-V3 (DeepSeek)",
  "Qwen2.5-72B (Alibaba)",
  "Llama 3.1-405B (Meta)",
] as const;

const DIMENSIONS = [
  "代码生成 (Code Generation)",
  "数学推理 (Mathematical Reasoning)",
  "多语言理解 (Multilingual Understanding)",
] as const;

await run(
  async ({ flow, save }) => {
    // --- Phase 1: declare researcher agent (one agent, reused for each model) ---
    const researcher = flow.agent({
      name: "researcher",
      system: [
        "你是 LLM 性能评测分析师。请对一个指定的大语言模型在以下三个维度进行深度评测：",
        "",
        "## 1. 代码生成 (Code Generation)",
        "- 在 HumanEval、MBPP、SWE-bench 等主流基准上的得分（列出具体数字）",
        "- 对 Python、JavaScript、C++、Go、Rust 等语言的代码质量",
        "- 代码补全、debugging、refactoring 能力评价",
        "- 与同类模型的横向对比位置",
        "",
        "## 2. 数学推理 (Mathematical Reasoning)",
        "- 在 MATH、GSM8K、MMLU-Math 等基准上的得分",
        "- 对代数、几何、微积分、概率统计的覆盖能力",
        "- Chain-of-Thought / 逐步推理的准确性",
        "- 与同类模型的横向对比位置",
        "",
        "## 3. 多语言理解 (Multilingual Understanding)",
        "- 在 MMLU (多语言子集)、FLORES、XQuAD 等基准上的表现",
        "- 支持的语言数量及主要语种的 fluency 水平",
        "- 跨语言翻译、代码切换、文化适配能力",
        "- 与同类模型的横向对比位置",
        "",
        "输出格式：",
        "- 每个维度以 ## 维度名 开头，2-4 句精炼概括，包含具体基准分数",
        "- 信息不足时标注「数据有限/暂未公开」，不编造数字",
        "- 末尾给一个综合一句话总结",
        "- 给一个综合推荐分（1-10 分）",
      ].join("\n"),
      contextSchema: ["model"] as const,
    });

    const synthesizer = flow.agent({
      name: "synthesizer",
      system: [
        "你是 LLM 技术评测报告撰写人。拿到 6 个主流大语言模型在 3 个维度的评测结果后，生成一份综合对比报告。",
        "",
        "报告结构（纯 Markdown）：",
        "",
        "## 总览对比矩阵",
        "| 模型 | 代码生成 | 数学推理 | 多语言理解 | 综合推荐分 | 一句话评价 |",
        "用 Markdown 表格，把 6 个模型在 3 个维度的关键基准分数和表现精炼填入。",
        "",
        "## 逐维度深度分析",
        "### 代码生成",
        "对比各模型在 HumanEval/MBPP/SWE-bench 等基准上的排名和差异，指出各自优势。",
        "### 数学推理",
        "对比各模型在 MATH/GSM8K 等基准上的表现，指出 CoT 能力差异。",
        "### 多语言理解",
        "对比各模型支持的语言数、低资源语言表现、翻译质量等。",
        "",
        "## 综合排名与选型建议",
        "按综合推荐分降序排列，给出以下场景的最佳选择（表格）：",
        "- 编程助手 / Copilot 场景",
        "- 数学 / 科研辅助场景",
        "- 多语言翻译 / 国际化场景",
        "- 通用全能 / 性价比场景",
        "- 开源 / 本地部署场景",
        "",
        "## 关键趋势",
        "- 开源 vs 闭源模型差距变化",
        "- 推理能力（CoT/思考链）成为新战场",
        "- 多模态融合趋势",
        "",
        "格式：纯 Markdown，适合直接保存为 .md。不要用代码块包裹报告内容。",
      ].join("\n"),
      contextSchema: ["all_reports"] as const,
    });

    // --- Phase 2: 6 researchers in parallel ---
    console.log(`\n🔍 并行评测 ${MODELS.length} 个主流 LLM（${DIMENSIONS.length} 个维度）...\n`);
    const reports: string[] = await flow.parallel(
      MODELS.map((model) => async () => {
        const ctx: Record<"model", string> = { model };
        const report = await flow.session(
          researcher,
          `请对 **${model}** 在代码生成、数学推理、多语言理解三个维度上进行深度评测。`,
          ctx,
        );
        const safeName = model.replace(/[^a-zA-Z0-9_-]/g, "_");
        await save(`research-${safeName}`, report);
        console.log(`  ✅ ${model} 评测完成`);
        return report;
      }),
    );

    // --- Phase 3: merge all reports ---
    const allReportsCombined = MODELS.map((model, i) =>
      `## ${model}\n\n${reports[i]}\n`
    ).join("\n---\n\n");

    const finalReport = await flow.session(
      synthesizer,
      "请将以下 6 个模型的独立评测结果整合成一份综合对比报告。",
      { all_reports: allReportsCombined },
    );

    await flow.output("final", finalReport);

    console.log("\n========== 最终评测报告 ==========\n");
    console.log(finalReport);
    console.log("\n==================================\n");
  },
  {
    programPath: new URL(import.meta.url).pathname.replace(
      /^\/([A-Za-z]):/,
      "$1",
    ),
  },
);
