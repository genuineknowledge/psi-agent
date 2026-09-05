---
name: feishu-todo-reminders
category: productivity
description: "周一三五 TODO 三张提醒卡（v9 六区数字格）。① 填报催办卡 feishu_todo_fill_reminder —— 谁还没填 todo list（三列大数字 已填/应填/未填 + 未填点名 + 请假豁免 + 去填写按钮），group 群发总览 / dm 私聊本人。② 规范体检卡 feishu_todo_spec_check —— 检查填了的人写得规不规范（合规/待完善/缺必填三档，按人问题清单，每条带「已修正」按钮），启发式判定、机器初判。③ mentor 检查确认卡 feishu_mentor_check_reminder —— 3 点后提醒 mentor 检查下级 todo，列组内成员概况 + 「✅ 已检查完成」确认按钮，点击原地更新为已确认态。数据源 todo_list_parsed.json + build_cards 填报口径。Use when 周一三五要催填/查规范、或要提醒各 mentor 核查组内 todo 时。区别于 feishu-cycle-report-card（那是周期报表/总览），本技能是填报环节的三张提醒卡。"
---

# 周一三五 TODO 提醒卡（三张）

TODO 填报环节的三张 v9 六区数字格卡片，覆盖「催填 → 查规范 → mentor 核查」闭环。
都读 `todo_list_parsed.json`（date_cols + people[{name,mentor,cols}]），填报状态判定
统一走 `build_cards.member_status`（唯一口径），确定性、无需大模型。

配色/版式沿用 v9（灰底大数字 + 语义色 + 海豚三号落款），与 [[feishu-cycle-report-card]]
同一视觉体系。三张都是「提醒/核查」用途，区别于报表卡的「总览」用途。

## 三个工具

| 卡 | 工具 | 何时用 |
|---|---|---|
| 填报催办卡 | `feishu_todo_fill_reminder` | 周 1/3/5 截止前，催没填 todo 的人 |
| 规范体检卡 | `feishu_todo_spec_check` | 填了之后，检查写得规不规范（全员/群） |
| mentor 检查确认卡（组长卡） | `feishu_mentor_check_reminder` | **3 点后**，提醒 mentor 核查下级 todo |
| 组员个人卡（组员卡） | `feishu_member_todo_card` | 给组员本人：填了没 + 规范 + 本周期 todo 清单 |

### ① 填报催办卡 `feishu_todo_fill_reminder`

三列大数字（已填 / 应填 / 未填）+ 未填点名区 + 请假豁免行 + 「去填写」按钮。
卡头自适应：有人没填=红，全员齐=绿。两种发法：

```
# 群发总览（发管理群，含未填点名，公开施压）
feishu_todo_fill_reminder(receive_id=<chat_id>, receive_id_type="chat_id",
                          mode="group", todo_list_url=<TODO LIST 链接>)
# 私聊本人（只发未填者本人，不点名别人）
feishu_todo_fill_reminder(receive_id=<未填者 open_id>, mode="dm",
                          dm_name=<姓名>, todo_list_url=<链接>)
```

应填 = 已填 + 未填（请假/未入职的人不计、不点名）。

### ② 规范体检卡 `feishu_todo_spec_check`

三列大数字（合规 / 待完善 / 缺必填）+ 按人问题清单（红在前），每条带「已修正」按钮
（回调 `spec_recheck`，点后可重新校验该人）。规范口径对齐 [[company-todo-sync]] 字段规则：

- 大目标 / 小目标：必填 标题 + 截止日期；大目标选填 友商对比 / 外部成果
- todo：必填 标题 + 截止日期 + 验收人

```
feishu_todo_spec_check(receive_id=<chat_id 或 open_id>, list_url=<完整清单链接>,
                       with_button=True)          # only_name=<姓名> 只查一人（私聊本人）
```

**判定是启发式（正则抓截止日期、关键词抓验收人），会漏判/误判**，卡面已注明「机器初判，
以人工复核为准」。要更准需接大模型理解填报语义（换掉 `_check_person` 判定即可，结构已留好）。

### ③ mentor 检查确认卡 `feishu_mentor_check_reminder`（一个工具两用）

**发提醒**：列出该 mentor 组内成员本周期填报/规范概况（三列 已填/未填/待完善 + 成员逐行）+
底部「✅ 已检查完成」按钮，私聊发给 mentor。

```
feishu_mentor_check_reminder(mentor_open_id=<mentor open_id>, mentor_name=<团队名>,
                             todo_list_url=<链接>)
```

**处理点击**：mentor 点「已检查完成」→ 回调 `mentor_check_done` → 同一工具处理
`card_action_json`：记录该 mentor 本周期已确认（存 AppData `mentor-check-state/`）+
`edit_card` 把卡片原地更新为「已确认检查完成 · 时间」态、按钮消失。发送时须带
`action_handlers_json={"mentor_check_done":"feishu_mentor_check_reminder"}`（工具已内置）。

组织结构（谁是谁的下级）取自 `todo_list_parsed.json` 每人的 `mentor` 字段。
mentor 收卡 open_id 用 `mentor-cards/roster.json`（本 app 通讯录）按名解析。

### ④ 组员个人卡 `feishu_member_todo_card`

给组员**本人**一张卡看清自己本周期的情况：三列（填报状态 / 规范 / 待改项）+
待完善项清单 + 本周期 TODO 清单 +「去修改 TODO LIST」按钮。

```
feishu_member_todo_card(receive_id=<组员 open_id>, member_name=<姓名>,
                        todo_list_url=<链接>)
```

「组长卡 + 组员卡」配对使用：③ 发给 mentor 看全组，④ 发给每个组员看自己。
卡头自适应：未填=红，缺必填=红，待完善=橙，规范=绿。

## 数据新鲜度（发卡前）

填报状态、规范判定读 `todo_list_parsed.json`（TODO LIST 解析结果）。请假豁免读
`mentor-cards/data/leave.json`——发卡前跑 `fetch_leave_attendance.py --cycle <日期>`
刷新请假/考勤（需 `PSI_FEISHU_APP_ID/SECRET`）。通讯录逐用户可读，但 `find_by_department`
受应用「通讯录数据可见范围」限制时会降级保留现有 roster（不阻塞发卡）。

## 定时触发（3 点后 mentor 核查）

要「3 点后自动提醒 mentor」，挂定时任务：`schedule_manage(action="create",
cron="0 15 * * 1,3,5", fire="tool", tool="feishu_mentor_check_reminder", ...)`
对每个 mentor 各发一张。与 [[company-todo-sync]] 的 15:00 采集派发同一节奏。

## 相关

- [[feishu-cycle-report-card]] — 周期报表/总览卡（本技能是填报环节提醒卡）。
- [[company-todo-sync]] — 采集派发主流程，报表/提醒的上游。
- [[feishu-todo-card]] — 可勾选待办卡。
- [[card-dsl]] — 通用卡片 DSL。
