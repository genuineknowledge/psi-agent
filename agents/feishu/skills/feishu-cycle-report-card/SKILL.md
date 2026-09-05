---
name: feishu-cycle-report-card
description: "生成并私聊投递「周期报表卡」——mentor 周期报表卡（每组一张）与 boss 全公司 TODO 总览卡，均为 v9「六区数字格」版式（schema 2.0，灰底大数字 + 语义色 + 明细走链接）。Use when 有人要求发某个 mentor/团队的周期报表、发老板的全公司 TODO 总览、或 company-todo-sync 周期跑完后要推报表时。核心是跑生成器脚本 六区数字卡-真实卡片v9.py（实时现取各组台账 + 读 data/ 下考勤/请假/入职缓存 → 产出 mentor_cards/mentor_cards_v9.json），再用 feishu_message_send_card 把 {mentor}.card / __boss__.card 原样投递。台账数据实时，考勤/请假/通讯录需先跑 fetch_leave_attendance.py 刷新。区别于 feishu-todo-card（那是可勾选待办卡，这是纯只读报表卡）。"
category: productivity
---

# 飞书周期报表卡（v9 六区数字格）

把公司 TODO 体系的数据，渲染成**纯只读**的周期报表卡，私聊投递：

- **mentor 周期报表卡**：每个 mentor 一张，卡头「📋 周期报表 · {周期} · {mentor}团队」，
  六区数字格呈现 ①人员概况 ②目标数量 ③完成情况 ④评价概况 + 逾期/请假/考勤/趋势（走链接）。
- **boss 全公司总览卡**：一张，卡头「📊 全公司 TODO 总览 · {周期}」，顶部三行灰底核心数字
  （①填报/在册/未按时 ②已闭环/进行中/逾期 ③团队/请假/考勤异常）+ 各明细表链接 + 趋势。

**这是「看」的卡，不是「点」的卡**——没有按钮/回调（`handlers` 恒空）。要发**可勾选**的
待办卡，用 [[feishu-todo-card]]，别用本技能。

## 什么时候用

- 有人说「给孙逊团队发周期报表」「把这周期的报表推给各 mentor」「发老板全公司 TODO 总览」。
- `company-todo-sync`（[[company-todo-sync]]）本周期采集/派发跑完后，按第 5 节「报表只推给 mentor
  本人」推卡。
- 想让老板不点开明细表也能从卡片直接拿到关键数字时（六区数字格就是为这个设计的）。

## 版式的权威来源（别搞错文件）

产出 v9 六区数字格版式的**唯一生成器**是 workspace 根目录的
`六区数字卡-真实卡片v9.py`。它 `import build_cards`（复用 `mentor-cards/` 的数据层）但
自己实现 v9 的 schema 2.0 渲染。

⚠️ **不要**跑 `mentor-cards/build_cards.py` 来发卡——那是更早的 legacy 版式（`config.wide_screen_mode`
老卡片，非六区数字格），产出和 v9 不一致。`build_cards.py` 只作数据层被 import，不直接发。

## 四步操作

### 1. 刷新考勤/请假/通讯录/入职（每次发卡前，需要飞书权限）

```bash
python mentor-cards/fetch_leave_attendance.py --cycle <YYYY-MM-DD>
```

拉飞书通讯录 + 请假审批（近 90 天）+ 全员考勤（周期窗口）+ 入职时间 →
`mentor-cards/data/{leave,attendance,join}.json` + `roster.json`。
**依赖 `PSI_FEISHU_APP_ID/SECRET`，且 app 需有通讯录/审批/考勤读取权限**；
权限不足（如 `40004 no dept authority`）时这步失败，生成器会降级读 `data/` 下的旧缓存，
卡面「数据截至」时间戳会停留在旧值——如实告诉用户这部分不是最新。

### 2. 跑生成器（实时现取台账 + 合流数据 → 卡片 JSON）

```bash
python 六区数字卡-真实卡片v9.py
```

产出 `mentor-cards/mentor_cards_v9.json`：
`{mentor名: {"oid": open_id, "card": 卡片JSON}, ..., "__boss__": {"card": 卡片JSON}}`。
已注册台账的组（见 `mentor-cards/ledger_sources.json`）**实时从飞书现取**最新记录；
未注册的组回落人工核对档案 `mentor-cards/data/manual_calibration.json`。
生成器还会幂等建/刷新各组「海豚·XX组·周期明细」飞书明细表并把链接嵌进卡片。

### 3. 读产物，取要发的卡

从 `mentor-cards/mentor_cards_v9.json` 取：
- 某 mentor 的卡：`data["孙逊"]["card"]`，收件人 open_id：`data["孙逊"]["oid"]`。
- boss 总览卡：`data["__boss__"]["card"]`（无 oid，收件人是老板，由你/用户指定）。

### 4. 私聊发卡（原样投递，不改版式）

用 `feishu_message_send_card`，`card_json` 传卡片 JSON 字符串，`receive_id_type="open_id"`：

```
feishu_message_send_card(
    receive_id=<收件人 open_id>,
    card_json=<json.dumps(card)>,
    receive_id_type="open_id",
)
```

报表卡是只读的，`action_handlers_json` 留 `"{}"`、`multi_use=False` 即可。

## 数据实时性（诚实边界）

| 数据 | 实时性 | 来源 |
|---|---|---|
| 台账（目标/打分/闭环/逾期） | ✅ 现取 | 已注册组实时拉飞书 base；未注册回落人工核对档案 |
| 考勤/请假/通讯录/入职 | 取决于第 1 步 | 跑了 `fetch_leave_attendance.py` 才新；否则读 `data/` 旧缓存 |

**发卡前如实核对卡面「数据截至」时间戳**：若第 1 步因权限失败，明确告诉用户考勤/请假部分是旧的。

## 收件人 open_id 是按 app 隔离的

配置里预存的 open_id 可能是**别的 app** 签发的，直接用会报 `open_id cross app`（99992361）。
本 app 下的 open_id：私聊场景取当前消息的 `sender_open_id`；批量推 mentor 时用
`mentor-cards/roster.json`（本 app 通讯录）解析姓名 → open_id。

## 相关

- [[company-todo-sync]] — 上游：采集/派发跑完后推本报表卡。
- [[company-todo-audit]] — 闭环判定，报表的完成情况口径依赖它。
- [[feishu-todo-card]] — 可勾选待办卡（对比：本技能是只读报表卡）。
- [[card-dsl]] — 通用卡片 DSL；本技能的 v9 版式是专用生成器，不走通用 DSL 模板。
