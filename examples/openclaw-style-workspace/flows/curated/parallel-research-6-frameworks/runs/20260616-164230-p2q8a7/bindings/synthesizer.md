## 总览对比矩阵

| 维度 | LangGraph | AutoGen | CrewAI | Dify | Coze | MetaGPT |
|------|-----------|---------|--------|------|------|---------|
| 架构与核心抽象 | 有状态有向图，节点+边+共享 State，显式支持环 | 三层架构，事件驱动 Actor 模型，全异步可分布式 | 角色化 Crew（自治）+ Flows（确定性），独立轻量内核 | 低代码平台，App + 节点式有向图工作流，YAML DSL | 可视化低代码平台，拖拽画布，底层基于 Eino | 角色化（Role/Action/Message/Env）+ SOP 固化 |
| 多智能体协作 | 共享状态+路由跳转，全模式可表达，强可控 | 多 Agent 对话为核心，群聊/选择/Swarm/层次化 | Process 驱动（sequential/hierarchical），偏流程编排 | 较弱，靠 Workflow 节点串联，无群聊原语 | 多 Agent 模式即状态机路由，偏确定性流程 | 发布-订阅消息池，SOP 串行流水线 |
| 工具/插件生态 | 复用 LangChain 海量集成，支持 MCP | function calling + autogen-ext，已支持 MCP | crewai-tools 数十款，企业版工具市场，支持 MCP | 内置数十款 + 1.0 插件 Marketplace，支持 MCP | 插件商店数百款，背靠字节，支持 MCP | @register_tool 注册，无插件市场，MCP 较新 |
| 部署与生产 | LangGraph Platform（SaaS/自托管），持久化+断点续跑最成熟 | 开源自托管为主，OpenTelemetry+gRPC 分布式，无官方 SaaS | 自托管 + CrewAI Enterprise/AMP，观测走集成 | 部署形态最全（Docker/K8s/Cloud），多租户+企业版 | SaaS + Coze Studio 自托管，多渠道一键发布，Coze Loop 观测 | 自托管为主 + MGX SaaS，原生观测较弱 |
| 社区与文档 | Stars 万级、迭代周级、官方课程，但文档易漂移 | Stars 4 万+、文档 0.4 后改善，但版本/AG2 分裂 | Stars 30k+、文档清晰、DeepLearning.AI 短课、已融资 | Stars 9 万+、中英双语完善、教程模板丰富 | 字节背书、Studio 开源后增长快、中文资源丰富 | Stars 4 万+、ICLR 2024 Oral、学术影响力突出 |
| LLM 集成 | 复用 LangChain 适配层，几乎全覆盖，一行换模型 | 统一 model client，Azure 一等支持 | LiteLLM 统一适配，覆盖广，可按 Agent 配模型 | 自有供应商适配层，国内外模型全，UI 即切换 | Eino ChatModel 抽象，国内豆包为主+国产模型 | config2.yaml 配置切换，国内外+本地模型 |

## 逐框架深度分析

### LangGraph

架构与核心抽象：由 LangChain Inc. 推出的底层编排框架，把 Agent 应用建模为有状态的图——节点（函数/可调用对象）通过普通边或条件边连接，且显式支持环，天然适配 ReAct 循环。执行模型受 Google Pregel 消息传递启发，所有节点共享类型化的中央 State（TypedDict 或 Pydantic），更新通过 reducer 合并。核心抽象含 StateGraph、Command、子图，并提供函数式 API（@entrypoint/@task）。

多智能体协作：统一表达为「多节点共享状态 + 路由跳转」，顺序、并行、层次、网状模式均可表达。官方提供 langgraph-supervisor 与 langgraph-swarm 两个高层库，交接通过 Command(goto, update) 原语完成。内建一等公民级的 human-in-the-loop（interrupt 暂停）与时间旅行（回到任意 checkpoint 重放），是其协作可控性上的独特优势。

工具/插件生态：不重复造轮子，直接复用 LangChain 数百个工具与集成。工具调用通过 ToolNode 与 bind_tools() 完成，自定义用 @tool 装饰器。MCP 支持通过官方 langchain-mcp-adapters 提供。无专属插件市场，复用 LangChain 集成目录。

部署与生产：商业化最重的一环。LangGraph Platform 提供 SaaS 托管与自托管两种形态，底层 LangGraph Server 基于 FastAPI 可 Docker/K8s 部署。生产能力含持久化与断点续跑（Postgres/SQLite/Redis checkpointer）、durable execution、流式输出、cron、水平扩展。可观测性走 LangSmith，调试用 LangGraph Studio。企业版提供 SLA、SSO、私有部署。

社区与文档：Stars 约一至两万量级，背靠 LangChain 生态社区基数大，发布接近周级。文档完善但因 API 演进快常被诟病与版本漂移、示例易过时。LangChain Academy 提供免费 LangGraph 课程，社区渠道有 Discord、论坛、GitHub Discussions。

LLM 集成：完全复用 LangChain 的 BaseChatModel 统一抽象，几乎覆盖所有主流提供商（OpenAI、Anthropic、Gemini/Vertex、Bedrock、Azure、Cohere、Mistral 等）。init_chat_model("provider:model") 一行换模型，图结构无需改动。本地模型通过 Ollama、vLLM、HF、LM Studio 支持，模型无关性几无短板。

### AutoGen

架构与核心抽象：经历 2024 年底 0.2→0.4 架构重写，原始创建者另起 AG2 分叉，本报告以 Microsoft/AutoGen（0.4+）为主。0.4 采用三层分离：autogen-core（事件驱动 Actor 运行时，agent 作为独立 actor 通过异步消息和 pub/sub 通信）、autogen-agentchat（高层会话式 API）、autogen-ext（模型客户端/工具/集成）。保留 AssistantAgent、UserProxyAgent、Team 抽象，编程模型转向全异步、可分布式，支持 Python 与 .NET 双语言核心。

多智能体协作：核心定位即「多 Agent 对话」，是其最强项。0.4 提供 RoundRobinGroupChat、SelectorGroupChat（LLM 动态选发言者）、Swarm（handoff 交接）、MagenticOneGroupChat（编排器层次化协作）。消息以会话上下文共享，支持嵌套/顺序对话与可组合终止条件。整体偏对话/群聊范式，事件驱动表达力强，但不像 LangGraph 以显式图/状态机为一等公民。

工具/插件生态：工具通过 LLM function calling 实现，FunctionTool 包装 Python 函数即可，门槛低。内置代码执行（本地/Docker 沙箱）、MultimodalWebSurfer、文件浏览等，配合 Magentic-One 形成通用 agent 工具组。原生支持 MCP，提供 LangChain 工具适配器。无独立插件市场，生态依赖 autogen-ext，规模中等。

部署与生产：开源库（MIT），自托管为主，无官方托管 SaaS。0.4 引入 OpenTelemetry tracing 与 gRPC 分布式 worker runtime，可跨进程/跨机扩展，是相对 0.2 的关键生产化改进。AutoGen Studio 提供低代码原型与可视化调试。无独立企业版，但有微软背书与 Azure 加持；断点续跑/持久化需借状态保存接口自行实现。

社区与文档：头部热门项目，Stars 约 4 万+，更新活跃。文档在 0.4 后质量明显提升，含教程、迁移指南、API 参考，有 Discord 与配套研究论文。需特别注意微软 AutoGen 与 AG2 分叉并存，教程、Stars、社区讨论分散在两套体系，是当前最大认知成本。

LLM 集成：通过统一 model client 适配层，0.4 提供 OpenAIChatCompletionClient、AzureOpenAIChatCompletionClient、AnthropicChatCompletionClient 及 Gemini、Ollama 等，切换基本只需替换 client。本地模型经 Ollama/LM Studio/OpenAI 兼容端点接入，内置 token 计数与缓存。对 Azure OpenAI 的一等支持是差异点。

### CrewAI

架构与核心抽象：以「角色扮演」为核心隐喻的 Python 多智能体编排框架（创始人 João Moura）。早期建于 LangChain 之上，v0.30 前后重写为完全独立的轻量内核。两层范式：Crew（Agent+Task+Process 的自治协作单元，Agent 由 role/goal/backstory 定义）与 2024 年引入的 Flows（@start/@listen/@router 装饰器的事件驱动编排，带显式状态对象）。理念是「Crew 管自治、Flow 管确定性」，两者可嵌套，编程偏声明式（支持 YAML 定义）。

多智能体协作：主要通过 Process 表达——sequential（任务按序流转）和 hierarchical（自动/指定 manager agent 分解委派校验）。Agent 间支持 delegation 与 ask-question 互调，状态通过 task context 和 Flow 共享 state 传递。相比 AutoGen 自由群聊，CrewAI 更结构化、偏流程编排，辩论/群聊需自行用 Flow 拼装，灵活度中等但确定性更强。

工具/插件生态：官方 crewai-tools 内置数十个工具，覆盖 Web 搜索、抓取/爬取、RAG、文件代码、数据库、SaaS 连接器等。支持 BaseTool 子类或 @tool 自定义，兼容复用 LangChain 工具。已支持 MCP（MCPServerAdapter 接入 stdio/SSE）。生态处第一梯队，企业版提供工具市场。

部署与生产：可自托管（纯 Python 易容器化），同时提供 CrewAI Enterprise/AMP 商业平台，含一键部署、可视化 UI、版本追踪、团队协作。可观测性不内置，走集成路线（AgentOps、Langtrace、Langfuse、OpenLIT、MLflow、W&B）。容错有任务级重试、guardrails、human-in-the-loop，但断点续跑依赖 Flow 自管 state，长流程检查点不如 LangGraph 成熟。

社区与文档：社区活跃，Stars 截至 2025 年约 30k+，迭代频繁。官方文档结构清晰、概念+示例齐全，DeepLearning.AI 与 João Moura 合作两门免费短课。有活跃论坛，已获约 1800 万美元 A 轮融资，公司化运营支撑。

LLM 集成：底层通过 LiteLLM 统一适配，天然支持 OpenAI、Anthropic、Gemini、Azure、Bedrock、Groq、Mistral 等。切换便捷，LLM(model="...") 赋给 Agent 即可，可为不同 Agent 配不同模型。本地模型经 Ollama/LM Studio/OpenAI 兼容端点接入，模型替换几乎零代码改动。

### Dify

架构与核心抽象：严格说更像 LLMOps/LLM 应用开发平台而非纯多智能体编排框架。前后端分离：后端 Python/Flask + Celery，前端 Next.js，存储 PostgreSQL，向量检索默认 Weaviate（也支持 Qdrant/Milvus/pgvector）。核心抽象围绕 App，分聊天助手、文本生成、Agent、工作流（Chatflow/Workflow）四类。工作流以节点+有向图为编程模型，节点含 LLM、知识检索、问题分类、条件分支、迭代、代码执行、工具调用等，可导出 YAML DSL。整体声明式/低代码。

多智能体协作：相对薄弱。原生 Agent 应用基于单 Agent 的 Function Calling 或 ReAct 循环，无群聊、辩论、层次化主管-下属原语。多步协作靠 Workflow 节点串联（迭代、条件、并行分支、子工作流）实现流水线/有限并行，状态通过节点变量传递。真正的多 Agent 自由对话需自行用工具节点或外部编排拼装，以流程编排为主、自主协商为辅。

工具/插件生态：内置数十款工具（联网搜索、DALL·E/SD、Wikipedia、各类 API）。1.0 版本（2025 年初）最大变化是引入插件体系和官方 Marketplace，把模型供应商和工具解耦为可独立安装插件。自定义工具支持 OpenAPI/Swagger 或自定义 API 接入。1.x 已加入 MCP 支持，既可作 Client 消费外部 Server，也可把 Dify 应用暴露为 MCP 服务。国内开源中生态丰富，但工具数量级不及 LangChain 系。

部署与生产：部署方式齐全，主推 Docker Compose 一键自托管，提供 Kubernetes（Helm Chart）方案，及 Dify Cloud SaaS。可观测性原生提供日志、标注、运行追踪，支持对接 LangSmith、Langfuse、OpenLLMetry/Phoenix。具备会话记录、API 限流、多租户，提供企业版（SSO、细粒度权限、可扩展部署）。容错/断点续跑相对基础，长流程恢复粒度不如专门工作流引擎。

社区与文档：GitHub 上最受欢迎的 AI 应用类项目之一，Stars 约 9 万+ 量级，贡献者众多、迭代活跃、发布周到双周级。文档质量好且中英双语完善（中文用户显著优势），配大量教程、用例库、模板应用。社区以 GitHub Discussions、Discord 为主，国内微信社群活跃，属第一梯队。

LLM 集成：模型支持面非常广，OpenAI、Claude、Azure、Gemini、Bedrock 及国内通义/文心/智谱 GLM/Moonshot/DeepSeek 等。本地模型通过 Ollama、Xinference、LocalAI、OpenLLM、LM Studio 接入，私有化友好。统一模型供应商适配层（1.0 后以插件提供），UI 即可切换，应用内可按节点指定不同模型。

### Coze

架构与核心抽象：字节跳动的一套产品，含 SaaS 平台（coze.com / 国内「扣子」coze.cn）、2025 年 7 月开源的 Coze Studio（自托管）、配套 Coze Loop（观测/评测）。本质是可视化低代码/无代码 Agent 开发平台，编程以拖拽画布为主。核心抽象含 Bot/智能体、Workflow、Plugin、Knowledge、Memory、Card、Trigger。智能体分单 Agent 与多 Agent 模式。Coze Studio 后端 Golang 微服务、前端 React/TS，底层编排基于字节自家的 Eino，DDD 设计，Apache 2.0。

多智能体协作：主要通过多 Agent 模式——多个子 Agent 作为节点用条件跳转连接，本质是控制流驱动的有限状态机/有向图（类 supervisor 路由）。Workflow 内可串并联多个 LLM 节点、子工作流。状态共享依赖全局变量和数据库节点，消息以节点输入输出传递。整体偏确定性流程编排，擅长可控任务分解与路由，但自由辩论、群聊式涌现协作表达力较弱。

工具/插件生态：内置插件商店提供数百个官方与第三方插件，是强项之一。自定义工具支持三种：导入 OpenAPI/已有 API、内置 IDE 写代码（Python/Node.js）、封装为工作流。MCP 方面国内「扣子」2025 年已支持接入 MCP Server 并能暴露自身能力，版本支持程度有差异。生态在国内同类领先，背靠字节流量入口（抖音、飞书）。

部署与生产：形态多样，SaaS 直接用 coze.com/coze.cn；自托管走 Coze Studio，官方提供 Docker Compose 一键部署。发布渠道覆盖 API、Web/Chat SDK、Discord、Telegram、飞书/Lark、微信等。可观测性由独立开源的 Coze Loop 承担（tracing、Prompt 管理、eval）。企业级能力主要通过火山引擎商业版提供。容错/断点续跑公开信息少，自托管 HA 需自行验证。

社区与文档：背靠字节，迭代频繁。Coze Studio 自 2025 年 7 月开源后 Stars 增长快（初期即达万级量级）。官方文档较完整，中英双语，配大量图文教程；国内「扣子」中文教程、案例、第三方课程丰富。社区含 Discord 及国内开发者社群，资源充裕。

LLM 集成：国际版支持 GPT、Claude、Gemini 等；国内「扣子」以字节自家豆包（Doubao/Skylark）为主，接入 Kimi、DeepSeek 等国产模型。开源 Coze Studio 通过 YAML 配置层接入 OpenAI、Claude、火山方舟、豆包、DeepSeek、Qwen、Ollama 本地模型等。SaaS 端下拉切换，自托管端改配置，统一适配层依托 Eino 的 ChatModel 抽象。

### MetaGPT

架构与核心抽象：核心理念是把人类软件公司的 SOP 编码进多智能体系统，让一行需求自动展开为 PRD、设计文档、任务拆分和代码。编程模型围绕四个抽象：Role（ProductManager/Architect/Engineer/QAEngineer 等）、Action（角色思考后执行的最小单元）、Message（结构化消息）、Environment（承载共享消息池）。每个 Role 遵循 _observe→_think→_act 循环，通过 watch 订阅消息类型决定激活，Team 编排多角色推进流程。整体是「角色驱动+消息订阅+SOP 固化」分层设计，而非显式图编排。

多智能体协作：核心是共享的发布-订阅消息池——角色把 Message 发布到 Environment，其他角色按订阅类型自动拾取，实现解耦的事件驱动协作。默认模式是 SOP 串行流水线（产品经理→架构师→项目经理→工程师→QA），强调结构化通信而非自由对话，消息带明确来源、内容、目标，减少闲聊噪声。引入「可执行反馈」机制让工程师角色根据真实执行结果迭代代码。表达偏固定流程协作，自由辩论/群聊支持不如 AutoGen 直接。

工具/插件生态：通过 @register_tool 装饰器注册函数为工具，配工具推荐机制（尤其 Data Interpreter 场景按任务自动检索编排）。内置工具覆盖网页浏览、搜索、代码执行、文件操作等，质量高但数量不算大而全，丰富度弱于 LangChain。MCP 支持较新仍在演进，成熟度信息有限。支持自定义工具但无插件市场，扩展靠代码注册而非可视化生态。

部署与生产：自托管为主，pip install metagpt 或 Docker 镜像，本地/服务器运行；团队推出商业化 MGX（mgx.dev）作为 SaaS 形态多智能体协作平台。可观测性自带运行日志和 token 成本统计，但原生 tracing/metrics 较薄弱，生产监控通常需自行接入第三方。容错与断点续跑存在但非主打——定位偏自动化软件生成/研究原型而非重交易型生产工作流，缺少官方明确的企业版 SLA 公开承诺。

社区与文档：星标最高的 AI Agent 框架之一，Stars 约 4 万+，由 DeepWisdom 团队（geekan 等）主导。学术影响力突出，论文被 ICLR 2024 接收（Oral）。官方文档覆盖快速上手、角色自定义、Data Interpreter，质量中上；社区有 Discord 和微信群，更新活跃，但教程系统性与第三方课程丰富度略逊于 LangChain/LlamaIndex 等老牌生态。

LLM 集成：提供统一 LLM Provider 适配层，通过 config2.yaml 配置切换模型，支持 OpenAI、Azure、Claude、Gemini、智谱/通义等国内外提供商及 Ollama/OpenAI 兼容接口接入本地开源模型。切换主要改配置中 api_type 和密钥，无需改业务代码。适配层统一对话、嵌入调用，支持多模型混用（不同角色用不同模型）。

## 综合选型建议

| 典型场景 | 推荐框架 | 理由 |
|----------|----------|------|
| 快速搭建 LLM 应用 | Dify / CrewAI | Dify 低代码可视化把 RAG+Agent+工作流开箱即用，落地最快；CrewAI 代码侧上手最快、角色模型直觉强，适合开发者快速起步。 |
| 零代码/非开发者使用 | Coze / Dify | Coze 拖拽画布+插件商店+多渠道一键发布，非工程背景也能上线；Dify 可视化界面与中英双语文档对非开发团队同样友好。 |
| 复杂多 Agent 协作（精细控制） | LangGraph | 有状态图+显式环+断点续跑+时间旅行+人在回路，对复杂可控、可恢复工作流提供一等公民级支持，控制粒度最细。 |
| 角色扮演 Agent 团队 | CrewAI | role/goal/backstory 的角色化建模最贴近真实团队分工，sequential/hierarchical 流程清晰，是该范式的代表框架。 |
| 软件自动生成 | MetaGPT | 把软件公司 SOP 固化进角色流水线，一句需求自动产出 PRD、设计、代码，并带可执行反馈迭代，为此场景量身定制。 |
| 企业级生产部署 | LangGraph / Dify | LangGraph Platform 持久化、durable execution、LangSmith 观测最成熟；Dify 部署形态齐全、多租户+企业版 SSO，交付完整。 |
| 学术研究/实验 | AutoGen / MetaGPT | AutoGen 事件驱动群聊范式灵活、研究论文支撑强，便于探索协作机制；MetaGPT 有 ICLR Oral 学术背书，适合多智能体研究复现。 |

## 关键差异与趋势

范式分野——「代码框架」vs「低代码平台」。LangGraph、AutoGen、CrewAI、MetaGPT 是面向开发者的代码优先框架，控制粒度与灵活度高；Dify、Coze 是可视化低代码/无代码平台，重交付与易用性。选型第一刀往往是问「使用者是不是开发者」，而非比较哪个更强。

协作哲学的两条路线。一条是确定性流程编排（LangGraph 的图、CrewAI 的 Process、Dify/Coze 的工作流节点、MetaGPT 的 SOP 流水线），强调可控、可复现；另一条是涌现式自由对话（AutoGen 的群聊/选择/Swarm），强调灵活与协作表达力。LangGraph 通过显式状态机在可控性上独树一帜，AutoGen 则在开放式多 Agent 对话上最强。

MCP 已成事实标准。六个框架几乎全部接入了 Model Context Protocol——LangGraph、AutoGen、CrewAI、Dify 均原生或官方适配，Coze、MetaGPT 也在跟进（成熟度有差异）。MCP 正在把「工具/数据源接入」从各家私有插件体系收敛为统一协议，工具生态的护城河正在被拉平，框架竞争重心转向编排能力与生产化。值得关注的还有 A2A（Agent-to-Agent）互操作的早期探索，方向是让不同框架的 Agent 能够互相调用。

商业化普遍走「开源内核 + 托管平台」双轨。LangGraph Platform、CrewAI Enterprise/AMP、Dify Cloud/Enterprise、Coze SaaS、MetaGPT 的 MGX 都是开源框架配商业托管的组合；AutoGen 是少数纯开源、依赖 Azure 生态的例外。生产化能力（持久化、断点续跑、观测、多租户）正成为差异化关键，其中 LangGraph 在 durable execution 上领先，Dify/Coze 在部署形态与发布渠道上最全。

模型无关性已是基线。所有框架都通过统一适配层（LangChain BaseChatModel、LiteLLM、各家 model client、YAML 配置）实现近乎零代码的模型切换，几乎全面覆盖 OpenAI/Anthropic/Gemini/Azure/Bedrock 与国产模型，并普遍支持 Ollama 等本地部署。模型选择不再是框架的约束点。

版本分裂与维护迁移的认知成本。AutoGen 的 0.2→0.4 重写叠加 AG2 分叉，是当前最突出的选型陷阱，文档、Stars、社区分散在两套体系；CrewAI 也经历了从依赖 LangChain 到独立内核的迁移。选型时务必明确所指版本与维护状态。

可观测性的两种取向。LangGraph（LangSmith）、Coze（Coze Loop）走「自有配套观测平台」路线；AutoGen（OpenTelemetry）、CrewAI、Dify、MetaGPT 走「对接第三方 tracing」路线。OpenTelemetry 正逐步成为 Agent 观测的通用底座。

国内外生态分野。Dify、Coze 在中英双语文档、国产模型（豆包、通义、智谱、DeepSeek 等）支持上对中文团队更友好；LangGraph、AutoGen、CrewAI、MetaGPT 则以英文生态与国际模型为主（MetaGPT、Dify、Coze 对国产模型也有良好覆盖）。落地区域与模型合规要求会显著影响选型。