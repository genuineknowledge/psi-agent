# rust-lang/rust 社区分析报告

下面的分析基于我对 Rust 项目公开运作方式的了解（官方文档、RFC 流程、bors 合并机制、团队治理结构等）。请注意：具体的平均时长、百分比为估算值，rust-lang/rust 是体量极大的仓库（数万 open issues、累计数十万 PR），精确数字会随时间显著波动，应以 GitHub 实时数据为准。

## 1. Issue 解决周期

平均解决时间（估算）
- 没有统一的"平均值"能代表全部 issue，差异极大。按类型粗略估算：
  - 编译器崩溃（ICE）、可复现的回归（regression）：通常较快，数天到数周，因为有 triage 团队和优先级标签（如 `P-critical`、`regression-from-stable-to-stable`）驱动。
  - 一般 bug：数周到数月。
  - 功能请求 / 语言特性：往往不通过 issue 直接"解决"，而是转入 RFC 流程，周期以月甚至年计。
- 这是基于 Rust 的标签体系和 triage 流程的推断，不是精确统计。

典型 issue 生命周期
1. 提交后由 triage 团队或机器人打标签（`needs-triage`、领域标签 `A-*`、团队标签 `T-compiler` 等）。
2. 按严重程度分配优先级（`P-critical` 到 `P-low`）。
3. 回归类问题会进入每周的 triage 会议讨论，可能被列入发布里程碑（`milestone`）。
4. 通过 PR 修复，PR 合并后用 `Fixes #xxxx` 自动关闭，或手动关闭。

是否有 SLA 或响应时间承诺
- 没有正式 SLA。Rust 是志愿者 + 部分受雇工程师混合的开源项目，不对外承诺响应时间。
- 实际上存在"事实上的优先级机制"：标记为 stable-to-stable 回归的问题会被认真对待，因为关系到下一个稳定版发布质量。

长期未解决 issue 占比（估算）
- 占比相当高。Rust 的 open issue 总数长期维持在数万量级，其中很大一部分是长期开放的功能追踪 issue（tracking issues）、设计讨论、低优先级 bug。
- 粗略估计：相当比例的 open issue 开放时间超过一年。需注意 tracking issue 本就设计为长期开放（追踪某特性从 nightly 到 stable 的全过程），不应等同于"未解决问题"。

## 2. PR 合并率

估算的 PR 合并率
- Rust 的 PR 合并率通常较高，我估计在 70%–85% 区间。原因是大量 PR 来自核心团队和长期贡献者，且很多外部 PR 在合并前会经过充分的 review 迭代而非直接关闭。
- 这是基于成熟、流程化项目的一般规律的估算，非精确统计。

典型 PR 审查周期
- 小型修复：数天到一两周。
- 编译器/标准库的实质性改动：数周到数月，可能需要多轮 review 和性能回归测试。

审查流程特点（这是 Rust 较为独特、我比较有把握的部分）
- 使用 bors 机器人进行合并：reviewer 用 `r+` 批准后，PR 进入合并队列，bors 在合并前对每个 PR 单独跑全套 CI，CI 全绿才真正合并到 master。这保证 master 始终可构建（"not rocket science" 合并规则）。
- 通常需要至少一名有权限的 reviewer 批准。使用 `@rust-highfive`（现 triagebot）自动分配 reviewer。
- 重大改动需要团队签字（FCP，final comment period）或先有对应 RFC。
- 性能敏感的改动会触发 `@bors try` + perf 基准测试。

被拒绝/关闭的 PR 主要原因
- 对应特性需要先走 RFC 流程，PR 提前提交。
- 设计方向未达成共识。
- 长期无人响应作者的修改请求，最终被标记 stale 关闭。
- 重复或被其他 PR 取代。

## 3. 社区治理特点

维护者团队规模与工作方式
- 采用团队（Team）治理结构：compiler、libs、lang、infra、release 等多个团队，加上下属工作组（working groups）。整体活跃贡献者规模庞大（数千名历史贡献者，核心团队数十至上百人量级）。
- 通过 RFC 仓库（rust-lang/rfcs）做语言级决策，公开讨论 + FCP 机制。
- 治理由 Leadership Council 协调（2023 年治理改革后取代了原 core team 模式）。

发布周期
- 有严格的固定发布周期：每 6 周发布一个 stable 版本，配套 nightly（每日）和 beta 通道。这是 Rust 治理中最确定、最可靠的事实之一。

贡献者 onboarding 难度
- 文档完善：有 rustc-dev-guide、std 文档、`E-easy` / `E-mentor` 标签帮助新人。
- 但编译器本身复杂度高，深入贡献的学习曲线陡峭。整体而言新手友好度在大型系统项目中算较好的。

## 4. 数据来源说明

精确/高可信度的部分（基于公开且稳定的事实）
- 6 周固定发布周期。
- bors 合并队列 + 每个 PR 独立全量 CI 的机制。
- 标签体系、RFC 流程、团队治理结构、Leadership Council。
- triagebot/highfive 自动分配 reviewer。

估算/推断的部分（无精确实时数据支撑）
- issue 平均解决时间、各类型周期。
- PR 合并率 70%–85%。
- 长期未解决 issue 占比。
- 各类 PR 审查周期的具体天数。

诚实说明
- 我没有访问 rust-lang/rust 的实时 GitHub 统计，上述数字均为基于项目运作模式的估算，未编造具体精确值。
- 若需要精确数据，建议直接查询 GitHub API（issues/PRs 的 created/closed/merged 时间戳）或使用如 OSS Insight、GitHub Insights 等第三方分析平台。我可以帮你写一段调用 GitHub API 统计合并率和解决周期的脚本，如果需要的话。