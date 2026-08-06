# Router 效果评测协议

本目录给出一套可复现、可审计的 Router 对照实验。它回答的不是“Router 能否转发 SSE”——现有
pytest 已经覆盖大量协议行为——而是以下三个经验问题：

1. 在什么任务分布上，`routing`、`aggregation`、`fallback` 比固定单后端更有价值？
2. 组合这些模块是否比单一模块和预算匹配的传统 orchestrator 更有效？
3. 质量、可靠性、成本和时延之间的收益是否具有统计意义？

## 1. 结论边界

psi-agent 的 Router 组合的是一个 Session 下的 **AI backend 或下级 Router**。会话历史、system
prompt、workspace tools 和工具执行仍由 Session 统一负责；Router 自身不拥有独立记忆、工具集或
workspace。各叶端后端接收的是同一 Session 投影出的公开请求。因此，本评测可以支持“后端选择、
聚合和故障回退有效”的结论，不能把每个后端描述成拥有独立记忆和工具的完整 Agent。

为了防止条件间历史串扰，每个条件应使用同一 Session 配置和同一初始历史快照的隔离副本。若直接
向 Router 的公开 Chat Completions 端点发送完整 `messages`，也必须保证各条件收到逐字节相同的公开
请求。涉及真实工具执行时，应通过 Session 端点运行，并在每个 case 前恢复相同历史快照。

Router 的能力边界也必须进入结论：

- `routing` 选择一个后端并流式转发；目标失败时不会自动改选另一个目标。
- `aggregation` 将同一请求并发广播给全部目标，再由专用 Aggregator 综合；它不是 Planner，也不
  拆分子任务。
- `fallback` 只识别连接、HTTP、SSE、`finish_reason="error"`、空完成等操作失败。一个内容低质但
  协议完整的回答仍被视为成功，因此 fallback 不能替代质量评审。
- 嵌套 Router 必须显式配置为 Router backend。系统不做运行时循环检测，也不会按嵌套深度自动放大
  timeout。

## 2. 预注册拓扑

先冻结候选顺序、描述、模型版本、价格、timeout 和故障注入计划，再查看测试集结果。统一入口按配置
顺序产生 `candidate-1`、`candidate-2` 等公开编号；不要在实验后根据测试结果改描述或换序。

### 2.1 主拓扑：Routing → 领域 Fallback

```text
同一 Session
    |
    v
Routing(domain)
    |-- candidate-1: code       -> Fallback(code-primary, code-backup)
    `-- candidate-2: reasoning  -> Fallback(reason-primary, reason-backup)
```

这个拓扑把两个问题正交化：外层 routing 负责能力匹配，内层 fallback 负责同领域的操作可用性。它比
扁平 fallback 更有判别力，因为扁平 fallback 会接受排在前面的、协议成功但领域不合适的回答。

### 2.2 进阶拓扑：按难度选择不同计算预算

```text
同一 Session
    |
    v
Routing(difficulty)
    |-- candidate-1: easy -> Fallback(cheap, strong)
    `-- candidate-2: hard -> Fallback(
                              Aggregation[specialist-a, specialist-b, critic],
                              strong
                            )
```

这里 `Aggregation[...]` 是 fallback 的第一个下级 Router，`strong` 是第二个目标。Aggregation 必须
使用专用控制 Aggregator，不能把该控制 AI 同时复用为聚合 target。只有 Aggregation 操作失败时才会
落到 `strong`；聚合结果低质但有效时不会触发 fallback。

主拓扑按领域定义 `candidate-N`，进阶拓扑按难度定义 `candidate-N`，两者的 route label 语义不同。
示例 case 的 `expected_route` 面向主拓扑。若同时评估进阶拓扑，应由 recording proxy 按 condition
关联各自预注册的期望 route，不要要求基础 runner 解析一个多拓扑映射。

## 3. 对照条件与消融

[`config.example.json`](./config.example.json) 列出建议的完整条件集。最低限度应包含：

| 条件 | 目的 | 约束 |
|---|---|---|
| `single-agent-cheap` | 固定低成本单后端基线 | 相同 Session、请求和输出上限 |
| `single-agent-strong` | 固定强后端基线 | 不能只拿弱基线衬托组合 |
| `budget-matched-orchestrator` | 传统单体 orchestrator 基线 | 最大模型调用数、token 或美元预算与完整组合匹配 |
| `routing-only` | routing 单模块消融 | 相同候选与 Selector |
| `aggregation-only` | aggregation 单模块消融 | 相同叶端与专用 Aggregator |
| `fallback-only` | fallback 单模块消融 | 固定且预注册的候选顺序 |
| `routing-domain-fallback` | 主完整组合 | Routing → 领域 Fallback |
| `routing-tiered-composition` | 进阶完整组合 | Routing → Fallback，hard 分支含 Aggregation |

“预算匹配”不能只匹配外层请求次数。应在叶端真实模型调用层同时报告并尽量匹配：

- 最大和实际模型调用数；
- 输入、输出及缓存 token；
- 按当日冻结价格表计算的美元成本；
- 相同 wall-clock timeout 与最大输出长度。

如果无法同时严格匹配所有预算，预注册一个主约束，例如“每 case 实际成本不高于完整组合的
105%”，其余指标原样报告。不要通过给 orchestrator 更短输出或不同 Session prompt 制造优势。

## 4. 适合区分模块价值的场景

### Routing

使用后端能力确实互补的混合任务，例如代码执行测试、严格数学答案、长中文推理和多模态理解。
同时加入：

- 领域清晰与领域模糊的输入；
- 最后一句是“继续”、判别信息位于早期历史的多轮输入；
- 简单与困难任务，防止 Selector 只学会领域而不会控制预算；
- 要求选择某个 candidate 或泄露传输地址的 prompt injection。

与每个固定后端、随机/关键词路由和 oracle best-backend 上界比较。Router 有意义的证据应是质量—
成本 Pareto 改善，而不只是 route label 准确。

### Aggregation

选择单一回答容易漏项、而多份独立材料确实可能互补的任务，例如迁移风险审查、代码 review、证据冲突
辨析和多约束决策。使用盲评 rubric 衡量覆盖、正确性、矛盾处理与无依据断言。另设事实可自动验证的
case，防止 Aggregator 只写出更长、更像样的文本。

### Fallback

在 recording/fault proxy 中按预注册计划注入 503、timeout、断流、畸形 SSE 和 error finish，比较
无故障及 10%/30% primary 故障率。故障计划应由 `(seed, case_id, repetition, logical_backend)`
确定，使共享 primary 的条件遭遇配对故障。另设“协议成功但答案错误”的负对照，确认 fallback 不会
错误地被解释成质量选择器。

## 5. 输入文件

### 5.1 配置 schema

配置文件只包含以下顶层字段：

```json
{
  "conditions": [
    {"name": "条件名", "url": "完整 POST 端点", "request_overrides": {}}
  ],
  "request": {},
  "repetitions": 5,
  "timeout_seconds": 120,
  "seed": 20260806
}
```

- `conditions`：每项严格包含 `name`、`url`、`request_overrides`。`url` 必须是完整 POST 端点，
  例如 `http://127.0.0.1:9031/chat/completions`；runner 不会自动追加路径。
- `request`：所有条件共享的 Chat Completions 请求模板。`messages` 若存在则作为前缀；runner 为每个
  case 追加一条 `{"role":"user","content": case.prompt}`。
- `request_overrides`：对共享 request 做**顶层覆盖**，不做递归深合并。随后 runner 强制
  `stream=true`。条件差异应仅限于预注册且必要的 API 选项。
- `repetitions`：每个 case × condition 的重复次数。
- `timeout_seconds`：单次外层请求总超时；超时必须作为失败保留，不能从统计中删除。
- `seed`：只控制 case × condition × trial 的确定性洗牌顺序，不写入模型请求。若供应商支持推理
  seed，应在所有条件的 request 中显式给出，并记录不支持或忽略 seed 的后端。

### 5.2 Case schema

[`cases.example.jsonl`](./cases.example.jsonl) 每行是一个 JSON object，且只使用以下字段：

```json
{
  "id": "唯一 ID",
  "scenario": "routing|aggregation|fallback|composition",
  "prompt": "发送给被测条件的用户输入",
  "grader": {"type": "exact|contains|manual"},
  "expected_route": "candidate-1",
  "tags": ["domain-code", "difficulty-easy"]
}
```

grader 行为与 `evals.router.metrics.grade_content` 一致：

- `exact`：必须提供字符串 `answer`；对回答和答案做空白折叠及 `casefold()` 后完全相等，得分为
  0 或 1。
- `contains`：可提供字符串数组 `required` 和 `forbidden`。得分是命中的 required 比例；命中任何
  forbidden 时 `contaminated=true`。contains 匹配是大小写敏感的原始子串匹配。
- `manual`：基础 runner 将 `score` 记为 `null`，可选 `forbidden` 仍用于污染检测。人工 rubric 由
  case ID 对应的预注册盲评表管理，不向模型发送，也不伪装成自动分数。

`expected_route` 是主 routing 条件应选择的外层公开 candidate ID。基础 HTTP runner 看不到 Router
内部决策，因此该字段用于和 recording proxy 日志离线关联；不要从最终回答反推 route。对不评估
route accuracy 的条件仍保留该字段，但不计入其 route 指标。

## 6. 执行

1. 为所有 condition 启动相同 Session 配置的隔离实例，或准备逐字节相同的完整请求。
2. 在每个真实 Selector、Aggregator 和叶端 AI 调用前放置透明 recording proxy；故障实验再启用其
   预注册 fault profile。
3. 做少量不计分 warm-up，确认所有端点模型版本、candidate 顺序、timeout 和价格快照。
4. 运行：

```powershell
uv run python -m evals.router.run `
  --config evals/router/config.example.json `
  --cases evals/router/cases.example.jsonl `
  --output evals/router/results.jsonl
```

为保护原始证据, output 已存在时 runner 会拒绝覆盖；确认需要替换旧结果时显式增加
`--overwrite`。

5. 生成描述性汇总：

```powershell
uv run python -m evals.router.summarize --input evals/router/results.jsonl
```

runner 固定洗牌顺序并读取单 choice SSE，累计最终 content，记录协议成功、自动评分、污染、TTFT、
总时延和外层可见 token。原始 JSONL 是审计依据；不要只保存汇总表。manual case 需要在盲评后按
case ID 连接人工分数，再进行研究级统计。

## 7. 指标与公式

令 (q_{icr}\in[0,1]) 为 case (i)、condition (c)、重复 (r) 的得分。manual 分数应按
预注册量表归一化到 `[0,1]`。先在 case 内平均重复，再按 scenario 做 macro-average，避免某一类 case
数量较多而主导结果：

\[
Q_c=\frac{1}{|S|}\sum_{s\in S}\frac{1}{|I_s|}\sum_{i\in I_s}
\left(\frac{1}{R}\sum_{r=1}^{R}q_{icr}\right)
\]

至少报告：

- **Protocol success rate**：收到有效单 choice 完成且无 error/timeout 的比例。
- **Clean success rate**：协议成功、自动任务得分达标且未命中 forbidden 的比例；manual case 不应
  被当作自动成功。
- **Route accuracy**：有 proxy route 记录且 condition 适用时，
  `mean(actual_route == expected_route)`；另报按 scenario 的 macro-F1 和 confusion matrix。
- **Oracle regret**：以固定叶端条件为 oracle 候选，
  \(R_c=\frac1N\sum_i(\max_b q_{ib}-q_{ic})\)。oracle 是上界，不是可部署基线。
- **相对最佳固定后端提升**：
  \(\Delta_{fixed}=Q_c-\max_b Q_b\)。最佳后端必须在 validation split 选择，不能在 test split
  事后挑选。
- **组合增益**：在同预算下，
  \(\Delta_{comp}=Q_{full}-\max(Q_{routing},Q_{aggregation},Q_{fallback},Q_{single})\)。同时单列
  `Q_full - Q_budget-matched-orchestrator`，避免把额外计算误认为架构收益。
- **Fallback recovery rate**：primary 操作失败后由后续候选成功恢复的次数 / primary 操作失败次数；
  另报平均 attempt 数和失败 stream 向用户泄漏事件数（应为 0）。
- **成本**：对每个真实模型调用 (k) 计算
  \(C=\sum_k(t^{in}_kp^{in}_k+t^{out}_kp^{out}_k)/10^6\)，并纳入 Selector、Aggregator、聚合
  branches 及失败的 fallback attempts。若有缓存读写价格，按供应商账单字段拆开计算。
- **时延**：TTFT 与端到端 latency 的 p50/p95；timeout 以预注册上限计入，不能删失。Aggregation
  重点报告最慢分支和 Aggregator 串行阶段，routing 重点报告 Selector 串行开销。
- **效率**：报告 `(quality, cost, p95 latency)` Pareto 前沿，以及达到预注册质量阈值的最低成本。
  `score / dollar` 只能作为辅助指标，成本接近零时该比值不稳定。

## 8. Recording proxy 与成本注意事项

runner 记录的 `visible_input_tokens` / `visible_output_tokens` 只描述外层可见请求和回答，**不是组合的
真实总成本**。Routing 的 Selector、Aggregation 的全部 branches 和 Aggregator、Fallback 的失败
attempt 都可能在外层不可见。仅凭最终 SSE usage 或字符数会系统性低估组合成本。

可信的成本记录应满足：

1. recording proxy 位于每个真实 AI/control-AI 调用边界；Router → Router 的传输本身不计作模型
   调用，避免双重计数。
2. 每次叶端调用分配唯一 call ID，并记录 condition、case、repetition、逻辑角色、模型版本、usage、
   缓存 token、状态和时间戳。日志不得把私有 socket 写进模型 prompt 或结果反馈。
3. 优先使用供应商返回的 usage 或账单数据。不同 tokenizer 下不能用统一字符换算冒充精确成本。
4. proxy 必须透明转发 SSE 顺序、零 choice usage 帧、`[DONE]`、取消和错误；不得自行 retry 或缓冲完整
   响应后再一次性发送。
5. 所有条件使用同一 proxy 路径。异步落盘，单独测量 no-op proxy 延迟并报告原始值与开销分布；不要
   对不同条件随意减去不同常数。

## 9. 统计方法

- case 是主要抽样单位，重复运行不是新的独立样本。先对同一 case 的 repetitions 求均值，再按
  scenario 分层、对 case 做 10,000 次 paired bootstrap，报告均值差与 95% CI。
- 对预注册的二元主指标可补充配对 McNemar 检验；连续/分数指标报告配对效应量和
  win/tie/loss。多个条件与指标同时检验时用 Holm 方法校正。
- manual case 至少由两名不知道 condition 的独立评审者评分，随机化回答顺序并隐藏模型、route、
  时延和长度来源；报告加权 kappa 或 ICC，分歧按预注册规则仲裁。
- 按 scenario、domain、difficulty、故障类型分别报告，再给 macro 总分。必须同时公开失败、timeout、
  空回答和污染样本，不能只在“成功回答”子集上算质量。
- 模型和服务随时间变化时，在同一时间块内随机交错 condition。保存模型版本、运行日期、价格快照、
  git commit、config、case 文件及原始 proxy 日志。

## 10. 现有结构测试及其证明边界

实验前先运行仓库已有验证：

```text
uv run pytest -q tests/evals/router
uv run pytest -q tests/psi_agent/router
uv run pytest -q tests/integration/test_serial_multi_ai_router.py tests/integration/test_fallback_router_composition.py
```

第一条验证评测 runner、grader 与汇总器自身。第二条验证请求复制、单 choice SSE、错误处理、routing
sticky、aggregation 并发隔离、fallback 顺序与回放等协议不变量。第三条验证真实 Session 链路、两层
3×3 组合矩阵、三模式六种排列及部分失败分支。

这些 pytest 使用确定性 mock，Selector 通常选择固定候选，Aggregator 也返回预设内容。它们能证明
“搭积木后协议仍正确”，不能证明 Selector 能在真实任务上选对后端、聚合能提高答案质量、fallback
能改善真实可用性，也不能证明完整组合优于单 Agent 或预算匹配 orchestrator。效果结论必须来自上述
配对实验、真实调用成本和统计区间，不能用测试通过数代替。
