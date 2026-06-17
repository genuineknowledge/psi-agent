- User: sby
- Project: psi-agent
- Preference: concise answers

- 2026-06-16: 对6大Agent框架（LangGraph/AutoGen/CrewAI/Dify/Coze/MetaGPT）进行了深度对比调研，报告已存为 research_report_agent_frameworks.md
- 期望使用 "fusion-flow" 工具进行并行任务编排（本次未成功，改为手动串联执行）
- AutoGen已进入维护模式，官方推荐迁移至 Microsoft Agent Framework (MAF)
- 2026-06-16: fusion-flow 基础设施（Fuclaw core、Node.js/tsx 运行时）在本 workspace 不可用。并行调研任务回退到手动 web_fetch（GitHub raw READMEs + 官方文档站）。此模式已沉淀为 technology-research skill。
- research_report_agent_frameworks.md 已生成，包含 6 框架 × 6 维度对比。

- 2026-06-17: 对6大深度学习框架（PyTorch/TensorFlow/JAX/MXNet/PaddlePaddle/MindSpore）进行了深度对比调研，报告已存为 research_report_dl_frameworks.md。fusion-flow 运行时 @agent-flow/core 未安装，回退到并行 web_fetch 模式。web_search 仍为 mock。

- 2026-06-17: Stack Overflow 2024 各编程语言薪资分布分析完成，报告存为 stackoverflow_2024_salary_by_language.md。fusion-flow 运行时 @agent-flow/core 未安装（npm 404），回退到并行 web_fetch 模式。web_search 仍为 mock。

- 2026-06-17: 对6大数据库（PostgreSQL/MySQL/SQLite/MongoDB/Redis/Cassandra）进行了高并发写入性能深度对比，报告已存为 research_report_db_write_performance.md。fusion-flow 运行时 @agent-flow/core 未安装（npm 404），回退到并行 web_fetch 模式。web_search 仍为 mock。

- 2026-06-17: 对6大AI应用框架（LangChain/LlamaIndex/AutoGen/CrewAI/Haystack/DSPy）进行了深度对比分析，报告已存为 research_report_ai_frameworks_6.md。AutoGen已进入维护模式，官方推荐迁移至Microsoft Agent Framework (MAF)。DSPy的自动Prompt优化范式独特，Shopify案例实现550x成本降低。fusion-flow运行时@agent-flow/core仍不可用，回退到并行web_fetch模式。

- 2026-06-17: 对6大前端框架（React/Vue/Angular/Svelte/SolidJS/Qwik）进行了深度生态对比，报告已存为 research_report_frontend_frameworks.md。fusion-flow 运行时 @agent-flow/core 不可用（npm 404），回退到并行 web_fetch 模式。web_search 仍为 mock。

- 2026-06-17: 对6大语言（Rust/Go/C++/Zig/D/Nim）内存安全机制进行了深度对比，报告已存为 research_report_memory_safety.md。fusion-flow 运行时 @agent-flow/core 不可用（npm 404），web_search 仍为 mock，web_fetch DNS 不可用，基于模型内置知识生成全量报告。

## Research Log

- 2026-06-17: 对6大云厂商（AWS/Azure/GCP/阿里云/腾讯云/华为云）定价策略进行了深度对比，报告存为 research_report_cloud_pricing.md。fusion-flow 运行时 @agent-flow/core 不可用，回退到并行 web_fetch。GCP 官网多次超时，部分内容使用模型内置知识补充。

- 2026-06-17: 对6大容器编排平台（Kubernetes/Docker Swarm/Nomad/Mesos/OpenShift/Rancher）进行了深度对比，报告已存为 research_report_orchestration_platforms.md。Apache Mesos 已于2022年退役。fusion-flow 运行时 @agent-flow/core 不可用，回退到并行 web_fetch 模式。- 2026-06-17: 对过去5年 Python 生态最热门10个库的 GitHub Star 趋势进行了深度分析，报告已存为 research_report_python_libraries_star_trends.md。fusion-flow 运行时 @agent-flow/core 不可用（npm 404），回退到并行 bash curl + GitHub API 模式（10个 repo 单次 batch 查询 ~8s）。web_search 仍为 mock。

- 2026-06-17: 对全球主要城市空气质量改善进行了深度调研，报告已存为 research_report_air_quality_improvement.md。Top 5: 北京(PM2.5 ↓63.5%)、墨西哥城(PM10 ↓75%)、首尔(PM2.5 ↓40%)、洛杉矶(臭氧 ↓90%)、伦敦(NO₂ ↓44%)。fusion-flow 运行时 @agent-flow/core 不可用，回退到并行 web_fetch。web_search 仍为 mock，多个外部站点 DNS/403 不可达，部分数据基于模型内置知识。

- 2026-06-17: 首次成功使用 fusion-flow (Fuclaw) 运行时进行了真正的并行编排——6 大主流 LLM（GPT-4o/Claude 3.5 Sonnet/Gemini 1.5 Pro/DeepSeek-V3/Qwen2.5-72B/Llama 3.1-405B）在代码生成、数学推理、多语言理解三个维度并行评测。运行时为 skills/fusion-flow/runtime/agent-flow-core.bundle.mjs，引擎为 claude (2.1.177)。报告已存为 research_report_llm_benchmark.md。fusion-flow 可用！之前「不可用」的结论是因为 npm 找不到 @agent-flow/core，但技能附带了 bundle 运行时可直接使用。