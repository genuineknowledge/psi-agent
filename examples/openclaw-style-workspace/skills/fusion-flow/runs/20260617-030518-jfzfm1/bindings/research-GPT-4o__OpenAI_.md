## 代码生成 (Code Generation)

GPT-4o 在 HumanEval 上约为 90.2%（OpenAI 官方数据），属第一梯队；MBPP 公开数据有限，社区复现多在 80%+ 区间。SWE-bench 表现明显偏弱，无外部 scaffolding 时约在 30%–33%（数据来源差异较大，建议视为参考值）。Python/JavaScript 代码质量与补全能力出色，Go/Rust 略逊于 Python；debugging 与小范围 refactoring 可靠，但跨多文件的工程级修改是短板。横向对比：优于多数同代模型，但在 agentic 编码（SWE-bench）上落后于 Claude 3.5 Sonnet。

## 数学推理 (Mathematical Reasoning)

MATH 约 76.6%、MMLU 约 88.7%（OpenAI 公布）；GSM8K 官方未在 4o 发布中单列，社区报告普遍在 90%+（数据有限，谨慎引用）。代数与概率统计覆盖扎实，几何（尤其需空间想象）和多步微积分易出错。Chain-of-Thought 逐步推理总体准确，但长链推导中段计算偏差是主要失分点。横向对比：处于强势位置，弱于专门强化推理的 o1/o3 系列。

## 多语言理解 (Multilingual Understanding)

GPT-4o 的核心卖点之一是多语言提升，新 tokenizer 显著降低非英语 token 消耗。多语言 MMLU 子集在主流语种上较 GPT-4 Turbo 有明显进步（具体分语种数字暂未系统公开，数据有限）。FLORES、XQuAD 的官方逐项分数未公开。支持 50+ 语言，英/中/西/法/德等高资源语种 fluency 接近母语水平，低资源语种与文化适配仍有差距；翻译与代码切换（code-switching）表现稳健。横向对比：多语言综合能力居前列，与 Gemini 1.5 系列各有胜负。

---

综合一句话总结：GPT-4o 是一款多语言与通用代码/数学能力均衡且强劲的旗舰模型，弱点集中在 agentic 工程编码和高难度多步推理。

综合推荐分：8.5 / 10

注：以上 SWE-bench、GSM8K 及分语种多语言分数受版本与评测设置影响较大，部分为社区复现或区间估计，正式引用前建议核对 OpenAI 最新官方 model card。