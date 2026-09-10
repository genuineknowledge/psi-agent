# Issue 草稿 —— 正负面清单卡交互层：现有 pipeline 之外我们已实现的卡片闭环，吸收哪些由你们定

> 用法：在 genuineknowledge/psi-agent 提 issue 时粘贴；可附上公开分支/源码包链接（如
> Twin-Ghosts/psi-agent-foruse 的提案分支），维护者自行取用。

## 背景

我们在真实租户中围绕正负面行为台账实现了**一整套飞书卡片交互闭环**，发现官方 main 现有的
`_positive_negative_list` pipeline（reader/table/models/analyzer/reviews/notifications/dedupe/rules）
覆盖的是**案例管理后台流**，而**面向人的卡片交互层**（确认、复核、反馈、复盘）在官方代码里还没有对应物。

## 我们已有的交互（官方 pipeline 目前没有的）

1. **候选批量确认**：会议纪要 → 行为候选 → multi_use 卡逐行「确认 / 不记录 / 修改后记录」→ 入库。
2. **AI 判断 + mentor 点击复核**：判断（锐评 + ★1-5 + 证据 + 建议）→ 同意 / 调整（覆写改判）/ 打回。
3. **反馈被记录人 + 双向闭环**：生效结果私聊反馈本人 → 开始复盘 / 补充说明；说明写回台账备注列，写成功才刷终态。
4. **周小结卡**：本周已登记记录 + 状态分布 + 展开全文 + 开始复盘。

实现形态：7 个 `feishu_pn_*` 工具 + `_pn_impl.py` 共享核心（4781 行）+ 2 个契约测试（1962 行）；
另有 mentor 周期报表卡引擎（build_cards/fetch_ledgers/fetch_leave_attendance，约 1900 行）一并附上。

## 请你们定夺

- 上述 A（交互闭环）/ B（卡片状态层：multi_use 逐行 action、终态幂等、状态持久化）/ C（台账回写契约）
  / D（mentor 周期引擎）中，哪些值得吸收进 agents/feishu？哪些方向你们已有计划、不必重复？
- 源码完整打包在提交说明同目录（`pn-cards/` + `mentor-cards-engine/`，已剔除台账 token / 花名册 / 实卡数据）。
  若需要走 PR，我可按你们偏好整理成基于最新 main 的增量分支。

## 边界说明（如实）

测试为真实租户流程契约测试，原随 `agents/feishu/tests/` 运行，未做独立环境复验；运行数据未随源码公开。
