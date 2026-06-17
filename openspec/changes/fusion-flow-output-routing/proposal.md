## 为什么

目前 fusion-flow skill 生成的 `.flow.ts` 文件落在 `skills/fusion-flow/examples/` 下，运行记录在 `skills/fusion-flow/runs/` 下，两者都埋在 skill bundle 内部，与 workspace 的 `flows/` 流库完全脱节。这导致 BackgroundReview 流审查管道和 flows curator 看不到这些产物，生成的流永远不会进入 `flows/curated/`。

第二个问题：`after_turn` 触发流审查依赖 tool-call 数量阈值，而不是实际检测到 `.flow.ts` 文件。一个 session 恰好只写了一个流就结束，往往跳过审查。

## 变更内容

- **生成的 `.flow.ts` 默认落点**：fusion-flow Authoring Mode 将新文件写到 `flows/adhoc/<slug>/flow.ts`，不再写到 `skills/fusion-flow/examples/`。
- **运行记录默认落点**：`npx tsx` 产生的 `runs/` 目录随之落在 `flows/adhoc/<slug>/runs/`。
- **`after_turn` 流检测**：`write` 或 `bash` 工具调用产生了新 `.flow.ts` 时，统计其 flow 原语数量，原语数 ≥ 5 才触发 BackgroundReview 流审查。
- **BackgroundReview 路由逻辑**：`after_turn` 触发审查后，BackgroundReview agent 评估流质量，质量达标（可复用、结构完整、有通用价值）则将其从 `adhoc` promote 到 `flows/curated/<slug>/FLOW.md`；否则留在 `adhoc`。

## 能力范围

### 新增能力

- `flow-output-routing`：控制 fusion-flow 生成的 `.flow.ts` 和运行记录的默认存放位置，默认落在 workspace 的 `flows/adhoc/<slug>/`。
- `after-turn-flow-trigger`：`System.after_turn` 检测到当轮新写入的 `.flow.ts` 后，统计其中的 flow 原语数量（`agent`/`parallel`/`pipeline`/`phase` 调用次数），原语数 ≥ 5 才触发 BackgroundReview 流审查，低于阈值的简单流跳过。
- `background-review-adhoc-routing`：BackgroundReview 流审查 agent 默认将新流写入 `flows/adhoc/`，评估后质量达标则 promote 到 `flows/curated/`。

### 修改的能力

- `fusion-flow-skill`：生成的 `.flow.ts` 和 `runs/` 的默认路径从 `skills/fusion-flow/examples/` 改为 `flows/adhoc/<slug>/`。

## 影响范围

- `examples/openclaw-style-workspace/skills/fusion-flow/SKILL.md` — 更新 Authoring Mode 的输出路径说明
- `examples/openclaw-style-workspace/systems/system.py` — `after_turn` 已有 `.flow.ts` 检测逻辑，增加原语数阈值判断（≥ 5 才触发）
- `examples/openclaw-style-workspace/systems/background_review.py` — `maybe_spawn_flow_review` 的 review prompt 增加 curated/adhoc 路由判断逻辑
- `examples/openclaw-style-workspace/tools/flow_manage.py` — `create` action 补充 `target="adhoc"` 支持
- `tests/integration/test_after_turn_flow_review.py` — 更新预期路径
- `/data6/sby/test/test_flow_evolution.py` — 已轮询 `flows/curated/`，无需改动
