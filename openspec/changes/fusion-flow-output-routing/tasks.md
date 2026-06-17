## 1. flow_manage 工具补充 adhoc 支持

- [x] 1.1 `flow_manage.py`：`create` action 增加 `target="adhoc"` 支持，在 `flows/adhoc/<slug>/` 下创建 FLOW.md
- [x] 1.2 `flow_manage.py`：`promote` action 支持从 `adhoc/<slug>` 读取内容并写到 `curated/<slug>/FLOW.md`
- [x] 1.3 `flow_manage.py`：`list` action 的 `target="adhoc"` 分支已有，确认能列出 `flows/adhoc/` 下的条目
- [x] 1.4 `flow_manage.py`：`view` action 支持从 `adhoc/<slug>` 读取

## 2. background_review 审查 prompt 与工具 schema 更新

- [x] 2.1 `background_review.py`：`_FLOW_REVIEW_PROMPT` 改为先写 `adhoc`，再按质量决定是否 promote
- [x] 2.2 `background_review.py`：`_build_tool_schemas` 中 `flow_manage` schema 补充 `target` 参数（`"adhoc"` / `"curated"`）
- [x] 2.3 `background_review.py`：`maybe_spawn_flow_review` 的 flow review 白名单加入 `flow_manage` 的 promote 调用路径（已有，确认即可）

## 3. system.py after_turn 增加原语数阈值

- [x] 3.1 `system.py`：`after_turn` 在调用 `maybe_spawn_flow_review` 前，用 `_count_flow_primitives()` 统计每个新 `.flow.ts` 的原语数，原语数 ≥ 5 才加入触发列表
- [x] 3.2 `system.py`：`_resolve_flow_path` fallback 搜索根增加 `flows/adhoc/` 和 `flows/curated/`

## 4. fusion-flow SKILL.md 输出路径更新

- [x] 4.1 `skills/fusion-flow/SKILL.md`：Authoring Mode 步骤 3 输出路径改为 `<workspace>/flows/adhoc/<slug>/flow.ts`
- [x] 4.2 `skills/fusion-flow/SKILL.md`：补充说明运行时仍 `cd skills/fusion-flow && npx tsx <abs-path>`，corePath 不变

## 5. 测试更新

- [x] 5.1 `tests/integration/test_after_turn_flow_review.py`：更新预期路径为 `flows/adhoc/`；增加原语数阈值测试用例（< 5 不触发）
- [x] 5.2 `/data6/sby/test/test_flow_evolution.py`：确认 `flows/curated` 轮询路径仍正确（promote 后落点），无需改动
- [x] 5.3 运行 `pytest tests/integration/` 确认全部通过
