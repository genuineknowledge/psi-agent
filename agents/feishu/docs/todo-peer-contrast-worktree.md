# 动态三层 · 同级对比 + 带人 scale-up — 实施工作树

> PR：`feat/todo-dynamic-layer3-contrast`（基线 origin/main `ba0719d6`）
> 依据：桌面《CEO需求满足度评估与总体开发方案_动静态分层》第六节「动态三层」；CEO 纪要「同级对比不为主、天然可拉对比表」「体现好后带人的 scale up（组织贡献外溢）」。

## 目标

动态三层 = ①同级 peer 之间的客观对比（「不为主」）②带人 scale-up 观察。产出是**客观对比表与 scale-up 信号，不是排名奖惩**。本 PR 只做 skill 化口径 + config 外置（仓库哲学：规则是文字不是代码，无确定性工具需求不加 .py）。

## 改动清单

| 文件 | 改动 |
|---|---|
| `skills/todo-peer-contrast/SKILL.md` | 新增：动态三层总纲（两个视图 A 同级对比表 / B 带人 scale-up + 同级基准 + 纪律 + 分界） |
| `config/todo-sop.yaml` | 新增 `peer` 段：`same_level_by: mentor` / `min_cycles: 3` / `indicators` 7 项 |
| `docs/todo-peer-contrast-worktree.md` | 本工作树 |
| `AGENTS.md` | 技能索引登记 `todo-peer-contrast` |
| `tests/test_todo_peer_contrast.py` | 新增：两视图 / 事实呈现不排名 / 同级基准 / 样本纪律 / 禁跨组跨层 / 衔接点 / 工具引用 / 索引 |
| `tests/test_todo_sop.py` | `peer` 段断言 + peer-contrast 纳入「判定 skill 指向 config」名单 |

## 判定口径

- **同级基准**：同一 `mentor` 名下成员（config `peer.same_level_by: mentor`）。`#798` sync_org_tree 合入后可升级为组织树同层——只改 config 值，本文流程零改动。
- **视图 A 同级对比表**：同口径同周期可比数字并排，明示「事实呈现，不排名奖惩」；表后可给「需 mentor 关注的差异点」但不下好坏结论。
- **视图 B 带人 scale-up**：客观信号（名下开始有人 / 名下行数 / 下属闭环与打分趋势 / 本人负载形态），不带好坏判断，拍板权在 mentor/上级。
- **指标口径**：与 `todo-growth-profile`（#800）同源 7 项，每条可回溯到台账行 / 快照页 / `.todo-eval`；#800 合入后直接引用其口径。
- **纪律**：严禁跨组/跨层自动对比；样本 < `peer.min_cycles`(3) 明说样本不足；取证对称；先验印象隔离；unavailable 明说；不向无关成员披露。

## 测试与验收

- 运行：`python -m pytest agents/feishu/tests/test_todo_peer_contrast.py agents/feishu/tests/test_todo_sop.py`（隔离 `-c` ini）；`ruff check` / `ruff format --check`。
- 验收：测试全绿；`git diff` 只含本表声明的 6 个文件；不携带仓库内 schedule 实体。

## 后续（不属本 PR）

- `#798` 组织树合入：`same_level_by` 可改 org_tree 同层。
- `#800` growth 合入：指标口径直接引用 growth，删除本文重复定义（保留「只做横向+外溢」的分界说明）。
- P3 之后（CEO 不为主）：peer 对比是否升级为可配置排行视图，另行评审。
