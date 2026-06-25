# Fusion-Guard 到 Dolphin-Agent 的适配设计规格

**日期**: 2026-06-25  
**状态**: 已审批

---

## 1. 目标

把 `Fusion-Guard` 的安全决策链路适配到 `Dolphin-Agent` 中，作为一个可选的 workspace/tool 方案，而不是改造 Dolphin 的核心 agent loop。

本设计的行为约束是：

- 每条 `user message` 都创建一次临时 workspace / 临时 CLI / 临时 session
- 意图分析使用当前 Dolphin session 的历史消息
- 当前 session 在接收 `user message` 时要额外落盘一次 history
- 若意图分析返回 `DENY`，返回 Dolphin 侧统一错误文案，并明确标注为 Fusion-Guard 安全拒绝
- 若意图分析返回 `NONE`，不安装额外规则，继续按 base policy 执行命令
- 若意图分析返回 `allow ...;`，先安装额外规则，再在同一临时 bash tool 中执行命令并回传结果
- 若意图分析过程失败，对 Dolphin 适配层保持 fail-closed，返回统一安全错误文案

---

## 2. 范围

### 包含

- Dolphin session 上下文暴露给 workspace tool
- 当前 user message 追加后立即写入 history
- 一个 Fusion-Guard 风格的安全 bash tool
- 临时 workspace / 临时 session / 临时 CLI 的一次性生命周期
- 意图分析、allow 规则过滤、策略安装、命令执行、结果回传
- 拒绝时的统一错误文案

### 不包含

- 修改 Dolphin 的协议格式
- 把安全逻辑内建成 Dolphin 核心 middleware
- 持久化临时 workspace
- 跨 turn 复用临时 CLI

---

## 3. 总体架构

实现分成两层：

1. **Dolphin core 的最小注入点**
   - 在 `SessionAgent` 中把当前 `user_message` 写入 history 后立即落盘
   - 在 tool 执行时暴露当前 session 的上下文快照给 workspace tool

2. **workspace/tool 适配层**
   - 提供一个安全 `bash` tool
   - 该 tool 创建临时 workspace
   - 该 workspace 内启动临时 `psi-agent session`
   - 再用临时 `psi-agent channel cli` 把意图分析 prompt 交给这个临时 session
   - 临时 session 返回 `DENY` / `NONE` / `allow ...;`
   - 若分析器异常或超时，适配层视为 `analysis_failed`
   - tool 将结果转成 base policy 执行、策略安装后执行或拒绝文案

这样 Dolphin 只负责“当前会话上下文”和“tool 宿主”，Fusion-Guard 继续负责“意图分析与安全执行收敛”。

---

## 4. 数据流

### 4.1 User message 进入时

1. Channel 发送单条 `user message`
2. Session 将该 message 追加到内存 history
3. Session 立即把 history 写回 `workspace/histories/{session_id}.jsonl`
4. Session 将当前 history 快照暴露给 tool 执行上下文

### 4.2 安全 bash tool 执行时

1. tool 从当前 session 上下文读取：
   - `session_id`
   - `workspace`
   - `history_path`
   - `history snapshot`
   - `ai socket`
2. tool 生成意图分析 prompt
3. tool 在临时 workspace 中启动一次性 session 和 CLI
4. 临时 CLI 输出意图分析结果
5. 若结果为 `DENY`：
   - 返回 Dolphin 统一错误文案
   - 文案中必须标注 Fusion-Guard 安全拒绝
6. 若结果为 `NONE`：
   - 不安装额外规则
   - 按 base policy 执行命令
7. 若结果为 `allow ...;`：
   - 安装允许规则
   - 在该 bash tool 中执行命令
   - 返回标准输出 / 标准错误合并结果
8. 若结果为 `analysis_failed`：
   - 返回 Fusion-Guard 统一安全错误文案
   - 不继续执行命令

---

## 5. 接口约定

### 5.1 Session tool 上下文

工具执行时必须能读取到一个只读上下文，至少包含：

- `session_id`
- `workspace_path`
- `history_path`
- `history_messages`
- `latest_user_message`
- `ai_socket`

工具只能读取，不直接修改 session 内存态。

### 5.2 history 落盘

当 `user_message` 进入 `SessionAgent.run()` 后，history 写盘行为变为：

- 先 append user message
- 再立即覆盖写入 JSONL
- 之后再发起 AI 请求和后续 tool 调用

这样临时 CLI 可以稳定读取当前轮上下文。

### 5.3 安全文本

拒绝时统一返回 Dolphin 侧文案，格式固定为：

`[Fusion-Guard] Security policy denied this request: <reason>`

其中 `<reason>` 由意图分析结果映射得到，不直接泄漏未过滤的内部实现细节。

`NONE` 不使用这条拒绝文案。它表示“没有额外 allow 规则，但可以继续按 base policy 执行”。

### 5.4 临时 workspace

每次 `bash` tool 调用都新建一次临时 workspace，包含：

- 意图分析用 system prompt
- 临时 session 所需的最小 workspace 结构
- 执行结果收集所需的脚本

临时目录必须在 finally 路径清理。

---

## 6. 错误处理

- 意图分析失败：返回 Fusion-Guard 统一安全错误文案，不继续执行命令
- 策略安装失败：返回 Fusion-Guard 统一安全错误文案，不继续执行命令
- 临时 session / CLI 启动失败：返回 Fusion-Guard 统一安全错误文案，不继续执行命令
- 命令执行失败：返回 bash 工具的标准错误文本
- 临时目录清理失败：只记日志，不影响主返回

整体语义是 fail-closed。

---

## 7. 测试策略

### 单元测试

- history 在收到 user message 后立即写盘
- tool 上下文能读到当前 history snapshot
- 意图分析 `DENY` 产生统一 Fusion-Guard 拒绝文案
- 意图分析 `NONE` 不安装额外规则并继续执行 base policy 路径
- `allow ...;` 只允许白名单规则进入安装请求
- `analysis_failed` 走统一拒绝文案
- 临时 workspace 一定在 finally 中清理

### 集成测试

- 真实 session + 真实 tool 调用
- 第一轮请求触发临时 CLI 意图分析
- deny 分支不执行命令
- none 分支不安装额外规则，但继续执行命令
- allow 分支完成策略安装并返回命令输出

---

## 8. 验收标准

- Dolphin 不需要改成内建安全 middleware
- 每条 `user message` 的 history 在分析前已经落盘
- 临时 workspace / 临时 session / 临时 CLI 生命周期只绑定单条消息
- 拒绝输出中明确可见 Fusion-Guard 标记
- `NONE` 路径不会被误当成拒绝
- 允许路径可以完成策略安装和 bash 执行
