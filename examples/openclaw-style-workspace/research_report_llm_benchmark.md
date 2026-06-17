## 总览对比矩阵

| 模型 | 代码生成 | 数学推理 | 多语言理解 | 综合推荐分 | 一句话评价 |
| --- | --- | --- | --- | --- | --- |
| Claude 3.5 Sonnet | HumanEval ~92.0%，SWE-bench Verified ~49%（10月升级版），agentic 编码最强 | GSM8K ~96.4%，MATH ~71.1%（升级版~78%），CoT 稳健 | MGSM ~91.6%，主流语种接近母语，官方量化数据稀缺 | 8.5 | 工程级编码与数学双优的闭源标杆，多语言数据偏少 |
| GPT-4o | HumanEval ~90.2%，MBPP 80%+，SWE-bench 偏弱(~30-33%) | MATH ~76.6%，MMLU ~88.7%，GSM8K 社区~90%+ | 50+ 语言，新 tokenizer 降本，综合居前列 | 8.5 | 通用能力最均衡的旗舰，短板在 agentic 编码 |
| DeepSeek-V3 | HumanEval-Mul ~82.6%，SWE-bench Verified ~42%，开源第一梯队 | MATH-500 ~90.2%，GSM8K ~89.3%，蒸馏 R1 推理强 | MMMLU ~79%，中英双语最强，小众语言偏弱 | 8.5 | 数学与代码双强的开源性价比旗舰 |
| Qwen2.5-72B | HumanEval ~86.6，MBPP ~88.2，LiveCodeBench ~55.5 | GSM8K ~95.8，MATH ~83.1，开源领先 | 29+ 语言，MMMLU 70+，中英突出 | 8.5 | 数学+中英多语言突出的开源全能型 |
| Llama 3.1-405B | HumanEval ~89.0，MBPP EvalPlus ~88.6，SWE-bench 未公开 | GSM8K ~96.8，MATH ~73.8，MMLU ~88.6 | 官方 8 语种，MGSM ~91.6，MMLU(译) ~75.9 | 8.5 | 数学突出、补全扎实的开源旗舰，广语种偏窄 |
| Gemini 1.5 Pro | HumanEval ~71.9%(初版)，Natural2Code ~77.7%，长上下文优势 | MATH 58.5%→67%+，GSM8K ~91.7%，MMLU ~81.9% | MGSM ~88.7%，100+ 语言，长上下文低资源适配独特 | 8.0 | 超长上下文+多语言见长，代码略逊同期最强 |

注：表中分数取自各模型官方技术报告或 model card；SWE-bench、GSM8K、FLORES/XQuAD 分语种等受版本与评测设置影响较大，部分为社区复现或区间估计，正式引用前建议核对最新官方资料。

## 逐维度深度分析

### 代码生成

在补全类基准（HumanEval/MBPP）上，闭源与开源旗舰已高度接近：Claude 3.5 Sonnet（~92.0%）与 GPT-4o（~90.2%）、Llama 3.1-405B（HumanEval ~89.0、MBPP ~88.6）构成第一梯队，Qwen2.5-72B（~86.6）紧随其后，DeepSeek-V3（HumanEval-Mul ~82.6%）因采用多语言版本数字略低但同样可靠，Gemini 1.5 Pro 初版（~71.9%）相对落后、后续快照有提升。

真正拉开差距的是 agentic 编码（SWE-bench Verified，仓库级多文件修复）。这里 Claude 3.5 Sonnet 升级版以 ~49% 显著领先，DeepSeek-V3（~42%）是开源阵营中最强的可量化项，而 GPT-4o 在无外部 scaffolding 时仅约 30%–33%，明显偏弱。Qwen2.5-72B、Llama 3.1-405B、Gemini 1.5 Pro 的 SWE-bench 官方数据缺失，但社区共识是仓库级修复均弱于专门优化模型。

共性规律：所有模型在 Python/JavaScript 上质量最稳，C++/Go/Rust 等系统语言生成可用但需更多人工校验；单文件实现 vs 跨文件工程修改之间存在普遍鸿沟。各自优势上，Claude 主打 agentic 编码，GPT-4o 胜在通用补全与 debugging，DeepSeek 是开源里 SWE-bench 最实在的选择，Gemini 借助 1M-2M token 上下文在大型代码库理解与跨文件 refactoring 上有独特价值。

### 数学推理

数学是本轮整体水位最高的维度。基础应用题（GSM8K）几乎都进入 90%+：Llama 3.1-405B（~96.8）与 Claude 3.5 Sonnet（~96.4）、Qwen2.5-72B（~95.8）领跑，Gemini 1.5 Pro（~91.7）、GPT-4o（社区 ~90%+）、DeepSeek-V3（~89.3）次之。

竞赛级难题（MATH）才是分水岭：DeepSeek-V3 凭借 MATH-500 ~90.2% 表现亮眼（注意 MATH-500 与全量 MATH 口径不同），Qwen2.5-72B（~83.1）紧随，GPT-4o（~76.6%）、Llama 3.1-405B（~73.8）、Claude 3.5 Sonnet（~71.1%，升级版约 78%）处于同级，Gemini 1.5 Pro（58.5%→67%+）随版本迭代改善明显但仍偏后。

CoT 能力差异：DeepSeek-V3 蒸馏自 R1 系列，长链推理在开源中最稳；各模型在代数与概率统计上 CoT 较可靠，普遍弱点集中在几何（需空间想象）与多步微积分/证明，长链推导中段的计算偏差是共同失分点。所有模型在最难的竞赛/科研级推理上仍落后于 o1/o3 等专门强化推理的模型。

### 多语言理解

这一维度官方量化披露最不充分，需谨慎解读。按语言广度看，Gemini 1.5 Pro（100+ 语言）最广，GPT-4o（50+）次之，Qwen2.5-72B（官方 29+）、Llama 3.1-405B（官方 8 种）覆盖更集中。

跨语言推理（MGSM）上，Claude 3.5 Sonnet 与 Llama 3.1-405B 同为 ~91.6%，Gemini 1.5 Pro ~88.7%，水平相近。多语言 MMLU 维度，DeepSeek-V3（MMMLU ~79%）、Llama 3.1-405B（翻译版 ~75.9）、Qwen2.5-72B（70+ 区间）可比。

低资源语言与翻译质量上，Gemini 1.5 Pro 的标志能力是用超长上下文做「上下文内学习」——例如喂入整本语法书来翻译 Kalamang 这类极低资源语言，这是其独有优势。中英双语场景中，DeepSeek-V3 与 Qwen2.5-72B 表现最突出，最适合中文母语用户。共性短板是低资源语言（部分非洲、东南亚语种）的 fluency 与文化适配明显下降。FLORES、XQuAD 的逐项官方分数普遍未系统公开，横向精确对比证据不足。

## 综合排名与选型建议

综合推荐分降序：Claude 3.5 Sonnet、GPT-4o、DeepSeek-V3、Qwen2.5-72B、Llama 3.1-405B 同为 8.5（按维度侧重排序），Gemini 1.5 Pro 8.0。五个 8.5 分模型实力接近，差异主要体现在场景适配而非绝对高低。

| 场景 | 最佳选择 | 理由 |
| --- | --- | --- |
| 编程助手 / Copilot | Claude 3.5 Sonnet | SWE-bench Verified ~49% 领先，agentic 多文件修复与单文件实现双强 |
| 数学 / 科研辅助 | DeepSeek-V3（开源）/ Qwen2.5-72B（备选） | MATH-500 ~90.2% 与 MATH ~83.1% 领先，CoT 稳健，性价比高 |
| 多语言翻译 / 国际化 | Gemini 1.5 Pro | 100+ 语言 + 长上下文低资源语言适配，覆盖最广 |
| 通用全能 / 性价比 | GPT-4o | 代码/数学/多语言三维均衡，生态成熟，综合体验最稳 |
| 开源 / 本地部署 | DeepSeek-V3 / Qwen2.5-72B | 开源阵营综合领先；DeepSeek 数学+代码双强，Qwen 中英多语言+数学突出 |

补充：若部署算力受限，Qwen2.5-72B 比 671B MoE 的 DeepSeek-V3 更易落地；若中文场景为主，DeepSeek-V3 与 Qwen2.5-72B 优于多数闭源模型。

## 关键趋势

开源 vs 闭源差距持续收窄。本轮 5 款旗舰中 3 款（DeepSeek-V3、Qwen2.5-72B、Llama 3.1-405B）为开源，在补全类编码、GSM8K、MATH 等可量化维度已逼近甚至局部反超闭源（如 DeepSeek-V3 的 MATH-500、SWE-bench 在开源中突出）。剩余差距主要集中在最难的 agentic 工程编码与广语种覆盖，而非基础能力。

推理能力（CoT/思考链）成为新战场。DeepSeek-V3 蒸馏自 R1、各家普遍引用 CoT 口径分数，说明逐步推理的稳定性已是核心竞争点。同时所有通用模型都坦承落后于 o1/o3 这类专门强化推理的系列，预示下一阶段竞争将从「通用旗舰」转向「推理优化变体」，长链推导中段的计算可靠性是待攻克的共同瓶颈。

多模态与长上下文融合趋势。GPT-4o（原生多模态）、Gemini 1.5 Pro（1M-2M token 长上下文）代表了两条融合路径：前者强调模态统一，后者用超长上下文实现「上下文内学习」低资源语言这类新能力。本报告聚焦文本三维度，但多模态与超长上下文正成为旗舰模型的标配竞争项，未来评测维度需相应扩展。

数据可信度说明：本报告所有分数整合自各模型官方技术报告与 model card，时间跨度为 2024 年中至 2024 年底；不同版本快照、few-shot/CoT 设置会带来波动，未公开项已如实标注、未做推算。如需当期精确榜单，建议核对 LMSYS、Open LLM Leaderboard 等第三方记录。