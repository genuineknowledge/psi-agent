# mentor 报表卡 · 数据来源与调用方式映射

> **原则（马晨柯 2026-08-28 定）**：任何内容不要定成死的数据，而是定位一种
> **调用方式 + 数据来源**。海豚（Haitun AI）工作时按需求去对应来源取数，
> 而不是改代码里的常量、校准表或映射。
>
> 本文档是 mentor 摘要卡 / boss 总览卡全部数据项的「来源清单」：每项数据 →
> 权威来源 → 海豚取数的调用方式 → 落盘文件 → 刷新时机。**代码里没有数字**；
> 数字全部来自下列来源之一，来源缺失时卡片明确标注口径并降级。

## 一、数据流总览

```
飞书在线来源（只读）                    本地落盘（fetch 脚本产物）          卡片
─────────────────────────────────      ───────────────────────────      ────────
《TODO LIST》电子表格（wiki）  ──导出──→ todo_list_source.xlsx ──解析──→ todo_list_parsed.json ─┐
     (7.24~最新周期列=填报正文)              （海豚 feishu_doc_export）                             │
各 mentor 台账多维表格（bitable） ──现取──→（build_cards.py 每次构建时现场拉取，         │
     (每 mentor 一个 base；来源清单          tenant token 只读，归一化字段，                  ├─→ build_cards.py
      ledger_sources.json；mentor 填写       含最新周期；②③④⑥ 台账口径；顺手                │    （唯一入口，
      层级/状态/打分/评语)                   落盘 data/ledger_<mentor>.json 仅作               │     按来源取数，
                                              审计副本 / 断网回退，不预存不依赖）              │     无死数据）
飞书审批（请假 approval）     ──拉取──→ data/leave.json（近 90 天 APPROVED）                       │    （唯一入口，
飞书考勤（打卡 attendance）  ──拉取──→ data/attendance.json（周期窗口逐日）                        │     按来源取数，
飞书通讯录（contact）        ──拉取──→ roster.json（36人 name↔user_id↔open_id）                   │     无死数据）
飞书通讯录（join_time）      ──拉取──→ data/join.json（入职时间）                                  │
人工核对档案（《真知团队信息档案》核对） → data/manual_calibration.json（②③④ 兜底口径） ─────────┘
```

> **台账来源切换**：某组台账链接拿到后，填入 `ledger_sources.json` 该 mentor 条目
> （name/url/app_token/tables），**下次 `build_cards.py` 构建时自动现取**，即自动切换该组
> ②③④⑥ 为台账口径；未注册台账的组保持人工核对档案口径（卡面标注）。台账不预存、
> 不依赖落盘快照（落盘仅为审计/回退副本）。**不再由海豚自己解析 TODO 造台账**
> （build_ledger.py 已废弃，2026-08-28 用户定：自己造的台账不可靠，不要建立和参考）。

## 二、数据项 → 来源 → 海豚取数方式

| # | 数据项 | 权威来源 | 海豚取数调用方式 | 落盘 | 刷新时机 |
|---|--------|----------|------------------|------|----------|
| 1 | 周期（最新列）/ 周期序号 | **《TODO LIST》表头**（最新一列日期 = 本周期） | `feishu_doc_export` 导出 xlsx → `build_cards.py --xlsx`（代码自动取 `date_cols[-1]`，序号=列数） | `todo_list_parsed.json` | 每周期导出后 |
| 2 | 全体成员（name↔user_id↔open_id） | **飞书通讯录** | `feishu_department_members(recursive=True)` 全量（应用权限范围外时用用户身份）；fetch 脚本 `find_by_department` 受限时保留旧值 | `roster.json` | 人员变动时；发卡前 |
| 3 | mentor 收卡 open_id | **roster.json（通讯录按名解析）** | `mentor_oids()` 自动解析；roster 缺失回落 `_MENTOR_OIDS_FALLBACK`（代码兜底，仅警告） | — | 同上 |
| 4 | 请假（类型/起止/天数/事由/状态） | **飞书审批**（approval_code `99EEC396-...`） | `fetch_leave_attendance.py --cycle <周期日>`（近 90 天实例 + 逐条详情） | `data/leave.json` | 每次发卡前 |
| 5 | 考勤（逐日打卡结果） | **飞书考勤** | 同上（周期日往前 6 天 ~ 周期日，自动推导） | `data/attendance.json` | 每次发卡前 |
| 6 | 入职时间（join_time） | **飞书通讯录**（contact/users/:user_id） | 同上（逐个查询；后台未填者返回平台默认值，判定逻辑不受影响） | `data/join.json` | 每次发卡前 |
| 7 | 请假豁免窗口 / 考勤窗口 / 数据截至时间 | **由周期日推导**（窗口=周期日±7 天；截至时间=数据文件 fetched_at） | `runtime_windows()` / `data_as_of()` 运行时计算 | — | 自动 |
| 8 | ① 人员概况 / ⑦ 趋势 | **TODO LIST 填报列 + 请假(4) + 入职(6)** | `member_status()` 判定链（filled→leave→not_joined→unfilled） | — | 发卡时 |
| 9 | ② 目标数量（大/小/TODO） | **该 mentor 台账 ledger**（层级字段：大目标1/小目标1-4/todo1-3） | `build_cards.py` 构建时按 `ledger_sources.json` **现场现取**（`fetch_ledgers.fetch_ledger_for`，tenant token 只读）→ `goal_counts_from_ledger()`（最新周期行）；未注册台账回落**人工核对档案**；落盘 `data/ledger_<mentor>.json` 仅为审计/回退 | 在线台账（现取） + `data/manual_calibration.json` | 构建卡片时自动现取 |
| 10 | ③ 完成情况（闭环/进行中/待开始/顺延/逾期） | **台账 ledger（状态字段：待开始/进行中/已交付/请假顺延）** | `closure_from_ledger()`（已交付=闭环；逾期=待开始且截止已过）；回落人工核对档案 | 同上 | 同上 |
| 11 | ④ 逾期明细 | **台账 ledger（状态=待开始 且 截止日期已过 → 台账·超期未交付）→ 关键词自动扫描补充 → 人工核对档案** | `overdue_from_ledger()` + `auto_overdue_extra()`（扫描 delay/延期/延后等词，跳过豁免/未入职） | 同上 | 发卡时（现取） |
| 12 | ⑤ 请假标注 | **飞书审批**（同 4） | `group_leaves()`（窗口重叠 + 合并同人相邻请假） | `data/leave.json` | 发卡时 |
| 13 | ⑥ 评价概况 | **台账 ledger（mentor打分/mentor评语字段，mentor 填写）** | 台账该组最新周期行有打分时展示（平均分/分布/评语条数），否则「暂无评价数据」 | 在线台账（现取） | mentor 填写后 |
| 14 | 数据源链接（TODO LIST / 各 mentor 台账） | **固定文档地址**（在线来源本身） | 代码 `TODO_LIST` 常量 + `ledger_sources.json`（这是「来源地址」不是数据） | — | 不变 |

## 三、口径与切换规则（诚实标注）

| 栏目 | 当前口径 | 台账接管条件 | 卡面标注 |
|------|----------|--------------|----------|
| ② 目标数量 | 已注册台账的组=台账（最新周期行层级计数）；未注册组=人工核对档案 `data/manual_calibration.json`（2026-08-28 17:35 核对） | 台账在 `ledger_sources.json` 注册 + `build_cards.py` 构建时**现场现取成功** | 「台账·截至MM-DD」/「人工核对 YYYY-MM-DD」 |
| ③ 完成情况 | 同上（台账状态字段 / 人工核对档案） | 同上 | 「台账·截至MM-DD」 |
| ④ 逾期明细 | 台账（状态=待开始 且 截止已过 → 台账·超期未交付）+ 自动扫描 | 同上 → 台账状态派生 | 条目标记「台账·超期未交付」/「自动标记」 |
| ⑥ 评价概况 | 台账（mentor打分/评语，有则展示） | 台账有打分行 | 「台账·截至MM-DD」 |
| ① 人员概况 / ⑦ 趋势 | 结构化真源（TODO 列 + 审批 + 通讯录入职） | — | 直接采信 |
| ⑤ 请假 / 考勤 | 飞书审批 / 打卡真实数据 | — | 「✅已批准」/ 异常红字 |

> **为什么未注册组不用台账**：台账是 mentor 自己维护的真实工作台账（层级/状态/
> 打分/评语字段），**不再由海豚解析 TODO 正文造台账**（build_ledger.py 已废弃，
> 用户 2026-08-28 定：自己造的台账有多处错误，不要建立和参考）。某组台账链接
> 拿到后填入 `ledger_sources.json` 即自动切换。

## 四、海豚新周期发卡流程（全部按来源取数，不碰代码）

```bash
# 0.（可选）人员变动时刷新通讯录：feishu_department_members(recursive=True) 更新 roster.json

# 1. 拉真实数据（通讯录安全阀自动保留旧值；窗口由 --cycle 推导，默认今天）
python3 mentor_cards/fetch_leave_attendance.py --cycle <周期日 YYYY-MM-DD>

# 2. 拉各 mentor 台账（台账已注册的组；新台账链接 → 先填入 ledger_sources.json）
python3 mentor_cards/fetch_ledgers.py

# 3. 导出最新《TODO LIST》xlsx（feishu_doc_export，新周期会新增日期列）
#    → 存为 todo_list_source.xlsx，然后：
python3 mentor_cards/build_cards.py --xlsx todo_list_source.xlsx
#    （build 自动：最新列=周期、序号=列数、窗口/截至时间由数据推导）

# 4. 仅数据未变时重新生成
python3 mentor_cards/build_cards.py

# 5. 抽查 2-3 张卡（数字与 TODO LIST 表/审批/考勤/台账核对）→ 读 mentor_cards.json 逐张发卡
```

**周期切换时唯一要人工做的**：拿到新的/遗漏的 mentor 台账链接 → 填入
`ledger_sources.json`；按《真知团队信息档案》核对 ②③④（仅对尚未注册台账的组）
→ 更新 `data/manual_calibration.json`（`calibrated_at` 改为核对时间）。台账
数字由 mentor 填写维护，属于台账运营，不是代码改动。

## 五、台账接入（待办：让 ②③④⑥ 全台账化）

1. **拿到其余 7 组台账链接**：当前只注册了孙逊（TODO 台账-孙逊，
   base `C6sQbhhj1a5BRkslxjOcY2PPnYc`，表 `tblhfghF8Iz2Fb1j` + 归档 `tblyPdcD9qzvAMGU`）。
   其余组的台账 base 链接待各 mentor/用户提供，填入 `ledger_sources.json` 即自动切换；
2. 台账字段统一（孙逊组 schema 为标准）：周期日期/负责人/mentor/层级(大目标1·小目标1-4·todo1-3)/
   父项/标题/截止日期/状态(待开始·进行中·已交付·请假顺延)/闭环五要素/mentor打分/
   agent建议分/mentor评语/外部成果/友商对比/任务GUID；
3. 已注册组的台账周期若落后于 TODO LIST 周期（如孙逊台账最新周期 8.17 < 8.28），
   卡面标注「台账·截至08-17」，待 mentor 更新台账后自动推进。
