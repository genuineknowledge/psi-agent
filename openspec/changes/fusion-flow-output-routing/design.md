## Context

fusion-flow skill 是 psi-agent openclaw-style-workspace 的内置工作流引擎。当前架构下：

- 生成的 `.flow.ts` 写到 `skills/fusion-flow/examples/`
- 运行记录写到 `skills/fusion-flow/runs/`
- `after_turn` 只在 tool-call 数 ≥ 10 时触发 BackgroundReview
- BackgroundReview 的 flow review 只会写到 `flows/curated/`，没有 `adhoc` 路由

这三个问题叠加，导致 fusion-flow 生成的流从未进入 workspace 的 `flows/` 管理体系。

## Goals / Non-Goals

**Goals:**
- 生成的 `.flow.ts` 和 `runs/` 默认落在 `flows/adhoc/<slug>/`
- `after_turn` 检测到新 `.flow.ts` 且原语数 ≥ 5 时触发流审查
- BackgroundReview 审查后质量达标的流自动 promote 到 `flows/curated/`
- `flow_manage` 工具支持 `target="adhoc"` 的 create 操作
- 已有测试（`test_after_turn_flow_review.py`、`test_flow_evolution.py`）继续通过

**Non-Goals:**
- 不改变 fusion-flow 的运行机制（tsx 调用方式不变）
- 不改变 `flows/curated/` 的已有结构和 curator 逻辑
- 不强制迁移已存在的 `skills/fusion-flow/examples/` 下的历史文件

## Decisions

### 1. 默认落点：`flows/adhoc/` 而非 `flows/curated/`

**选择**：新生成的流默认写 `adhoc`，审查通过后 promote。

**原因**：绝大多数一次性调研流不值得长期保留。默认写 `curated` 会污染精选库；默认写 `adhoc` + 审查促进符合"先产出、后筛选"的工作流。

**备选方案**：直接写 `curated` 然后 curator 定期清理 → 被否，curator 是定期任务，审查延迟长且逻辑混淆。

### 2. 触发阈值：原语数 ≥ 5

**选择**：用 `_count_flow_primitives()` 统计 `agent/parallel/pipeline/phase` 调用数，≥ 5 才触发。

**原因**：原语数是流复杂度的最直接代理指标，已有现成实现（`background_review.py`）。阈值 5 过滤掉只有 1-2 个 agent 的简单流，避免浪费 LLM 审查资源。

**备选方案**：无条件触发 → 被否，简单流（原语 1-2 个）审查价值低，且增加无谓的 LLM 调用。

### 3. promote 机制：flow_manage `action="promote"`

**选择**：BackgroundReview 调用 `flow_manage(action="promote", flow_name=slug)` 将 `adhoc/<slug>` 升级到 `curated/<slug>`。`flow_manage.py` 已有 `promote` action，只需补充从 `adhoc` 读取的逻辑。

**原因**：复用现有工具，不新增代码路径。

### 4. SKILL.md 路径更新：指令式而非代码式

**选择**：在 SKILL.md Authoring Mode 步骤 3 中明确写明输出路径为 `<workspace>/flows/adhoc/<slug>/flow.ts`，runs 目录为 `<workspace>/flows/adhoc/<slug>/runs/`。

**原因**：SKILL.md 是 LLM 的行为指令，路径规则写在指令里比写在代码里对 LLM 更直接有效。

## Risks / Trade-offs

- **`corePath` 解析与新路径冲突**：SKILL.md 原有逻辑要求 `cd <corePath> && npx tsx <file>`，而新路径下 `.flow.ts` 不在 `corePath/examples/` 里。需要在 SKILL.md 中明确：生成文件写到 workspace `flows/adhoc/`，但运行时仍 `cd skills/fusion-flow && npx tsx <abs-path>`。→ 在 SKILL.md 的 Run 和 Author 两节都加说明。

- **`_resolve_flow_path` 可能找不到新路径下的文件**：当前实现扫描 `fusion-flow` 关键词目录，新路径 `flows/adhoc/` 不含该关键词。→ 在 `_resolve_flow_path` 的 fallback 搜索中增加 `flows/adhoc/` 和 `flows/curated/` 两个根目录。

- **promote 后 adhoc 原目录残留**：promote 操作应删除 `adhoc/<slug>` 或保留？→ 保留（归档语义），与现有 `.archived/` 机制一致，不删除。

## Migration Plan

1. 更新 `flow_manage.py`：`create` 支持 `target="adhoc"`；`promote` 支持从 `adhoc` 读取
2. 更新 `background_review.py`：`maybe_spawn_flow_review` 的 prompt 增加 adhoc→curated 路由指令；`_build_tool_schemas` 的 `flow_manage` schema 补充 `target` 参数
3. 更新 `system.py`：`after_turn` 增加原语数阈值判断（`_count_flow_primitives` 已有）
4. 更新 `skills/fusion-flow/SKILL.md`：Authoring Mode 步骤 3 输出路径改为 `flows/adhoc/<slug>/`
5. 更新 `_resolve_flow_path`：fallback 增加 `flows/adhoc/` 和 `flows/curated/` 搜索根
6. 更新集成测试预期路径

回滚：以上均为增量修改，`flow_manage` 旧调用方式不变，回滚只需还原 SKILL.md 路径指令。

## Open Questions

- `slug` 命名规则：由 LLM 在 Authoring Mode 中根据任务描述生成，还是取 `.flow.ts` 文件名去掉日期后缀？→ 暂定取文件名（`flow-author-20260616-001` → `flow-author-20260616-001`），保持可追溯性。
