# 六大AI Agent框架深度调研报告

> 调研时间：2026-06-16 | 覆盖框架：LangGraph、AutoGen、CrewAI、Dify、Coze、MetaGPT

---

## 总览对比矩阵

| 维度 | LangGraph | AutoGen | CrewAI | Dify | Coze | MetaGPT |
|------|-----------|---------|--------|------|------|---------|
| **定位** | 低层编排框架 | 多Agent协作（维护模式→MAF） | 多Agent自动化框架 | LLM应用开发平台 | 零代码Agent构建平台 | 多Agent软件工程 |
| **GitHub Stars** | ~12k+ | ~38k+ | ~25k+ | ~65k+ | ~3k (JS SDK) | ~52k+ |
| **开发语言** | Python/JS | Python/.NET | Python | Python/TS | 平台 (JS/Python SDK) | Python |
| **许可证** | MIT | MIT | MIT | Dify Open Source (Apache 2.0+) | MIT | MIT |
| **维护方** | LangChain Inc | 社区维护 (原Microsoft Research) | CrewAI Inc | LangGenius | 字节跳动 | DeepWisdom |
| **生产就绪度** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐→MAF | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| **学习曲线** | 中高 | 中 | 低中 | 低 | 极低 | 中 |
| **零代码支持** | ❌ (需LangSmith Studio) | ✅ (AutoGen Studio) | ❌ | ✅ (可视化画布) | ✅ (核心体验) | ❌ |

---

## 1. LangGraph

### 架构设计与核心抽象

- **基于图的状态机模型**：受Google Pregel和Apache Beam启发，面向NetworkX风格的图API
- **节点（Node）+ 边（Edge）**：Node是计算单元（LLM调用/工具调用），Edge定义控制流（普通边/条件边）
- **StateGraph**：核心抽象，定义共享状态的Schema（TypedDict/Pydantic），所有节点读写同一状态
- **Durable Execution（持久执行）**：内置checkpointing，支持从任意节点断点续跑，适用于长时间运行的Agent
- **Human-in-the-Loop（人机协同）**：原生支持中断（interrupt）机制，可在任意节点暂停等待人工审核/修改状态
- **Memory体系**：短期工作记忆（当前会话Context）+ 长期持久记忆（跨会话），通过checkpointer实现
- **Deep Agents**：2025年推出的高层封装，支持子Agent、文件系统、规划能力

### 多智能体协作机制

- **SubGraph组合**：通过子图嵌套实现多Agent编排，每个子图是一个独立的状态机
- **Supervisor模式**：Supervisor Agent作为路由节点，根据条件分发到不同Worker子图
- **Hierarchical Agent Teams**：多层嵌套，每层有独立的supervisor
- **Map-Reduce并行模式**：Send API实现fan-out/fan-in，适合并行处理

### 工具/插件生态

- **LangChain生态集成**：直接复用LangChain的Tool/Toolkit体系（100+内置工具）
- **LangSmith**：官方可观测性平台，支持trace可视化、evaluation、deployment
- **MCP协议**：通过langchain-mcp-adapters支持
- **自定义工具**：简单的@tool装饰器即可定义

### 部署与生产可用性

- **LangSmith Deployment**：官方部署平台，针对长运行状态工作流设计
- **自托管**：可部署到任意Python环境，支持异步（asyncio）
- **LangGraph Cloud**：Serverless API部署，自动伸缩
- **Streaming**：原生支持token-level streaming
- **容错**：checkpointing + durable execution天然支持故障恢复

### 社区活跃度与文档质量

- **文档**：docs.langchain.com + reference.langchain.com，质量高但版本碎片化
- **教程**：LangChain Academy免费课程、YouTube大量社区内容
- **社区**：LangChain Forum活跃，但框架复杂度导致学习曲线偏高
- **更新频率**：LangChain Inc维护，发布节奏快（周级）

### 与主流LLM的集成方式

- 通过LangChain的ChatModel抽象层，支持OpenAI、Anthropic、Google、AWS Bedrock、Azure等
- 支持自定义LLM适配器（任何实现BaseChatModel的类）
- LangGraph本身与模型无关

---

## 2. AutoGen（→ Microsoft Agent Framework）

> ⚠️ **重要变更**：AutoGen已于2025年进入维护模式（Maintenance Mode），不接收新功能。官方推荐迁移至 [Microsoft Agent Framework](https://github.com/microsoft/agent-framework)。本报告覆盖AutoGen v0.4架构，并附MAF说明。

### 架构设计与核心抽象

- **分层架构**：
  - **Core API**：底层消息传递、事件驱动Agent、分布式Runtime（支持Python/.NET跨语言）
  - **AgentChat API**：高层API，提供预设Agent类型和Multi-Agent模式
  - **Extensions API**：可插拔扩展（LLM客户端、代码执行器等）
- **Agent抽象**：AssistantAgent（通用）、CodingAgent、UserProxyAgent等
- **AgentTool**：将Agent封装为Tool，实现Agent调用Agent的层次化协作
- **Event-Driven**：基于异步消息传递，支持actor模型

### 多智能体协作机制

- **Two-Agent Chat**：基础双Agent对话模式
- **GroupChat / SelectorGroupChat**：多Agent群聊，Selector决定下一步发言者
- **Swarm**：基于Tool的本地化Agent选择（Handoff模式），类似OpenAI Swarm
- **Magentic-One**：预置多Agent团队（Orchestrator + WebSurfer + Coder + FileSurfer + ComputerTerminal）
- **GraphFlow**：Workflow模式，DAG编排（类似LangGraph）

### 工具/插件生态

- **MCP原生集成**：MCPWorkbench支持多个MCP Server
- **Code Executor**：支持本地/Docker/Azure容器代码执行
- **AutoGen Studio**：零代码GUI原型设计工具
- **AutoGen Bench**：Agent性能评估基准

### 部署与生产可用性

- **分布式Runtime**：Core API支持gRPC分布式Agent通信
- **AutoGen Studio**：仅用于原型设计，不推荐生产使用
- **MAF（继任者）**：企业级生产支持，稳定API，跨Runtime互操作（A2A、MCP）
- **现有AutoGen**：社区维护，安全补丁和Bug修复继续，但无新功能

### 社区活跃度与文档质量

- **文档**：microsoft.github.io/autogen，结构化文档质量高
- **社区**：Discord活跃，但Maintenance Mode后活跃度下降
- **GitHub**：38k+ stars，学术引用多
- **迁移路径**：官方提供AutoGen→MAF迁移指南

### 与主流LLM的集成方式

- Extension API支持：OpenAI、Azure OpenAI、Anthropic、Ollama、Groq等
- OpenAIChatCompletionClient为核心，也可自定义ModelClient
- 支持多模型混合使用（不同Agent用不同模型）

---

## 3. CrewAI

### 架构设计与核心抽象

- **完全独立框架**：不依赖LangChain，从零构建
- **双层架构**：
  - **Crews**：自治Agent团队，角色扮演式协作
  - **Flows**：事件驱动的工作流引擎（生产级架构），精确状态管理
- **核心类**：
  - `Agent`：角色（Role）+ 目标（Goal）+ 背景故事（Backstory）+ 工具（Tools）
  - `Task`：描述 + 期望输出 + 指定Agent + 依赖关系
  - `Crew`：Agent集合 + Task集合 + Process（sequential/hierarchical）
  - `Flow`：事件驱动 + 状态管理 + 条件路由
- **Process类型**：
  - **Sequential**：任务按顺序执行
  - **Hierarchical**：Manager Agent自动分配和验证任务
- **YAML配置**：Agent/Task通过YAML声明式定义

### 多智能体协作机制

- **Sequential Process**：线性任务链，前一Task输出作为后一Task输入
- **Hierarchical Process**：Manager Agent自动规划、委托和验证
- **Flow中的Crew嵌套**：Flow控制整体流程，在需要自治能力的步骤中触发Crew
- **@listen / @router / @start装饰器**：Flow的事件驱动编排
- **or_ / and_** 条件组合：复杂触发逻辑

### 工具/插件生态

- **crewai-tools**：官方工具包（SerperDev、ScrapeWebsite、FileRead、DirectoryRead等）
- **自定义工具**：继承BaseTool，实现_run方法
- **MCP集成**：支持MCP Server作为工具使用，多种Transport（Stdio/SSE/Streamable HTTP）
- **CrewAI AMP**：企业套件（控制面板、可观测性、安全、24/7支持）
- **Coding Agent Skills**：为Claude Code/Cursor等提供的官方技能包
- **100+可观测性集成**：Langfuse、Arize Phoenix、Datadog、Braintrust等

### 部署与生产可用性

- **CrewAI AMP**：企业级部署方案，支持云部署和本地部署
- **Crew Control Plane**：集中管理平台（免费试用）
- **Checkpointing**：Flow支持状态持久化和恢复
- **Human-in-the-Loop**：支持人工审核和人工输入
- **Telemetry**：匿名遥测（可禁用），用于改进框架

### 社区活跃度与文档质量

- **文档**：docs.crewai.com，Mintlify搭建，质量好
- **教程**：DeepLearning.AI合作课程（Multi AI Agent Systems），10万+认证开发者
- **社区**：community.crewai.com活跃论坛
- **GitHub**：25k+ stars，增长迅速
- **更新频率**：活跃开发，版本迭代快（v1.14+）

### 与主流LLM的集成方式

- 默认OpenAI，支持任意LLM（Ollama、Anthropic、Groq、Azure、Together等）
- 支持LiteLLM集成（100+模型）
- 不同Agent可使用不同模型
- 支持本地模型（Ollama、LM Studio）

---

## 4. Dify

### 架构设计与核心抽象

- **全栈LLM应用开发平台**：前端（Next.js）+ 后端（Python/Flask）+ 数据库（PostgreSQL）
- **核心模块**：
  - **Workflow（工作流画布）**：可视化编排，节点包括LLM、知识检索、代码执行、条件分支等
  - **Agent**：基于Function Calling / ReAct的智能体
  - **RAG Pipeline**：完整的文档摄入→向量化→检索流程
  - **Prompt IDE**：提示词工程界面，支持A/B测试
  - **LLMOps**：应用日志、性能监控、标注系统
- **Backend-as-a-Service**：所有功能提供REST API

### 多智能体协作机制

- **Workflow编排**：通过可视化DAG实现多步骤协作
- **Agent节点**：在Workflow中嵌入Agent节点，支持Function Calling/ReAct推理
- **迭代（Iteration）节点**：支持循环逻辑
- **条件分支**：if/else路由
- **多Agent模式**：通过组合多个Agent节点 + 代码节点实现复杂Agent协作

### 工具/插件生态

- **50+内置工具**：Google Search、DALL·E、Stable Diffusion、WolframAlpha、HTTP请求等
- **插件市场（dify-plugins）**：社区贡献的模型运行时和工具
- **模型Provider**：100+ LLM集成（OpenAI、Anthropic、Google、本地模型等）
- **可观测性集成**：Opik、Langfuse、Arize Phoenix
- **第三方集成**：Slack、Discord、飞书、企业微信等发布渠道

### 部署与生产可用性

- **Docker Compose**：一键部署，最小4GB RAM
- **Kubernetes**：社区Helm Charts支持
- **Dify Cloud**：官方SaaS（免费套餐含200次GPT-4调用）
- **AWS/Azure/GCP**：Terraform/CDK一键部署方案
- **企业版**：Dify Premium（AWS Marketplace），支持自定义品牌
- **监控**：Grafana Dashboard，PostgreSQL数据源

### 社区活跃度与文档质量

- **GitHub**：65k+ stars，增长速度极快
- **文档**：docs.dify.ai，多语言支持（含中文），FAQ完善
- **社区**：Discord活跃、Reddit r/difyai、GitHub Discussions
- **贡献**：Linux Foundation项目，贡献者众多
- **更新频率**：极其活跃（月均数百commits）

### 与主流LLM的集成方式

- 原生支持100+模型提供商
- OpenAI API兼容协议的统一适配层
- 支持自托管模型（vLLM、Ollama、LocalAI等）
- 模型管理：统一配置、切换、A/B对比

---

## 5. Coze（扣子）

### 架构设计与核心抽象

- **字节跳动旗下零代码Agent构建平台**：以Web控制台为核心体验
- **Bot = Agent**：每个Bot是一个可发布的AI应用
- **核心组件**：
  - **Persona（人设）**：Prompt + 知识库定义Bot行为
  - **Skills（技能）**：插件/工作流/知识库的组合
  - **Workflow**：可视化拖拽工作流编排
  - **Knowledge**：文档知识库（支持多种格式）
  - **Variables**：对话变量和长期记忆
- **Realtime API**：实时语音/视频交互（WebSocket）

### 多智能体协作机制

- **Multi-Agent Mode**：在Bot内定义多个Agent角色
- **工作流串联**：通过Workflow将多个Bot/Agent串联
- **触发式协作**：基于条件/事件触发不同Agent响应
- **主从模式**：主控Agent根据用户意图路由到专业子Agent
- **跨Bot通信**：API调用实现Bot间数据传递

### 工具/插件生态

- **官方插件市场**：丰富的预置插件（搜索、图像生成、数据分析等）
- **自定义插件**：支持OpenAPI规范的API插件
- **Code Interpreter**：内置Python代码沙箱
- **知识库**：支持多种文档格式（PDF/Word/网页/飞书文档等）
- **多平台发布**：飞书、微信、抖音、Web、API等
- **SDK**：JavaScript/Node.js SDK（@coze/api），支持Taro/UniApp小程序

### 部署与生产可用性

- **SaaS优先**：核心平台为云服务（coze.cn / coze.com）
- **API渠道**：通过API SDK可在自建应用中集成
- **企业版**：提供企业级权限、数据隔离
- **发布渠道**：一键发布到飞书、微信、Web等多端

### 社区活跃度与文档质量

- **文档**：coze.com/docs，中英文文档，质量好
- **社区**：中文社区活跃（字节生态），国际版增长快
- **GitHub**：JS SDK仓库3k+ stars（平台本身非开源）
- **入门门槛**：极低，非开发者也可使用
- **生态**：与飞书/抖音深度集成，国内场景丰富

### 与主流LLM的集成方式

- Coze平台自带模型（字节自研豆包系列）
- 国际版支持OpenAI等第三方模型
- 模型切换在控制台一键完成
- 不同Agent/Workflow节点可指定不同模型

---

## 6. MetaGPT

### 架构设计与核心抽象

- **软件公司多Agent系统**：模拟软件公司的SOP流程
- **核心哲学**：`Code = SOP(Team)` — 将SOP标准操作流程编码为Agent协作协议
- **角色体系**：
  - Product Manager（产品经理）→ 生成PRD/用户故事
  - Architect（架构师）→ 设计系统架构
  - Project Manager（项目经理）→ 任务拆分和分配
  - Engineer（工程师）→ 编码实现
  - QA Engineer（测试工程师）→ 测试
- **消息流转**：基于Message类的结构化通信（Document/Diagram/Code等）
- **SOP驱动**：各角色按预定义的软件开发SOP依次执行
- **MGX（MetaGPT X）**：2025年发布的自然语言编程产品，全球首个AI Agent开发团队

### 多智能体协作机制

- **固定SOP流水线**：PM→Architect→PM→Engineer→QA的严格顺序
- **共享环境（Environment）**：所有Agent共享一个工作空间
- **结构化输出传递**：每个角色输出结构化文档（JSON/代码/图表）
- **Role机制**：每个Role有特定的_action和_think逻辑
- **学术创新**：
  - AFlow（ICLR 2025 Oral, top 1.8%）：自动化Agent工作流生成
  - SPO（Self-Play Optimization）：自博弈优化
  - AOT（Agent-Oriented Thinking）：面向Agent的推理

### 工具/插件生态

- **Data Interpreter**：数据分析Agent子项目
- **自定义Role**：继承Role类实现自定义角色
- **代码执行**：原生支持Python代码生成和执行
- **搜索工具**：SerpAPI、Google Search
- **学术工具包**：Research Agent、Debate Agent等

### 部署与生产可用性

- **CLI/库方式使用**：`metagpt "task description"`
- **HuggingFace Space**：在线Demo
- **Docker**：支持容器化部署
- **MGX**：www.mgx.dev 产品化方向
- **生产就绪度**：偏学术/原型，直接生产使用需额外工程化

### 社区活跃度与文档质量

- **GitHub**：52k+ stars，学术影响力大
- **论文**：ICLR 2024发表，学术引用广泛
- **文档**：docs.deepwisdom.ai，持续完善中
- **社区**：Discord活跃，中文社区强
- **学术产出**：持续产出论文（AFlow等）
- **更新**：活跃开发，向产品化方向演进（MGX）

### 与主流LLM的集成方式

- 通过YAML配置支持OpenAI、Azure、Ollama、Groq等
- 不同Role可以配置不同模型
- 支持本地模型
- 模型层抽象（LLMType枚举）

---

## 综合选型建议

### 按使用场景推荐

| 场景 | 推荐框架 | 理由 |
|------|---------|------|
| **快速搭建LLM应用（含RAG）** | Dify | 可视化 + 全栈 + API导出，3分钟上手 |
| **零代码/非开发者使用** | Coze / Dify | 两者皆有可视化界面，Coze更适合字节生态 |
| **复杂多Agent协作（需要精细控制）** | LangGraph | 最灵活的底层编排，状态管理最强 |
| **快速原型多Agent系统** | CrewAI | YAML配置 + 角色扮演，开发体验最好 |
| **软件自动生成** | MetaGPT | 独有SOP软件工程流程 |
| **企业级生产部署** | LangGraph + LangSmith / CrewAI AMP | 生产级基础设施 |
| **学术研究/实验** | AutoGen(→MAF) / MetaGPT | 论文多，架构思想先进 |

### 未来演进方向关注

1. **Microsoft Agent Framework（MAF）**：AutoGen的继任者，企业级承诺，需重点跟踪
2. **Deep Agents（LangGraph上层）**：简化开发体验的方向
3. **MetaGPT → MGX**：从框架到产品的转型
4. **MCP协议**：所有框架都在积极拥抱MCP，成为工具集成的通用标准

---

*本报告基于各框架官方文档、GitHub README及公开资料整理，数据截至2026年6月。*
