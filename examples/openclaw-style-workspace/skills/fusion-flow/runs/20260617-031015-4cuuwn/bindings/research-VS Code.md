# VS Code (microsoft/vscode) 社区分析报告

> 说明：以下分析基于我对该项目的已有知识（截至训练数据），并非实时抓取 GitHub API 的结果。VS Code 是 GitHub 上 issue/PR 数量最庞大的项目之一（issue 总数已超过 20 万），因此下面的具体数字大多为**量级估算**，我会明确标注哪些是公开已知事实、哪些是推断。

## 1. Issue 解决周期

平均解决时间：缺乏精确公开统计，估算中位数在数天到数周之间，但存在严重的长尾分布。VS Code 团队有专门的 issue triage（分诊）流程，新 issue 通常在 1-3 个工作日内被打上标签或得到初步回应，但"得到回应"与"实际解决"差异很大。依据：项目公开的 [Issue Triage 文档](https://github.com/microsoft/vscode/wiki/Issue-Triaging) 描述了系统化的分诊机制；团队规模与 issue 体量决定了响应快、彻底解决慢的特征。

典型 issue 生命周期：
- 提交后由机器人/分诊员检查模板完整性，缺信息会被打上 `info-needed` 并可能自动关闭
- 分诊员分配到对应功能领域的负责人（按 `area-*` 标签划分）
- 进入 milestone（VS Code 按月度迭代组织 milestone）或标记为 `backlog`
- 修复、验证（团队有专门的 verification 流程，修复后会被标 `verified`）后关闭

是否有 SLA：没有面向社区的正式 SLA 或响应时间承诺。VS Code 是免费开源产品，不提供 issue 响应保证。这是事实，非估算。

长期未解决占比：估算偏高。该项目有数万个 open issue 长期停留在 backlog（feature request 尤其如此）。团队会定期用机器人关闭陈旧/无活动的 issue。粗略估计长期（>1 年）未解决的 open issue 占活跃 backlog 的相当比例，但我无法给出可靠百分比——这是估算，置信度低。

## 2. PR 合并率

PR 合并率：缺乏官方公布数据。VS Code 的一个显著特点是**绝大多数代码 PR 来自内部团队成员**，外部贡献者 PR 占比较小。内部 PR 合并率高；外部 PR 合并率明显更低。整体合并率我无法给出准确数字，避免编造。

典型 PR 审查周期：内部团队 PR 通常在同一 milestone（数天到两周）内合并；外部 PR 周期差异很大，从数天到数月不等，取决于是否对应已接受的 issue。

审查流程特点（基于公开的 [贡献指南](https://github.com/microsoft/vscode/wiki/How-to-Contribute)）：
- 要求 PR 关联一个已被团队接受的 issue（标 `feature-request` 且已批准，或确认的 bug）
- 贡献者需签署 CLA（Contributor License Agreement）
- 有完整 CI 门槛（构建、单元测试、集成测试、lint）
- 通常需要至少一名核心团队成员 review 批准

被拒绝/关闭的 PR 主要原因（推断）：
- 未关联已接受的 issue，或团队不打算接受该功能
- 不符合项目方向/架构设计
- CI 失败或长期无响应未更新
- 与现有 PR 重复

## 3. 社区治理特点

维护者团队：由微软的 VS Code 核心团队主导，是带薪全职工程师团队（数十人量级）。治理是**公司主导型**而非纯社区共识型——路线图、milestone、是否接受功能由内部团队决定。这是该项目治理的关键特征（事实）。

发布周期：有规律的**月度发布**（每月一个版本，附带 release notes）。这是 VS Code 长期保持的稳定节奏，属公开事实。

贡献者 onboarding 难度：
- 文档完善（有详细 wiki、构建指南、good first issue 标签），入门门槛在文档层面较低
- 但**功能性贡献门槛高**：必须先让团队接受 issue 才考虑 PR，否则容易白费工夫
- 小型 bug 修复、文档、本地化贡献相对容易被接纳

## 4. 数据来源说明

精确/公开已知事实：
- 月度发布节奏（公开 release notes 可验证）
- 系统化的 issue triage 流程与标签体系（公开 wiki）
- 贡献需关联已接受 issue + CLA + CI 门槛（公开贡献指南）
- 微软全职团队主导的公司型治理
- 无面向社区的正式 SLA

估算/推断（置信度有限）：
- issue 平均/中位解决时间的具体数值
- 长期未解决 issue 的具体占比
- PR 整体合并率的百分比
- PR 审查周期的具体天数

我没有调用 GitHub API 或实时统计工具，因此所有涉及具体百分比和天数的内容都是基于项目运作模式的合理推断，而非实测数据。如果你需要精确数字，建议直接查询 GitHub 的 Insights/API，或使用 [OSS Insight](https://ossinsight.io/) 这类第三方统计平台——我可以帮你设计具体的查询方案。