---
name: todo-growth-profile
description: "判/产「一个人跨周期的成长观察」的唯一口径（动态二层·个人成长档案与周期成长简报）。LOAD FIRST whenever asked 某人最近成长怎么样 / 给我看我的成长简报 / 这几个月的变化 / 长期组织贡献如何, or when producing a periodic personal growth brief. **不是打分评级,是有据可依的画像**:聚合 wiki 快照链、mentor 台账历史周期表、.todo-eval 连续性序列(及授权后的交付物/上级 comments)生成「个人成长简报」——**人 vs 自己**多周期序列,严禁人 vs 人。每条观察必须带证据引用;指标先少后多、宁缺毋滥;定性词沿用完成度纪律,不发明分数/评级/百分制;覆盖周期少于阈值时明说「样本不足」不硬出结论;简报只发本人 + mentor/上级。判定口径读 config/todo-sop.yaml 的 growth 段。与 todo-completion-standard(单条做完没)、todo-truthfulness-check(真不真)、company-todo-audit(闭环)分界:本文只做跨周期聚合观察,不重复判单条;带人 scale up / peer 对比属 P3 缓做。Not for 判单条完成度/真伪/闭环/填报格式。"
category: knowledge-base
---

# TODO 成长观察总纲（动态二层）

CEO 口径:**少输入**(交付物、上级 comments),希望以 todolist **完全评价一个人的动态成长与组织贡献**——招人/淘汰靠数据而非人治、公平公正有证据。本文是这份观察的唯一口径:它**不是打分评级**,是**有据可依的画像**:拿已积累的周期数据生成「个人成长简报」,人 vs 自己,不跨人比。

> 判定口径(指标清单、样本阈值)读 `config/todo-sop.yaml` 的 `growth` 段,用户可编辑;本文保留观察纪律与「指标怎么算、从哪取」。

## When to use

- 「某人最近成长怎么样」「这几周/几个月有什么变化」「长期组织贡献如何」「给我看我的成长简报」「进展趋势」。
- 按需生成某人的周期成长简报;周期推送任务由用户在与 agent 对话中用 `schedule_manage` 自建(本文不携带 schedule 实体)。

## When not to use

- 判单条「做完没」→ [`todo-completion-standard`](todo-completion-standard/SKILL.md)。
- 判「真不真」→ [`todo-truthfulness-check`](todo-truthfulness-check/SKILL.md)。
- 判台账闭环/回流 → [`company-todo-audit`](company-todo-audit/SKILL.md)。
- 判填报格式/按时按量 → [`todo-writing-standard`](todo-writing-standard/SKILL.md)。
- **peer 对比 / 带人 scale up** → 属 P3 缓做(CEO 口径),本文不做。

## 分界

完成度/真伪/闭环/格式判**单条或单期**;本文只做**跨周期聚合观察**(把已判过的结果与台账历史串成个人轨迹),不重复判单条、不发明新档位。产出**人 vs 自己**的多周期序列,**严禁人 vs 人**(peer 对比/带人 scale up 属 P3)。成长观察是**画像,不是考核**。

## 数据源(全部为既有权威存储,不臆造新表)

| # | 数据源 | 怎么取 | 用途 |
|---|---|---|---|
| ① | LLM wiki 快照链(`company-todo-sync` 每周期写《X todo <周期>》页,不覆盖、按时间累积) | `wiki_read` 逐周期快照页 | 事实序列:各期填报了哪些目标/todo、怎么变化 |
| ② | mentor Bitable 台账**历史周期表**(`feishu_mentor_ledger_cycle_table` 每周期一张表;含 状态/mentor打分/mentor评语/外部成果/友商对比/任务GUID) | `feishu_bitable_search_records` 逐周期表读取 | 结构化数据:状态、打分、评语、成果 |
| ③ | `.todo-eval/` 连续性序列(`company-todo-audit` 已按 (cycle,person,item) 落盘,含 verdict) | 读 workspace `.todo-eval/YYYY-MM-DD.json` | 闭环/回流/持续逾期序列 |
| ④ | (可选,需授权)交付物 / 上级 comments(E2 证据) | `feishu_doc_read` / `feishu_api` 任务域接口 | 补硬证据,降「待确认」占比 |

读取纪律:所有数据「查不到 = unavailable」要明说;不凭印象补数。

## 指标集(先少后多,每条必须**可回溯**到具体台账行/快照页/评测记录,宁缺毋滥)

| 指标 | 数据源 | 取法口径 |
|---|---|---|
| 闭环率 | ② 各周期台账表 | 该周期「已闭环」行数 / 总行数,按周期算 |
| 按时率 | ② + 请假事实 | 按时交付占比;请假顺延豁免口径沿用 `company-todo-fill-check`(顺延不计逾期) |
| 回流次数 | ③ `.todo-eval` | verdict 含 回流/持续逾期 的计数 |
| 持续逾期段 | ③ `.todo-eval` | 连续未闭环的周期段(起止周期) |
| mentor打分趋势 | ② 台账 `mentor打分` | 人 vs 自己 的跨周期打分序列 |
| 承担层级迁移 | ② 台账 `层级` | todo → 小目标 → 大目标 的迁移;若带人(mentor 角色)仅作背景记录,不判效果 |
| 外部成果累计 | ② 台账 `外部成果`/`友商对比` | 非空且量化的行,逐条列证据 |

指标清单本身在 `config/todo-sop.yaml` 的 `growth.indicators`;将来增删指标改 config,不动本文纪律。

## 产出:周期「个人成长简报」

- **形态**:人 vs 自己 的多周期序列(周期 1..N),把①②③④按周期对齐;每条观察带证据引用(快照页 `[[X todo <周期>]]`、台账 record_id、task_guid、`.todo-eval` 日期)。
- **定性词**:沿用完成度纪律(`已完成` / `推断已完成／待确认` / `进行中` / `未闭环`,取自 config 的 `completion_verdicts`);**不发明分数/评级/百分制/自造词**。
- **收尾**:「要定论还缺什么」清单(哪条需谁确认、哪个周期缺哪份数据)。
- **投递**:只发本人 + 其 mentor/上级;不向无关成员披露。

报告示例:

```text
个人成长简报 · 张三 · 覆盖 8.19-9.02(3 个周期)
| 周期 | 闭环率 | 按时率 | 回流 | mentor打分 | 承担层级 | 外部成果 |
|---|---|---|---|---|---|---|
| 8.19 | 2/3 | 100% | 0 | 3 | todo | 无 |
| 8.24 | 3/3 | 100% | 0 | 4 | 小目标 | 用户数+200(证据见台账 r_1234) |
| 8.26 | 2/2 | 1 项顺延 | 0 | 4 | 小目标 | — |
观察:层级从 todo 升到小目标,打分 3→4,闭环稳定。证据:台账 8.24 行 r_1234、快照页 [[张三 todo 8.24]]。
要定论还缺:8.26 外部成果未填,需本人补;8.19 前更早周期无 .todo-eval 记录,不作为趋势依据。
```

## 纪律(与其它判定 skill 同源,不许放宽)

1. **先验印象隔离**:本人自评(「我最近比较努力」)、他人评价、历史印象不作证据,只进「背景」区并标来源。
2. **取证对称**:一次涉及多人时,对每个人用同一条链、同一深度;某级 unavailable 就把全体降到同一深度重判。
3. **查不到 = unavailable 明说**:读表/读 wiki/读 .todo-eval 失败要写明,不顺势补数。
4. **样本不足不硬出结论**:覆盖周期 < `config/todo-sop.yaml` 的 `growth.min_cycles` 时,明说「样本不足」,给已有数据即可,不给成长定论。
5. **不跨人对比**:本 skill 只产人 vs 自己的序列;peer 对比/带人效果是 P3,CEO 口径缓做。
6. **不发明分数与评级**:只引用台账 `mentor打分` 等既有字段;定性词只用完成度纪律既有词。
7. **不向无关成员披露**:简报只发本人 + mentor/上级。

## Boundaries

- 不编造打分、成果数字、任务号;查不到就是 unavailable,不是「应该有」。
- 成长观察是**有据可依的画像,不是绩效定论**:不加评价性措辞,不替代考核。
- 简报只回答「这段周期的成长轨迹长什么样、缺什么数据」;要不要据此处理由用户拍板。
- 判定口径(指标/阈值)在 config;换公司只改 `config/todo-sop.yaml`。
