## ADDED Requirements

### Requirement: after_turn 基于原语数触发流审查
`System.after_turn` 在检测到当轮新写入的 `.flow.ts` 文件时，SHALL 统计其 flow 原语数量，仅当原语数 ≥ 5 时触发 BackgroundReview 流审查。

#### Scenario: 原语数达标触发审查
- **WHEN** 当轮产生新 `.flow.ts` 且其 `agent`/`parallel`/`pipeline`/`phase` 调用总数 ≥ 5
- **THEN** `after_turn` 调用 `BackgroundReview.maybe_spawn_flow_review()`

#### Scenario: 原语数不足跳过审查
- **WHEN** 当轮产生新 `.flow.ts` 且原语数 < 5
- **THEN** `after_turn` 不触发流审查，正常返回

#### Scenario: 未产生新 flow.ts
- **WHEN** 当轮 `write`/`bash` 工具调用未产生新 `.flow.ts` 文件
- **THEN** `after_turn` 不触发流审查

#### Scenario: 新路径下可检测
- **WHEN** 新 `.flow.ts` 写在 `flows/adhoc/<slug>/flow.ts`
- **THEN** `_resolve_flow_path` 能正确解析该路径并返回绝对路径
