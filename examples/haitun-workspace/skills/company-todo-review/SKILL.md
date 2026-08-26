---
name: company-todo-review
description: "公司 TODO 管理体系·评价回写 —— 负责人交付一条 todo 后，向其 mentor 发送 1-5 分评价卡；mentor 打分/评语后把结果写回台账并追加到该人 wiki 快照页对应 todo 之后。Use when a todo's Feishu task is marked complete (交付事件驱动，通常由 feishu_todo_card_tick 之后的下一轮触发本技能发评价卡), or when a <feishu_card_action> callback with dispatch.handler pointing at this skill's review card arrives. Companion skills: company-todo-sync (采集与派发), company-todo-audit (闭环判定)."
category: productivity
---

# 公司 TODO 管理体系 · 评价回写

一条 todo 交付后，让其 mentor 打分评语，回写权威台账并追加进本人 wiki 快照页——这一步是
闭环五要素里的第 4、5 项，缺了任何一半都停在「未闭环」（见 `company-todo-audit`）。

## 用到的工具

- `feishu_review_card_send` — 补发/重发 1-5 分评价卡（按台账 record_id 发，测试模式自动改发测试人）
- `feishu_review_card_select` — 点分回调的直调工具（框架自动派发，本技能不处理）
- `feishu_review_input` — 评语确认回调的直调工具（框架自动派发，本技能不处理）
- `feishu_review_reject` — 打回重做回调的直调工具（框架自动派发，本技能不处理）：
  撤销任务完成状态 + 台账状态回「进行中」+ 重建卡标注「已打回重做」
- `feishu_bitable_update_record` / `feishu_bitable_search_records` — 定位并写回台账（仅直调失败回退时由本技能使用）
- `wiki_read` / `wiki_write` — 评价与打回记录的 wiki 回写（由 `company-todo-audit` 第 0 步兜底）

## 交付后发卡

1. **发卡已自动化**：`feishu_todo_card_tick` 在把任务标记完成后，会**自动**查台账行取 mentor，
   向其私聊发评价卡（本技能不再负责发卡；若 tick 结果里 `review_card.skipped` 为「no mentor」
   或台账读取失败，说明该行没接台账或没配 mentor，此时不需要补发）。需要重发卡时调
   `feishu_review_card_send`（传台账 record_id），不要手工拼卡。
2. 评价卡布局（工具 `_review_card_impl` 固化，**card 2.0**）：
   - **五个小按钮 1分-5分**：点分即打分——由工具 `feishu_review_card_select` 直调处理：
     写台账 `mentor打分`（Number 列，传 int）+ 原位重建卡片高亮「✓ N分」（五选一互斥，
     可反复改分，后点覆盖）；
   - **评语输入框**：带「确认」按钮，点确认后回调携带 `action.input_value` —— 由工具
     `feishu_review_input` 直调处理：写台账 `mentor评语`（Text 列）+ 原位重建卡片（按钮不消失）；
   - **「打回重做」按钮**：由工具 `feishu_review_reject` 直调处理——撤销飞书任务的完成
     状态（与「标记完成」互逆）+ 台账状态回「进行中」+ 重建卡标注「已打回重做」。
     执行人重新完成后再次点「标记完成」会重新触发一张评价卡。
   **卡片上没有「提交」按钮**——点分就是打分，评语确认就是提交评语，二者独立、可多次操作。
3. **回调处理分工**：
   - `review_score`（点分）、`review_input`（评语确认）、`review_reject`（打回重做）：
     **框架直调工具处理，秒级完成，本技能不处理、不参与、不得编辑评价卡**；
     - 打回重做由工具 `feishu_review_reject` 直调：撤销飞书任务的完成状态
       （`completed_at` 清空，与「标记完成」互逆）+ 台账状态改回「进行中」+ 原位重建
       评价卡标注「已打回重做」。执行人重新点击「标记完成」时会再次触发评价卡推送，
       本技能不需要为打回做任何事；
   - 仅当直调失败回退到本技能时才处理（见下）。
   本技能收到回调时先看 `dispatch.handler` 是否指向本技能、`value.action` 是什么；
   直调工具已处理的回调不会到达本技能。
   **注意轮次后缀**：飞书 action id 每卡单次消费、Channel 按 `(message_id, action)` 去重，
   所以每次重建卡片都会把 action 名轮次 +1——回调的 `value.action` 形如
   `review_score_r3` / `review_input_r3` / `review_reject_r3`（`_rN` 后缀只表示轮次）。
   判断回调类型时按 `_r` 前缀匹配（`value.action` 以 `review_reject` 开头即打回），
   `value.round` 字段是当前轮次，重建卡片时用 `round+1`（同 `feishu_review_card_select` 的做法）。
4. 卡片回调约定：分数按钮与评语输入框的 `action_handlers` 分别映射到
   `feishu_review_card_select` / `feishu_review_input`（工具直调，秒级写台账+重建卡片）；
   「打回重做」映射到本技能（`company-todo-review`）。
   `business_context_json` 携带 `record_id`/`task_guid`/`owner_open_id`/`cycle_date`/`title`；
   按钮 `value` 里带 `action`（`review_score` / `review_input` / `review_reject`）、`score`、
   `record_id`、`title`、`owner_name`、`owner_open_id`、`cycle_date`、`task_guid`。

## 直调失败回退（仅此时本技能才处理评价卡回调）

仅当回调带着 `dispatch.handler` 指向本技能、但 `value.action` 是 `review_score` /
`review_input` / `review_reject`（框架直调失败的回退）时，按动作补做：

- `review_score`：写台账 `mentor打分`（Number 列，传 int 不传字符串），再调
  `feishu_review_card_select(card_action_json=...)` 用原回调 payload 重建卡片；
- `review_input`：取 `action.input_value` 写台账 `mentor评语`，再调
  `feishu_review_card_select(card_action_json=...)` 重建卡片（`value.score` 替换为会话历史里
  最近一条该 record 的 `review_score` 回调的真实分数，无历史则 0）；
- `review_reject`：调工具 `feishu_review_reject(card_action_json=...)` 补做打回
  （撤销任务完成 + 台账状态回「进行中」+ 重建卡标注「已打回重做」），不要自己手工拼。
- **禁止编造打分/评语**：只写本次回调 `value` 里实际携带的值。`score` 不在 1-5 时点分回调不写
  台账只重建卡片；**绝不**从会话历史、compacted history 或之前的评价里取数来"补写"本次回调。

## 卡片回调纪律（与 AGENTS.md 一致，此处不重复推导，只点名）

- **评价卡保持原状、可多次操作**：mentor 点分/确认评语后，**不要编辑评价卡**（不要替换成
  「已提交」「已评分」等只读样式）—— mentor 可能改分重交、补评语，同一张卡允许多次操作，
  每次操作覆盖该行对应字段即可。只有 todo 卡（tick/untick）才需要按行消费换状态，评价卡不需要。
- 写操作成功且卡片已经承载全部必要信息时，本轮以零 assistant 文本结束；只有警告、部分失败、
  权限问题或必要后续步骤才回复。
- snapshot 缺失或损坏时 `fail closed`，不假定是旧卡片、不臆造已匹配的 handler。

## 边界

- 评价的权威落点有两个：Bitable 台账（供统计/闭环判定读取）与 wiki 快照页（供人工回溯）。
  打分/评语由直调工具实时写台账；wiki 快照由 `company-todo-audit` 第 0 步（评价回写扫描）
  兜底同步，本技能只在「打回重做」时直接回写 wiki。
- 只有该条 todo 的 mentor 本人的评分算数；不接受负责人自己给自己打分、也不接受群聊场景下的评价卡
  （评价卡只私聊发给 mentor 本人）。
- 「打回重做」不写打分/评语，只把任务与台账状态回退（由 `feishu_review_reject` 直调完成），
  等下一次真正交付后再发新的评价卡。
