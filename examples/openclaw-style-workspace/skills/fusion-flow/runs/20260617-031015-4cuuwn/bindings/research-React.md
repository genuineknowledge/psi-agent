# React (facebook/react) 社区分析报告

说明：以下分析基于我对 React 仓库长期运作模式的知识（截至 2024 年底的训练数据），不包含实时 GitHub API 统计。具体数字均为估算，已在各节标注依据。需要精确数据请通过 GitHub API 或 [Issue Metrics](https://github.com/github/issue-metrics) 一类工具实测。

## 1. Issue 解决周期

平均解决时间（估算）
- 难以给出单一均值，分布高度长尾。简单的文档/重复类 issue 通常在数天内关闭；涉及 reconciler、并发渲染、Hooks 行为的复杂 issue 可能数月甚至跨年。
- 估算中位关闭时间在数周量级，但相当比例的 issue 是被 bot 或维护者以"无法复现/请提供最小复现/转到 Discussion"等方式关闭，而非真正修复。

典型 issue 生命周期
1. 提交后由机器人或维护者打标签（如 `Component: *`、`Status: Unconfirmed`、`Resolution: Needs More Information`）。
2. React 团队明确要求可复现 demo（CodeSandbox 等），缺失复现的 issue 容易被搁置或自动关闭。
3. 确认为有效 bug 后进入内部跟踪，修复往往随版本迭代合入。
4. 大量"使用问题/求助"类被引导到 GitHub Discussions、Stack Overflow，不在 issue 区解决。

SLA / 响应承诺
- 没有公开的 SLA 或响应时间承诺。React 由 Meta 内部团队主导，外部 issue 响应取决于团队优先级，与内部产品需求强绑定。

长期未解决占比（估算）
- 估计有相当比例（粗略 30%~50% 量级）的 open issue 属于长期未处理或低优先级状态。React 历史上会定期做 issue 清理（stale bot / 批量关闭），所以 open 数量被人为压低，不代表都已解决。这一点请以实测为准。

## 2. PR 合并率

PR 合并率（估算）
- 外部贡献者 PR 的合并率偏低，估算明显低于 50%，可能在 20%~40% 区间。原因是 React 的核心代码改动大多来自 Meta 内部工程师，通过内部系统（Phabricator/Meta 内部 CI）同步到 GitHub，外部 PR 多集中在文档、类型、测试、小修复。
- 文档与 typo 类 PR 合并率较高；触及核心运行时的外部 PR 合并率很低。

典型 PR 审查周期
- 文档/小修复：数天到一两周。
- 核心改动：周期长且不确定，常需团队内部讨论，部分 PR 会长期挂起后被关闭或被内部等价实现替代。

审查流程特点
- 强 CI 门槛：Flow 类型检查、ESLint、Prettier、Jest 单测、bundle size 检查（sizebot 会评论体积变化）等。
- 通常需要核心维护者 approve；核心改动可能需要多人或团队共识。
- 提交需遵循 CLA（Contributor License Agreement）和贡献指南。
- 内部优先：很多变更先在 Meta 内部落地再开源，外部 PR 可能因此被"内部已实现"而关闭。

被拒绝/关闭的主要原因
- 与团队架构方向不一致（尤其涉及 reconciler/并发特性）。
- 缺少测试或破坏现有测试。
- 改动范围过大、缺乏前期 RFC 讨论。
- 重复或已被内部实现。
- 长期无响应被 stale 关闭。

## 3. 社区治理特点

维护者团队规模与工作方式
- 核心团队规模较小，由 Meta（Facebook）员工为主，外加少量长期外部核心贡献者。
- 治理偏"公司主导 + 开源同步"模式：决策中心在 Meta 内部，GitHub 仓库是镜像与协作面。
- 重大变更走 [RFC 流程](https://github.com/reactjs/rfcs)（reactjs/rfcs 仓库），如 Hooks、Suspense 等都经过 RFC。

发布周期
- 没有固定的日历式发布节奏；按特性成熟度发布。历史上有过较长的大版本间隔（如 16→17→18 跨度较大）。会发布 canary/experimental 渠道供尝鲜，新特性（如 Server Components、新编译器）先在实验通道迭代。

贡献者 onboarding 难度
- 文档/示例/类型类贡献门槛低，有 "good first issue" 类标签。
- 核心运行时贡献门槛很高：需理解 fiber 架构、并发模型、Meta 内部同步流程，且方向需与团队一致，外部独立推动核心改动较难。

## 4. 数据来源说明

精确数据
- 本报告不含我直接验证的精确实时统计。所有数字均为估算。

估算依据
- React 长期采用的标签体系、机器人（stale bot、sizebot、CLA 检查）和 "需最小复现" 的 issue 处理惯例。
- React 由 Meta 内部团队主导、通过内部 CI 同步到 GitHub 的公开已知工作模式。
- reactjs/rfcs 的 RFC 决策流程与历史大版本发布节奏。
- 开源社区普遍观察到的"公司主导项目外部 PR 合并率偏低"规律。

建议的精确化方式（若需真实数字）
- 用 GitHub REST/GraphQL API 拉取 issues/PRs 的 `created_at`、`closed_at`、`merged_at` 计算中位/均值。
- 用 `github/issue-metrics` 统计响应与解决时间。
- 区分"外部贡献者 PR"与"维护者 PR"分别计算合并率，否则会被内部同步 PR 严重拉偏。

诚实声明：以上周期与比率均为基于运作模式的合理估算，并非实测统计，请勿直接引用为精确指标。