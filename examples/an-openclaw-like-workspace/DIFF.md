# DIFF.md - OpenClaw-Like Workspace 与 OpenClaw 的差异

本文档描述 `examples/an-openclaw-like-workspace` 与真实 OpenClaw 的异同。

---

## 相同部分

以下功能与 OpenClaw 保持一致：

### 1. Bootstrap 文件结构

| 文件 | 说明 |
|------|------|
| AGENTS.md | 工作区配置和行为规则 |
| SOUL.md | Agent 人格和行为指导 |
| TOOLS.md | 本地笔记和环境特定配置 |
| IDENTITY.md | Agent 身份记录（名称、类型、风格、emoji） |
| USER.md | 用户档案信息 |
| BOOTSTRAP.md | 首次运行初始化流程 |
| HEARTBEAT.md | 周期性检查配置 |
| MEMORY.md | 长期记忆存储 |

### 2. System Prompt 核心部分

| 部分 | 说明 |
|------|------|
| 身份声明 | "You are a personal assistant running inside OpenClaw." |
| Tooling | 工具列表和描述 |
| Tool Call Style | 工具调用风格指导（核心原则部分） |
| Execution Bias | 执行偏好（行动导向、持续工作、需要证据） |
| Safety | 安全原则（不追求自我保护、优先安全） |
| Workspace | 工作目录信息 |
| Runtime | 运行时信息（host, os, arch, model, shell, Python版本） |
| Project Context | Bootstrap 文件加载和拼接 |
| Skills | 技能加载和使用指导 |
| Memory | 记忆系统指导 |
| Heartbeats | 心跳处理指导 |
| Silent Replies | 静默回复规则 |
| Current Date & Time | 当前日期和时间信息 |
| Prompt Cache Boundary | 缓存边界标记 |

### 3. 工具实现

| 工具 | 说明 |
|------|------|
| read | 异步文件读取 |
| write | 异步文件写入 |
| edit | 精确字符串替换 |
| bash | 异步 shell 命令执行（带超时） |

---

## 差异部分

以下功能与 OpenClaw 不同或未实现：

### 1. 未实现的 OpenClaw 特定功能

| 功能 | 原因 |
|------|------|
| OpenClaw CLI Quick Reference | psi-agent 不是 OpenClaw，没有这些 CLI 命令 |
| OpenClaw Self-Update | psi-agent 有自己的更新机制 |
| Model Aliases | psi-agent 有自己的模型管理 |
| Documentation Links | OpenClaw 文档与 psi-agent 无关 |

### 2. 未实现的通道/平台相关功能

| 功能 | 原因 |
|------|------|
| Messaging | psi-agent 有独立的 channel 组件处理消息路由 |
| Reactions | 由具体 channel 实现 |
| Voice (TTS) | 由具体 channel 或 skill 实现 |
| Assistant Output Directives | OpenClaw Web UI 特定功能 |
| Webchat Canvas | OpenClaw Web UI 特定功能 |
| Authorized Senders | psi-agent 有自己的权限机制 |

### 3. 未实现的高级功能

| 功能 | 原因 |
|------|------|
| Reasoning Format | 由模型自动处理 |
| Sandbox | psi-agent 没有沙箱功能 |
| Provider Contribution | psi-agent 有自己的 provider 机制 |
| Prompt Mode (full/minimal/none) | psi-agent 暂不支持 subagent |

### 4. 部分实现的功能

| 功能 | 差异说明 |
|------|----------|
| Tool Call Style | 仅保留核心原则（不叙述常规调用、保持简洁），去掉审批处理相关内容 |
| Runtime | 使用 Python 版本而非 Node.js 版本，不包含 channel 信息 |
| Silent Replies | 仅在 system prompt 中实现指导，session 和 channel 不实现 SILENT_TOKEN 处理 |
| Real Conversation Detection | 已实现：检测心跳/静默回复等非真实对话，避免无意义的 compaction |

### 5. 新增功能

| 功能 | 说明 |
|------|------|
| Real Conversation Detection | 新增 `has_meaningful_conversation_content()` 和 `is_real_conversation_message()` 函数，用于检测消息是否包含真正的对话内容。在 compaction 前检查，如果没有真实对话则跳过，节省 LLM 调用资源。 |

### 5. 架构差异

| 方面 | OpenClaw | OpenClaw-Like Workspace |
|------|----------|------------------------|
| 语言 | TypeScript/Node.js | Python |
| 运行时 | Node.js | Python 3.14+ |
| 异步框架 | 原生 Promise/async | anyio/asyncio |
| 配置管理 | OpenClaw 配置文件 | workspace 目录自包含 |
| 工具注册 | 动态加载插件 | tools/ 目录扫描 |

---

## Heartbeat 实现差异

| 方面 | OpenClaw | OpenClaw-Like Workspace |
|------|----------|------------------------|
| 触发方式 | 外部心跳轮询 | schedules/ 目录中的定时任务 |
| 频率 | 可配置 | 固定 30 分钟 |
| 响应 | HEARTBEAT_OK 或警报文本 | 读取 HEARTBEAT.md 并执行任务 |

---

## 扩展说明

本 workspace 是 psi-agent 框架的一个示例实现，展示了如何构建一个与 OpenClaw 风格兼容的 agent 工作区。用户可以：

1. 复制整个 workspace 目录进行移植
2. 修改 bootstrap 文件来自定义 agent 行为
3. 在 tools/ 目录添加新工具
4. 在 skills/ 目录添加新技能
5. 在 schedules/ 目录添加定时任务

---

## 参考资源

- OpenClaw 源码：https://github.com/openclaw/openclaw
- psi-agent 文档：参见项目 CLAUDE.md
