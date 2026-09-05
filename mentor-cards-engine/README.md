# mentor_cards — Mentor 周期报表卡 / Boss 总览卡生成器

> 用途：把《TODO LIST》飞书电子表格的填报数据 + **飞书审批（请假）** + **飞书考勤（打卡）**
> + **飞书通讯录（入职时间）** 渲染成 **8 张 mentor 摘要卡 + 1 张 boss 全公司总览卡**
> （飞书 Legacy 卡片 JSON），供海豚逐张私聊投递。
> 对应任务：TODO 体系 · mentor 摘要卡与 boss 统计卡（DDL 8-29），设计依据 `mentor-report-card-structure.md`（契约 v2）。
>
> **数据原则（2026-08-28 马晨柯定）**：代码里没有死数据。每项数据都有
> 「调用方式 + 数据来源」，海豚工作时按来源取数。**数据来源与取数方式全表见
> [`DATA_SOURCES.md`](DATA_SOURCES.md)**——发卡前先读它。

## 文件

| 文件 | 说明 |
|---|---|
| `fetch_leave_attendance.py` | **真实数据通道**：拉飞书通讯录（roster，权限受限自动保留旧值）+ 请假审批（近 90 天）+ 全员考勤（周期窗口，由 `--cycle` 推导）+ 全员入职时间 → `roster.json` / `data/leave.json` / `data/attendance.json` / `data/join.json`（依赖 `PSI_FEISHU_APP_ID/SECRET`，仅标准库） |
| `ledger_sources.json` | **各 mentor 台账来源清单**（每 mentor 一个台账 base 的地址：name/url/app_token/tables）。这是「从哪取数」的地址，不是数据；拿到某组台账链接填进去即自动切换台账口径 |
| `fetch_ledgers.py` | **台账拉取逻辑**（tenant token，只读，归一化字段）。可独立 CLI 跑，但**不再是发卡前置步骤**：`build_cards.py` 每次构建时自动调用同一逻辑现取（见下） |
| `build_cards.py` | 唯一入口：读 TODO 数据 + 请假/考勤/入职 + **各 mentor 台账（构建时现场从飞书现取；有则台账口径，无则人工核对档案）** → 统计 7 栏目 → 生成卡片 JSON。周期/窗口/截至时间/mentor open_id 全部运行时推导，无死数据 |
| `build_ledger.py` | ~~TODO LIST 本周期正文 → 台账行~~ **已废弃**（用户 2026-08-28 定：不要自己造台账，改读 mentor 真实台账） |
| `roster.json` | 全员通讯录映射（name ↔ user_id ↔ open_id），由海豚 `feishu_department_members` 刷新（fetch 脚本权限受限时不覆盖） |
| `data/manual_calibration.json` | **人工核对档案**（②③④ 兜底口径，`calibrated_at` 记录核对时间）；某组台账注册并拉到数据后自动让位 |
| `data/leave.json` | 请假实例（申请人/类型/起止/天数/事由/状态），由 fetch 脚本生成 |
| `data/attendance.json` | 全员逐日打卡结果（周期窗口），由 fetch 脚本生成 |
| `data/join.json` | 全员入职时间（name → join_date），由 fetch 脚本生成；**未按时判定的「入职后」依据** |
| `data/ledger_<mentor>.json` | 台账记录**审计副本 / 断网回退**（build_cards 每次构建现场现取后顺手落盘，含 latest_cycle）。卡片 ②③④⑥ 消费的是**现场拉取结果**，不是这份文件 |
| `mentor_cards.json` | 生成产物：`{mentor名: {"oid": open_id, "card": 卡片JSON}, "__boss__": {"card": 卡片JSON}}` |
| `../todo_list_parsed.json` | 解析后的 TODO 数据源（由 `--xlsx` 或 `analyze_todo_list.py` 维护） |

## 海豚调用路径（四步）

> 完整来源映射与切换规则见 **[DATA_SOURCES.md](DATA_SOURCES.md)**。以下为速查。

### 1. 拉真实请假/考勤/入职时间（每次发卡前必做）

```bash
python3 mentor_cards/fetch_leave_attendance.py --cycle 2026-08-28
```

生成 `data/leave.json`（近 90 天请假审批）+ `data/attendance.json`（周期日往前
6 天 ~ 周期日全员打卡）+ `data/join.json`（全员入职时间）+ 刷新 `roster.json`
（通讯录权限受限时自动保留旧值并告警，改用 `feishu_department_members` 刷新）。
无凭据或缺文件时 `build_cards.py` 自动降级到兜底口径，不报错。

### 2. 各 mentor 台账（**build_cards.py 自动现取，无需单独跑**）

台账不是预存数据：`build_cards.py` 每次构建卡片时，对 `ledger_sources.json` 中
已注册的 mentor 台账 base **现场从飞书现取最新记录**（tenant token，只读），
归一化后直接用于 ②③④⑥；顺手落盘 `data/ledger_<mentor>.json` 仅作审计副本 /
断网回退（现取失败才读它，且打 warn）。**某组台账链接拿到后，填入
`ledger_sources.json` 对应 mentor 条目，下次构建即自动切换台账口径**（②③④⑥）。
未注册台账的组自动回落人工核对档案（`data/manual_calibration.json`）。
（`fetch_ledgers.py` 仍可独立跑一次用于手动刷新副本，但不再是发卡前置步骤。）

### 3. 刷新 TODO LIST（数据源更新后必做）

```bash
# 用 feishu_doc_export 把《TODO LIST》（wiki H6icwLWn1iwpXAk73QMcA6MgnWc，sheet）
# 导出为 xlsx 存到工作区，然后：
python3 mentor_cards/build_cards.py --xlsx todo_list_source.xlsx
```

周期（最新列）、周期序号（列数）、请假/考勤窗口、数据截至时间全部自动推导。

### 4. 仅重新生成卡片（数据未变时）

```bash
python3 mentor_cards/build_cards.py
```

### 5. 发卡（海豚执行）

读 `mentor_cards/mentor_cards.json`，逐张调用：

```
feishu_message_send_card(receive_id=<entry["oid"]>, card_json=json.dumps(entry["card"]))
```

- 8 张 mentor 卡 → 各 mentor 本人 open_id（由 roster/通讯录解析）
- boss 卡 → `__boss__` 条目
- 卡片为**纯只读**（无按钮/回调），发出即终态；测试时改投测试者 open_id 即可

## 卡片结构（每张 mentor 卡 = 7 栏目 + 链接）

```
📋 周期报表 · 8.28 第16周期 · <mentor>团队      ← 卡头（红=有逾期/未填；绿=全员闭环；蓝=常规）
<mentor> mentor · 组内 N 人 · 数据截至 08-28 18:08
成员：张三✅ · 李四🏖 · 王五⚠️ · 赵六📅      ← 每名成员填报状态（📅=该周期尚未入职）
──────────────────────────────
① 人员概况   填报 x/N 人 · 请假中 y · 未按时 z（附名单）
             ⏰ 考勤异常（8.22–8.28）：姓名（迟到2/缺卡1）   ← 真实打卡
② 目标数量   大目标 a ｜ 小目标 b ｜ 本周期 TODO c（台账·截至X 或 人工核对 口径标注）
③ 完成情况   ✅已闭环 d · 🔄进行中 e · ⏳待开始 · 🏖顺延 · ⚠️逾期 f · 填报率 g%
④ 逾期明细   逐条红字：责任人｜任务摘要 — 标记（台账·超期未交付/自动标记/人工核对）
⑤ 请假标注   🏖 姓名 · 类型 起~止（X天） ✅已批准   ← 飞书审批真实数据
⑥ 评价概况   台账打分：平均 X ★ · 分布 · 评语 N 条（台账未填则「暂无评价数据」）
⑦ 趋势       近 8 期填报率 x/N(+a假) → … → y/N(+b假) 🟢↑
             （括号 +N 假 = 该周期日当天在假人数，按已批准请假区间覆盖当日判定，
               不受展示窗口限制回看各期；请假豁免填报）
──────────────────────────────
📎 数据源：[打开 TODO LIST 电子表格](wiki 链接)
📊 报表：[打开 <mentor> 台账](该 mentor 台账 base 链接)   ← 台账已注册时显示
note: TODO LIST · 请假（飞书审批）· 考勤（飞书打卡 8.22–8.28）· obj_token
```

boss 卡 = 全局指标 + 团队维度（8 行，每行 人数/填报/未填/请假/逾期/填报率）+ 全公司逾期明细（红字）+ 请假标注 + 考勤异常 + 趋势 + 数据源链接（卡片形态依据行已按用户要求移除）。

## 口径（诚实标注）

| 栏目 | 口径 | 可信度 |
|---|---|---|
| ① 人员概况 / ⑦ 趋势 | 结构性事实：最新列非空 = 已填报；**趋势分母只计该周期前已入职的人**（中途入职者入职前的周期不计入，如 7.24 期全公司分母 31→25）；**趋势括号 +N 假 = 该周期日当天在假人数**（按已批准请假区间覆盖当日判定，回看各期不受展示窗口限制，请假豁免填报） | ✅ 直接采信 |
| ① 未按时（⚠️） | **= 入职后 + 非请假 + 本周期没写**：请假/放假 🏖 豁免；入职日期晚于本周期日（未入职 📅）豁免；其余没写才算未按时。入职时间来自飞书通讯录 `join_time`（`data/join.json`，后台默认值 2026-03-01 不影响判定） | ✅ 通讯录 + 审批 |
| ① 请假中 / ⑤ 请假标注 | **飞书审批真实数据**：近 90 天 APPROVED 且与周期窗口重叠；未填报且请假 → 豁免不计未按时（如董修奇 8.03–9.01 暑假） | ✅ 审批系统 |
| ① 考勤异常 | **飞书打卡真实数据**：周期窗口迟到/早退/缺卡（NoNeedCheck 不计） | ✅ 打卡系统 |
| ② 目标数量 / ③ 完成情况 | **台账 ledger**（mentor 已填写评语/打分/状态后接管）；当前用**人工核对档案** `data/manual_calibration.json`（卡面标注核对日期） | ⚠️ 档案口径，卡面标注 |
| ④ 逾期明细 | 台账（状态=逾期，mentor 使用后）→ 关键词自动扫描补充（标注「自动标记」）→ 人工核对档案；请假豁免的未填报条目（董修奇）自动过滤 | ✅ 已核对 |
| ⑥ 评价概况 | 台账 mentor 评语/打分字段（mentor 填写后展示），否则「暂无评价数据」 | ⚠️ 待 mentor 填写 |

## 新周期维护清单（海豚每次跑前；详见 DATA_SOURCES.md）

1. 拉请假/考勤/入职/通讯录：`python3 mentor_cards/fetch_leave_attendance.py --cycle <周期日>`
2. 导出最新《TODO LIST》xlsx（新周期会**新增日期列**，历史列保留 → 趋势自动延长）
3. 跑 `--xlsx` 刷新（周期/序号/窗口/截至时间全部自动推导，无需改代码）
4. 按《真知团队信息档案》核对 ②③④ → 更新 `data/manual_calibration.json` 的
   `goals/closure/overdue` 与 `calibrated_at`（台账被 mentor 使用后此步自动消失）
5. 台账维护：`build_ledger.py` 产出台账行 → `feishu_bitable_create_records` 灌台账
   （②③④ 的台账口径来源；mentor 填写评语/打分/状态后卡片自动切换）
6. 生成后抽查 2-3 张卡（数字与 TODO LIST 表、请假/考勤核对）再批量发
