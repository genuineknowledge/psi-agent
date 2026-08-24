# FusionFlow 声明式反馈循环设计

> **状态**: Draft implementation
>
> **目标分支**: `agent/workflow-runtime-reliability`
>
> **跟踪 Issue**: [#88](https://github.com/msg-bq/psi-agent/issues/88)
>
> **范围**: 定义并实现反馈状态的 `n -> n+1` 语义、同步提交、最终状态可见性和 `TerminalStep` 终止契约。

## 目标

FusionFlow 是声明式 Step-Artifact 数据流语言。反馈循环继续使用现有的 `consumes` / `produces` 图关系表达, 不引入命令式 `for` 或 `while`。

```fusionflow
consumes(update_a) == [state_a, state_b];
produces(update_a) == [state_a];

consumes(update_b) == [state_a, state_b];
produces(update_b) == [state_b];
```

上述关系在合法反馈区域内唯一解释为:

```text
state_a[n + 1] = update_a(state_a[n], state_b[n])
state_b[n + 1] = update_b(state_a[n], state_b[n])
```

同一个 feedback Artifact 表示一条按 epoch 版本化的状态流。源码不需要为了
runtime 版本机制增加 `state_a_next` 等第二状态 identity；作者仍可声明普通的
`next_state` 业务中间值, 再由唯一 commit Step 把它写回原 feedback Artifact。

## 反馈状态识别

每个反馈状态必须同时满足:

1. 位于 Step-Artifact 二部图的循环区域内。
2. 具有一个不依赖该循环区域的初始化来源。
3. 具有恰好一个环内 producer, 作为下一状态 writer。

完整设计允许的初始化来源是:

- `input_workflow` 提供的初值;
- 环外 initializer 提供的初值;
- resume 时已完整提交的 checkpoint。

`consumes` 只声明依赖, 不能提供初值。

本 PR 的第一版后端只接受 `input_workflow` seed；resume 时可从该 loop
已经完成 barrier commit 的 checkpoint 接续。环外 initializer 因当前
Artifact 全局单 producer 约束尚不能无歧义表示，留在 #88 后续实现。

对一个循环区域, 所有同时满足“有独立初值”和“有唯一环内 next writer”的 Artifact 都进入同一个同步反馈状态集合。后端不在多个候选 feedback cut 之间猜测作者意图。

若状态集合为空、存在多个 next writer, 或把反馈写入解释为跨 epoch 后单轮依赖仍有环, checker 必须拒绝该图。

## 同步 epoch 语义

每个 epoch 使用不可变的 current snapshot:

```text
current = state_vector[n]
staged  = empty state_vector[n + 1]
```

规则如下:

1. 本轮所有 feedback `consumes` 都读取同一份 `state_vector[n]`。
2. feedback `produces` 只写入 staged `state_vector[n + 1]`。
3. staged 状态不能在同一轮通过普通 `consumes` 被读取。
4. 所有 next state 与终止判断成功完成后, 才在 epoch barrier 原子提交整个状态向量。
5. 任一必需 writer 或终止判断失败时, 本轮状态向量不得部分提交。

这对应同步 Jacobi 更新, 而不是依赖调度先后的异步或 Gauss-Seidel 更新。

同一 epoch 读取 staged `n+1` 的显式语法不在本设计范围, 继续由 #88 跟踪。

## 最终状态可见性

反馈区域对外表现为一个复合数据流节点:

- 中间 epoch 只对循环内部、checkpoint 和 trace 可见;
- 环外 consumer 不会被中间状态唤醒;
- `output_workflow` 和环外 consumer 只在循环成功终止后读取最终已提交状态。

## 终止概念

catalog 增加两个子类型概念:

```text
TerminalStep <: Step
BoolArtifact <: Artifact
```

不增加 `termination_signal(...)` operator。`TerminalStep` 的类型已经声明其控制角色。

`TerminalStep` 仍是普通数据流 Step:

- 使用 `consumes(...)` 读取本轮业务数据;
- 由自己的 executor / instruction 完成业务判断;
- 每个 epoch 恰好运行一次;
- 唯一逻辑结果是严格 Boolean。

### 显式结果

```fusionflow
const delta: Artifact;
const done: BoolArtifact;
const check_convergence: TerminalStep;

consumes(check_convergence) == [delta];
produces(check_convergence) == [done];
```

显式 `produces` 必须恰好是长度为 1 的列表, 唯一元素必须属于 `BoolArtifact`。

### 隐式结果

```fusionflow
const delta: Artifact;
const check_convergence: TerminalStep;

consumes(check_convergence) == [delta];
```

省略 `produces` 时, compiler 为该 `TerminalStep` 创建一个不可从源码引用的内部 `BoolArtifact` 结果。显式形式与隐式形式具有相同的控制语义。

### 封闭输出契约

`TerminalStep` 不能产生第二个输出或任何普通 Artifact。

这个 BoolArtifact 是封闭的 loop control，不能作为 `output_workflow` 输出，
也不能被其他 Step 消费。需要对外呈现的完成状态应进入最终 feedback state，
或由终止后的普通 Step 从该最终状态提取。

诊断报告、评分、差值、验证结果等业务数据必须由普通 Step 产生; `TerminalStep` 只消费这些数据并作最终 Boolean 分类。

运行值只接受严格 `true` 或 `false`, 不接受字符串、数字或一般 truthy / falsy 转换。

## 循环归属

`TerminalStep` 不接收额外的循环定位参数。后端通过其 input dependency ancestry 反向定位所属 feedback component:

- 唯一到达一个 feedback component: 合法;
- 到达零个 feedback component: 静态错误;
- 到达多个独立 feedback component: 静态归属歧义;
- 一个 feedback component 没有 `TerminalStep` 或有多个 `TerminalStep`: 静态错误。

循环归属只是静态图分析, 不会把 feedback state 作为隐藏参数传给 `TerminalStep`。

## barrier 与终止顺序

每个 epoch 的后端顺序为:

1. 从 current snapshot 执行本轮数据流。
2. 收集完整 staged next-state vector。
3. 执行并读取 `TerminalStep` 的严格 Boolean 结果。
4. 若所有必需结果成功, 原子提交完整 next-state vector。
5. 根据 Boolean 决定继续或成功终止。

| TerminalStep 结果 | 后端行为 |
|---|---|
| `false` | 提交完整 `state[n+1]`, 开始下一 epoch |
| `true` | 提交完整 `state[n+1]`, 成功终止, 对外发布最终状态 |
| 非 Boolean | 不提交本轮, 报运行时契约错误 |
| Step 失败 | 不提交本轮, 按 Step 失败 / retry 策略处理 |

`true` 指“本轮产生的新状态是最终状态”, 因此必须先提交 `state[n+1]`, 不能返回 `state[n]`。

## Loop Engineering

### 行为伪代码

下面的 `while` 只用于解释运行行为, 不进入 FusionFlow 源码:

```python
def loop_engineering(state):
    while True:
        work = discover(state)
        candidate = engineer(state, work)
        verification = verify(state, candidate)
        next_state = advance(state, work, candidate, verification)
        done = should_stop(work, verification)
        state = commit(next_state)

        if done:
            return state
```

`next_state` 是本轮中间 Artifact。`commit` 消费它并成为反馈 Artifact
`state` 的唯一 next writer。真正的原子提交仍由 epoch barrier 完成, 因此
这个 `commit` Step 不能执行不可回滚的外部副作用。

### 逐轮关系

```text
work[n]         = discover(state[n])
candidate[n]    = engineer(state[n], work[n])
verification[n] = verify(state[n], candidate[n])
next_state[n]   = advance(state[n], work[n], candidate[n], verification[n])
done[n]         = should_stop(work[n], verification[n])
state[n + 1]    = commit(next_state[n])
```

barrier 等待 `state[n+1]` 与 `done[n]` 都成功:

- `done[n] == false`: 提交 `state[n+1]`, 开始第 `n+1` 轮;
- `done[n] == true`: 提交 `state[n+1]`, 将它作为最终工程状态发布;
- 任一 Step 失败: 不提交本轮。

### FusionFlow 声明

该示例只增加 `TerminalStep` 和 `BoolArtifact` 两个 catalog concept。所有 operator 都来自现有 G4 工作流写法:

```fusionflow
const state: Artifact;
const work: Artifact;
const candidate: Artifact;
const verification: Artifact;
const next_state: Artifact;
const done: BoolArtifact;

const discover: Step;
const engineer: Step;
const verify: Step;
const advance: Step;
const commit: Step;
const should_stop: TerminalStep;

const discover_agent: Agent, Executor;
const engineer_agent: Agent, Executor;
const verify_agent: Agent, Executor;
const advance_agent: Agent, Executor;
const commit_agent: Agent, Executor;
const stop_agent: Agent, Executor;

workflow loop_engineering {
  input_workflow(loop_engineering) == [state];

  consumes(discover) == [state];
  produces(discover) == [work];

  consumes(engineer) == [state, work];
  produces(engineer) == [candidate];

  consumes(verify) == [state, candidate];
  produces(verify) == [verification];

  consumes(advance) == [state, work, candidate, verification];
  produces(advance) == [next_state];

  consumes(should_stop) == [work, verification];
  produces(should_stop) == [done];

  consumes(commit) == [next_state];
  produces(commit) == [state];

  output_workflow(loop_engineering) == [state];

  step_executor(discover) == discover_agent;
  step_executor(engineer) == engineer_agent;
  step_executor(verify) == verify_agent;
  step_executor(advance) == advance_agent;
  step_executor(should_stop) == stop_agent;
  step_executor(commit) == commit_agent;

  step_name(discover) == "Discover";
  step_instruction(discover) == "Return discover(state) as work.";

  step_name(engineer) == "Engineer";
  step_instruction(engineer) == "Return engineer(state, work) as candidate.";

  step_name(verify) == "Verify";
  step_instruction(verify) == "Return verify(state, candidate) as verification.";

  step_name(advance) == "Advance";
  step_instruction(advance) == "Return advance(state, work, candidate, verification) as next_state.";

  step_name(should_stop) == "Should Stop";
  step_instruction(should_stop) == "Return exactly should_stop(work, verification) as strict Boolean done.";

  step_name(commit) == "Commit Next State";
  step_instruction(commit) == "Return commit(next_state) as state without external side effects.";
}
```

这里 `should_stop` 本身就是 `TerminalStep`, 因而 `done` 是它唯一且封闭的
`BoolArtifact` 输出。

## ReAct Loop

### 行为伪代码

```python
def react(prompt, env, max_steps):
    for step in range(max_steps):
        thought, action = reason(prompt)
        observation, done = env.step(action)
        prompt = update(prompt, thought, action, observation)

        if done:
            return action
```

`step` 对应 runtime epoch, `max_steps` 对应 host 的 `max_loop_epochs` 安全上限。
`env` 是 `env_step` 的 executor, 不是 Artifact。这份代码每轮都会调用一次
`env.step(action)`, 然后检查同一次调用返回的 `done`。

逐轮关系为:

```text
(thought[n], action[n])    = reason(prompt[n])
(observation[n], done[n])  = env.step(action[n])
prompt[n + 1]              = update(prompt[n], thought[n], action[n], observation[n])
loop_done[n]               = terminal(done[n]) = done[n]
```

### FusionFlow 声明

```fusionflow
const prompt: Artifact;
const thought: Artifact;
const action: Artifact;
const observation: Artifact;
const done: BoolArtifact;
const loop_done: BoolArtifact;

const reason: Step;
const env_step: Step;
const update: Step;
const terminal: TerminalStep;

const reason_executor: Agent, Executor;
const env: Agent, Executor;
const update_executor: Agent, Executor;
const terminal_validator: Program, Executor;

workflow react {
  input_workflow(react) == [prompt];

  consumes(reason) == [prompt];
  produces(reason) == [thought, action];

  consumes(env_step) == [action];
  produces(env_step) == [observation, done];

  consumes(update) == [prompt, thought, action, observation];
  produces(update) == [prompt];

  consumes(terminal) == [done];
  produces(terminal) == [loop_done];

  output_workflow(react) == [action];

  step_executor(reason) == reason_executor;
  step_executor(env_step) == env;
  step_executor(update) == update_executor;
  step_executor(terminal) == terminal_validator;
  program_path(terminal_validator) == "./skills/workflow/examples/terminal_identity.py";

  step_name(reason) == "Reason";
  step_instruction(reason) == "Read prompt and produce thought and action as two separate outputs. Do not execute action.";

  step_name(env_step) == "env.step";
  step_instruction(env_step) == "Execute env.step(action) exactly once and produce observation and strict Boolean done as two separate outputs.";

  step_name(update) == "Update Prompt";
  step_instruction(update) == "Return update(prompt, thought, action, observation) as the next prompt.";

  step_name(terminal) == "If Done";
  step_instruction(terminal) == "Validate done and produce loop_done with exactly the same strict Boolean value.";
}
```

`done` 是 `env_step` 产生的业务 Boolean。`loop_done` 是 `terminal` 产生的
封闭 loop-control Boolean。两者使用不同 Artifact ID, 避免两个 Step 同时写
`done`。`terminal_identity.py` 只校验并原样返回 `done`, 不重新判断业务条件。

### 这个例子完成了什么, 尚缺什么

新的反馈与终止语义已经能声明:

- `prompt[n] -> thought/action -> observation/done -> prompt[n+1]`;
- 对 `env.step` 返回值的严格 Boolean 终止判断;
- `true` 时先提交 `prompt[n+1]`, 再发布终止轮的 `action`;
- 中间轮的 `action` 不作为 workflow output 发布。

但 `TerminalStep` 只解决循环终止, 不自动提供以下能力:

- `env.step` 内部 action 类型与静态校验;
- 一个可观察的动态工具派发 primitive;
- 外部工具副作用的 exactly-once。

因此, 在 `env_step` 仍是黑盒 executor 时, 该 G4 能逐项表达这段 ReAct
外层反馈结构, 但不能证明 `env.step` 内部的工具副作用具有 exactly-once 语义。

## 必需静态诊断

第一阶段至少应提供以下稳定诊断:

- `MISSING_INITIAL_STATE`
- `MULTIPLE_NEXT_WRITERS`
- `RESIDUAL_EPOCH_CYCLE`
- `MISSING_TERMINAL_STEP`
- `MULTIPLE_TERMINAL_STEPS`
- `TERMINAL_LOOP_NOT_FOUND`
- `AMBIGUOUS_TERMINAL_LOOP`
- `INVALID_TERMINAL_OUTPUT_COUNT`
- `INVALID_TERMINAL_OUTPUT_TYPE`

诊断应列出 loop component、feedback state、初始化来源、current readers、next writer、TerminalStep 和 commit group, 让作者明确看到 `n` 与 `n+1` 的解释。

## Runtime 持久化与恢复

实现不再使用“每个 Step 只运行一次”的身份模型，已经加入:

- 稳定 `loop_id`;
- `epoch` 纳入 invocation identity;
- committed current-state vector;
- epoch barrier 的原子 commit;
- resume 时校验完整 checkpoint 与 workflow definition digest;
- 初始 checkpoint 尚未进入 loop 时，仍严格校验 feedback input seed。

当前只支持从 epoch barrier 恢复。inflight epoch 与 staged outputs 会保留在
checkpoint schema 中用于校验，但不能从半轮状态续跑；遇到这种 checkpoint
会 fail closed。后续可在保证副作用重放契约后支持恢复未完成 epoch。

外部副作用无法仅靠 checkpoint 获得 exactly-once。Program 和 Agent tool invocation 仍须使用稳定 idempotency key, 或明确保持 at-least-once 语义。

## 第一版实现边界

本 PR 有意先收紧到可以准确执行和清楚诊断的子集:

- 每个 workflow 最多一个 feedback component;
- feedback seed 只来自 `input_workflow` 或已提交 checkpoint;
- 每个 feedback Artifact 恰好一个 next writer;
- 删除跨 epoch feedback 依赖后，本轮图必须是 DAG;
- feedback 不与 selector、`foreach` 或 Human Step 混用;
- `TerminalStep` 必须唯一归属于该 component;
- 环外 consumer 与 workflow output 只看到终止后的最终 committed state;
- resume 只发生在 epoch barrier;
- host 可用 `max_loop_epochs` 做安全上限，但它不是 FusionFlow 源码语法。

## 非目标

本设计不包含:

- `for` / `while` 命令式语法;
- 默认固定点终止;
- `termination_signal(...)` operator;
- 同一 epoch 显式读取 staged `n+1`;
- nested loop / multi-rate loop;
- lazy `if` / branch activation;
- exactly-once 外部副作用保证;
- 跨机器调度与故障接管。

## 实现状态

- [x] 在 catalog / type checker 中加入 `TerminalStep <: Step` 和 `BoolArtifact <: Artifact`。
- [x] 支持 TerminalStep 显式或隐式唯一 BoolArtifact 输出并实施封闭输出校验。
- [x] 分析单个 feedback component、workflow input seed、唯一 next writer 与单轮 DAG。
- [x] 生成 current / staged 双缓冲 epoch execution unit。
- [x] 实施完整状态向量 barrier、终止后最终状态可见性与 Artifact 覆盖发布。
- [x] 把稳定 `loop_id` / `epoch` 纳入 checkpoint 和 invocation identity。
- [x] 增加真实 G4 编译、planner、同步 snapshot、严格 Boolean、barrier checkpoint 与恢复保护测试。
- [x] 提供 Loop Engineering 与归一化 ReAct 的完整 `.workflow` 示例。
- [x] 更新 Workflow README 与 SKILL 行为约定。
- [ ] 支持 inflight epoch / staged output 恢复与外部副作用幂等契约。
- [ ] 支持环外 initializer、多/嵌套 feedback component、selector/foreach/Human 组合。
