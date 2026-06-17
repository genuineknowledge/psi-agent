# LangGraph 框架深度调研

> 数据基准约为 2025 年末至 2026 年初；GitHub 数据、版本号等会随时间变化，标注的数值为量级参考。

## 架构设计与核心抽象

LangGraph 由 LangChain Inc. 推出，定位是一个**底层编排框架（low-level orchestration）**，把 Agent 应用建模为一张**有状态的图**：节点（node，本质是函数或可调用对象）通过边（edge）连接，边分为普通边与条件边（conditional edge），核心区别于多数框架的一点是它**显式支持环（cycle）**，因此天然适合 ReAct 式的「思考-行动」循环。其执行模型受 Google Pregel 的消息传递范式启发，所有节点共享一个**类型化的中央 State**（用 `TypedDict` 或 Pydantic 定义），状态更新通过 **reducer** 合并（例如 `add_messages` 用于追加消息）。主要抽象包括 `StateGraph`、`compile()` 后的可运行图、`Command`（同时表达「跳转 + 状态更新」）、子图（subgraph）；同时提供与 LCEL（Runnable 接口）的兼容，以及较新的**函数式 API**（`@entrypoint` / `@task` 装饰器）面向不想手写图结构的用户。

## 多智能体协作机制

LangGraph 把多 Agent 协作统一表达为「多个节点共享状态 + 通过路由跳转」，因此**顺序、并行、层次、网状（many-to-many）等模式都可表达**。官方提供两个高层库：`langgraph-supervisor`（监督者模式，由一个中心 Agent 分发任务）和 `langgraph-swarm`（去中心化的 handoff 群组）。Agent 间的「交接」通过 `Command(goto=..., update=...)` 这一原语完成，状态共享则依赖共享的图 State 或子图间的状态映射。它还内建一等公民级的 **human-in-the-loop**（通过 `interrupt` 暂停等待人工输入）和**时间旅行（time-travel，回到任意 checkpoint 重放）**，这是它在协作可控性上的独特优势。并行分支（fan-out/fan-in）通过同一步内激活多个节点实现。

## 工具/插件生态

LangGraph 本身不重复造工具轮子，而是**直接复用 LangChain 庞大的集成生态**——LangChain 拥有数百个工具与集成（搜索、数据库、向量库、API 等）。工具调用通过预制的 `ToolNode` 与模型的 `bind_tools()` 完成，自定义工具用 `@tool` 装饰器即可。**MCP（Model Context Protocol）支持**通过官方的 `langchain-mcp-adapters` 包提供，可把任意 MCP server 暴露的工具直接转成 LangChain 工具接入图中。生态丰富度上，依托 LangChain 母生态属于第一梯队，但严格意义上的「LangGraph 专属插件市场」并不存在，复用的是 LangChain 的集成目录。

## 部署与生产可用性

这是 LangGraph 商业化最重的一环。官方提供 **LangGraph Platform**（原 LangGraph Cloud），含 **SaaS 托管**与**自托管（self-hosted / hybrid）**两种形态，底层是 `LangGraph Server`（基于 FastAPI，可 Docker/K8s 部署）。生产能力包括**持久化与断点续跑**（Postgres / SQLite / Redis 等 checkpointer 后端）、**durable execution（崩溃后从断点恢复）**、流式输出、定时任务（cron）、以及队列与水平扩展。可观测性通过 **LangSmith** 做 tracing / 监控，**LangGraph Studio** 提供图的可视化调试与状态回放。企业版（Enterprise）提供 SLA、SSO、私有部署等，定价对企业客户单独商谈。

## 社区活跃度与文档质量

LangGraph 仓库的 GitHub Stars 约在**一万至两万量级**（远小于 LangChain 主仓库的十万级，但增长很快），背靠整个 LangChain 生态因而社区基数大、issue/PR 活跃、**发布频率高（接近周级别的版本迭代）**。官方文档相对完善且持续更新，但因 API 演进快，历史上常被诟病**文档与版本「漂移」、示例易过时**。教程资源充足：**LangChain Academy 提供免费的「Introduction to LangGraph」课程**，配套大量官方 how-to 与概念文档。社区渠道有 Discord、官方论坛及 GitHub Discussions。

## 与主流 LLM 的集成方式

LLM 接入完全复用 LangChain 的 `BaseChatModel` 统一抽象层，因此**几乎覆盖所有主流提供商**：OpenAI、Anthropic、Google（Gemini/Vertex）、AWS Bedrock、Azure OpenAI、Cohere、Mistral 等。模型切换非常便捷——`init_chat_model("provider:model")` 一行即可换模型，图结构无需改动。**本地模型**通过 Ollama、vLLM、Hugging Face、LM Studio 等集成支持。这种「框架只管编排、模型由 LangChain 适配层统一收口」的设计，使 LangGraph 在模型无关性（model-agnostic）上几乎没有短板。

---

**一句话定位**：LangGraph 是面向生产的**有状态、可循环的 Agent 编排引擎**——它把多智能体工作流建模为带持久化检查点的图，独特价值在于对「环、断点续跑、时间旅行、人在回路」的一等支持，让复杂、可控、可恢复的 Agent 系统从原型平滑走向生产。