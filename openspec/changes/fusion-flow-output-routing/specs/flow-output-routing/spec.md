## ADDED Requirements

### Requirement: fusion-flow 生成的 flow.ts 默认写入 flows/adhoc
fusion-flow skill 在 Authoring Mode 生成 `.flow.ts` 文件时，SHALL 将文件写到 `<workspace>/flows/adhoc/<slug>/flow.ts`，而非 `skills/fusion-flow/examples/`。

#### Scenario: Authoring Mode 生成新流
- **WHEN** 用户请求 fusion-flow 生成工作流
- **THEN** 生成的 `.flow.ts` 文件路径为 `<workspace>/flows/adhoc/<slug>/flow.ts`

#### Scenario: 运行记录落点
- **WHEN** `npx tsx` 运行生成的 `.flow.ts`
- **THEN** 运行记录目录为 `<workspace>/flows/adhoc/<slug>/runs/<runId>/`

#### Scenario: corePath 不变
- **WHEN** 运行 `.flow.ts` 时
- **THEN** 运行命令仍为 `cd <workspace>/skills/fusion-flow && npx tsx <abs-path-to-flow.ts>`，corePath 不改变
