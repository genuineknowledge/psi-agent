## ADDED Requirements

### Requirement: BackgroundReview 流审查默认写 adhoc 并按质量 promote
BackgroundReview 流审查 agent 在审查新 `.flow.ts` 后，SHALL 先将流写入 `flows/adhoc/<slug>/FLOW.md`，然后评估其质量，质量达标时调用 `flow_manage(action="promote")` 将其提升到 `flows/curated/<slug>/FLOW.md`。

#### Scenario: 高质量流自动 promote
- **WHEN** 审查 agent 判断流可复用、结构完整、有通用价值（原语数 ≥ 5、有明确输入输出、逻辑可复用）
- **THEN** 调用 `flow_manage(action="promote", flow_name=slug)` 将流从 `adhoc` 提升到 `curated`

#### Scenario: 低质量流留在 adhoc
- **WHEN** 审查 agent 判断流为一次性任务或结构简单
- **THEN** 流保留在 `flows/adhoc/<slug>/FLOW.md`，不调用 promote

#### Scenario: flow_manage 支持 adhoc create
- **WHEN** 调用 `flow_manage(action="create", flow_name=slug, target="adhoc", ...)`
- **THEN** 在 `flows/adhoc/<slug>/FLOW.md` 创建流文件

#### Scenario: promote 后 adhoc 原文件保留
- **WHEN** promote 操作完成后
- **THEN** `flows/adhoc/<slug>/` 目录保留（归档语义），`flows/curated/<slug>/FLOW.md` 同时存在
