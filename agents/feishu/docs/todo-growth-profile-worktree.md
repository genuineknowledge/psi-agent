# 动态二层 个人成长档案与周期成长简报 — 实施工作树

> PR：`feat/todo-growth-profile`（基线 `origin/main 58ba260d`，已含 #784/#793/#795/#796）
> 依据：桌面《CEO需求满足度评估与总体开发方案_动静态分层》第六节「动态二层」；CEO 访谈原话（少输入、希望以 todolist 完全评价一个人的动态成长/组织贡献；好了之后带人 scale up 属 P3 缓做）。本文是文档既有设计的落地，不是新发明。

## 目标

动态一层（audit 落盘 + 前后对比摘要）已把「个人连续性序列」沉淀进 `.todo-eval`；台账历史周期表与 wiki 快照链也已由 company-todo-sync 持续维护。动态二层在此基础上做**跨周期聚合观察**：给一个人生成「成长简报」——**人 vs 自己**、有据可依、不打分、不跨人。

## 改动清单

| 文件 | 改动 |
|---|---|
| `skills/todo-growth-profile/SKILL.md` | 新增动态二层总纲：数据源(①wiki 快照链 ②台账历史周期表 ③.todo-eval ④授权后的 E2) / 7 项指标(可回溯) / 周期成长简报形态 / 纪律(样本不足、不发明分数、不跨人) |
| `config/todo-sop.yaml` | 新增 `growth` 段：`indicators` 清单 + `min_cycles: 3`（样本不足阈值） |
| `docs/todo-growth-profile-worktree.md` | 本工作树 |
| `AGENTS.md` | 技能索引登记 `todo-growth-profile` |
| `tests/test_todo_growth_profile.py` | 新增测试：frontmatter/触发短语/人vs自己/不发明分数/指标可回溯/数据源/config growth/工具引用/索引 |
| `tests/test_todo_sop.py` | 断言 config `growth` 段存在且 indicators 非空、min_cycles≥1；把 growth-profile 纳入「判定 skill 指向 config」名单 |

## 判定口径（不发明，沿用既有）

- 不产新档位：定性词用 `completion_verdicts`（已完成/推断已完成／待确认/进行中/未闭环）。
- 指标全部读既有权威存储：台账历史周期表、wiki 快照链、`.todo-eval`；E2 交付物/comments 仅授权后使用。
- 请假顺延豁免口径沿用 `company-todo-fill-check`。
- 周期不足 `growth.min_cycles` 时只给数据、不给成长定论。

## 测试与验收

- 运行：`python -m pytest agents/feishu/tests/test_todo_growth_profile.py agents/feishu/tests/test_todo_sop.py`（隔离收集配置）；`python -m ruff check / ruff format --check` 改动的 .py。
- 验收：测试全绿；`git diff` 只含本表声明的文件。
- 定时任务：成长简报按需生成；周期推送由用户在与 agent 对话中用 `schedule_manage` 自建，本 PR 不携带 schedule 实体。

## 后续（不属本 PR）

- P3：带人 scale up 效果、peer 对比（CEO 明示不为主、可缓）。
- 防滑坡：消费 `.todo-eval` 序列做质量趋势（另立计划）。
- 准确率评估集：仍缺，另立计划。
