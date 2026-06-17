# Kubernetes (kubernetes/kubernetes) 社区分析报告

说明：以下分析基于我对 Kubernetes 项目治理结构、公开流程文档（如 contributor guide、SIG 制度）以及社区已知运作方式的了解。Kubernetes 是超大型项目，具体数字会随时间波动，精确的实时统计需查询 DevStats（k8s.devstats.cncf.io）。我会明确区分确定信息和估算。

## 1. Issue 解决周期

平均解决时间（估算）
- 由于 issue 体量巨大（仓库累计数万 issue，活跃 open issue 长期在数千量级），平均解决时间差异极大。简单 bug/文档类 issue 通常数天到数周关闭；涉及设计讨论或跨 SIG 的 issue 可能数月甚至跨多个发布周期。
- 依据：Kubernetes 采用 SIG（Special Interest Group）分流机制，issue 经 triage 后按领域归口，这会加快有明确归属的问题处理，但复杂问题需要 KEP（Kubernetes Enhancement Proposal）流程，周期显著拉长。

典型 issue 生命周期
1. 提交后由 triage 流程（bot + triage 团队）打标签，关键标签如 `sig/*`、`kind/bug`、`priority/*`、`needs-triage`。
2. 归口到对应 SIG，由该 SIG 的成员讨论或在 SIG 会议中评估。
3. 进入处理（关联 PR）或被标记需要更多信息。
4. 长期无活动会被 `k8s-triage-robot` 自动标记 `lifecycle/stale` → `lifecycle/rotten` → 最终自动关闭（除非被 `/remove-lifecycle` 或 `frozen` 阻止）。

是否有 SLA 或响应时间承诺
- 没有正式的 SLA。这是确定信息——作为志愿者+企业混合驱动的开源项目，Kubernetes 不承诺响应时间。但有制度化的 triage 节奏（SIG 定期会议、triage party），以及自动化的 stale bot 生命周期管理，形成了事实上的处理节律。

长期未解决 issue 占比（估算）
- 估算偏高。由于 stale 机器人会自动关闭沉寂 issue，"长期 open" 的留存其实被人为压制，但仍有相当比例（粗估 30%–50% 的 open issue 处于数月以上无实质进展状态）。此为估算，非精确数据。

## 2. PR 合并率

估算的 PR 合并率
- 核心仓库的 PR 合并率较高，估算约 55%–70%（merged / total closed）。依据：Kubernetes 有严格的自动化门禁，很多低质量 PR 在合并前就被关闭或贡献者主动放弃，同时机器人也会关闭 stale PR，这会拉低 merged 比例。精确值需查 DevStats。

典型 PR 审查周期
- 小型修复：数天到一周多。
- 功能型/涉及 API 变更：数周到数月，且常需绑定 KEP 与特定发布窗口。
- 临近发布的 code freeze 期间，非关键 PR 会被推迟。

审查流程特点（较为确定的制度化流程）
- 采用 OWNERS 文件机制：需要 reviewer 的 `/lgtm` 和 approver 的 `/approve`，通常至少涉及两个角色（review 与 approve 分离）。
- Prow 作为 CI/自动化系统，包含大量必过测试（`/test`、verify、e2e 等），CI 是硬门槛。
- 需签署 CLA（CNCF CLA check）。
- Tide 机器人在满足 label + CI 条件后自动合并。

被拒绝/关闭的 PR 主要原因
- CI/测试持续失败或贡献者未修复。
- 缺少 KEP 或未经 SIG 批准的设计变更。
- 未通过 OWNERS 审批、范围过大或与现有架构冲突。
- 长期无响应被 stale bot 关闭。
- 重复或已被其他 PR 取代。

## 3. 社区治理特点

维护者团队规模和工作方式
- 治理高度结构化：由 Steering Committee（指导委员会）统筹，下设众多 SIG 和 Working Group，每个领域有独立的 chair、tech lead、reviewer/approver 群体。
- 这是少数真正"联邦式"治理的大型项目，CNCF 托管，企业贡献者占比高（Google、Red Hat、微软等历史上贡献显著）。

是否有定期发布周期
- 有，且制度化明确。这是确定信息：Kubernetes 采用约每年 3 个 minor 版本的节奏（历史上约每 3–4 个月一次，近年稳定为每年 3 次），每个周期含 enhancement freeze、code freeze、release candidate 等明确阶段，由 SIG Release 管理。补丁版本（patch）按月度节奏发布。

社区贡献者 onboarding 难度
- 入门门槛中等偏高。文档完善（有 `good first issue`、`help wanted` 标签、contributor guide、mentoring 计划如 LFX），但工具链复杂（Prow、OWNERS、KEP、SIG 流程）、代码库庞大，从首次贡献到成为 approver 路径较长，需要持续参与积累信任。

## 4. 数据来源说明

精确/高确定性信息（基于公开流程文档）：
- SIG/OWNERS/KEP 治理结构、Prow 与 Tide 自动化、`/lgtm` + `/approve` 双角色审查、stale bot 生命周期、CLA 要求、SIG Release 管理的定期发布节奏。这些是 Kubernetes 公开且稳定的制度。

估算信息（需用实时数据验证）：
- 平均 issue 解决时间、PR 合并率具体百分比、长期未解决 issue 占比、审查周期天数。这些是基于项目规模和流程特征的合理推断，未引用实时统计。

权威数据源建议：
- DevStats（k8s.devstats.cncf.io）——CNCF 官方维护的实时统计，含 PR 合并率、issue/PR 生命周期、贡献者活跃度等精确指标。
- 各 SIG 的 GitHub README 与会议记录。
- Kubernetes contributor guide 与 release 文档。

我没有联网拉取当前数字，以上百分比和时间均为估算。如果需要精确的当期数据，可以告诉我，我可以帮你确定从 DevStats 查询哪些具体指标，或在可联网时为你检索。