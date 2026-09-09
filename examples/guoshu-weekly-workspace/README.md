# 国数周报 Agent — Demo Workspace

可跑通的周报问答 demo：agent 包经 MCP 连接周报取数服务，用自建 mock 库打通全链路。
入口组的真实服务就绪后，改 `GUOSHU_WEEKLY_MCP_URL` 即可切换，agent 侧不改代码。

对应方案文档《国家数据集团周报 Agent 开发方案》第 1 期「骨架打通」。

## 快速开始

前置：MySQL 8.4 已起，`weekly_mock` 库已导入（见「数据层准备」）。

```bash
# 1. 起 mock 取数服务
cd mock-mcp
python server.py --port 18900

# 2. 另开一个终端，跑契约测试
cd ..
export GUOSHU_WEEKLY_MCP_URL=http://127.0.0.1:18900/mcp
export GUOSHU_WEEKLY_MCP_TOKEN=demo-token
python tests/smoke_test.py
```

预期 `415/415 passed`。

## 数据层准备

用原生 MySQL 8.4 而非翻译层：396 道参考 SQL 全部原样可跑（100%），
执行计划来自与生产同一个优化器。

```bash
# 免安装 ZIP，整个装在一个目录里，卸载 = 停服务 + 删目录
# https://cdn.mysql.com//Downloads/MySQL-8.4/mysql-8.4.11-winx64.zip

mysqld --defaults-file=my.ini --initialize-insecure --console
mysqld --defaults-file=my.ini --console

mysql -u root -e "CREATE DATABASE weekly_mock CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
mysql -u root --default-character-set=utf8mb4 weekly_mock < weekly_mock-full-20260817.sql

# 只读用户：让「只读」由数据库强制，而不只是注释里的声明
mysql -u root -e "
CREATE USER 'weekly_ro'@'127.0.0.1' IDENTIFIED BY 'weekly-ro-2026';
GRANT SELECT ON weekly_mock.* TO 'weekly_ro'@'127.0.0.1';"
```

`my.ini` 要点：`character-set-server = utf8mb4`、`collation-server =
utf8mb4_0900_ai_ci`（与 dump 一致，中文不乱码）、`bind-address = 127.0.0.1`
（本机演示，零远程暴露面）。

**不要写 `default_authentication_plugin`** —— 该变量在 MySQL 8.4 已移除
（属 8.0 时代），写了会让 `--initialize` 直接失败。8.4 默认就是
`caching_sha2_password`，无需覆盖。

导入后校验行数请用 `SELECT COUNT(*)`，**不要信
`information_schema.TABLE_ROWS`** —— 那是 InnoDB 估算值，实测 158 行的表读出 14。

预期 12 张表：task 158、task_attachment 543、task_board 2、task_category 47、
task_group_detail 55、task_group_progress_history 404、task_milestone 602、
task_progress 1068、task_progress_import 20、task_workflow_action 1613、
task_workflow_submission 470、task_year_goal 387。

### 接进 psi-agent 对话

```bash
export GUOSHU_WEEKLY_MCP_URL=http://127.0.0.1:18900/mcp
export GUOSHU_WEEKLY_MCP_TOKEN=demo-token

psi-agent gateway --default-agent examples/guoshu-weekly-workspace
```

Windows 上 `--listen` 必须带 `http://` 前缀；从 Git Bash 启动网关会让子进程挂
`0xC0000142`，改用 PowerShell。

## 结构

```
guoshu-weekly-workspace/
├── systems/system.py          # system prompt：取数纪律 + 口径硬约束 + 角色化输出
├── tools/
│   ├── _weekly_config.py      # 环境变量解析、URL 校验（/mcp + 非 loopback 强制 HTTPS）
│   ├── _weekly_mcp.py         # MCP 客户端（惰性建连、supervisor 线程、只读重试）
│   ├── weekly_schema.py       # 结构与字段字典
│   ├── weekly_query.py        # 任务查询（列表 / 详情）
│   ├── weekly_aggregate.py    # 聚合、快照时间、导入对账
│   ├── weekly_progress.py     # 进展、里程碑、审批、附件、健康自检
│   ├── weekly_group.py        # 集团组专表（明细 / 负责人 / 历史进展 / 统计）
│   └── weekly_goal.py         # 年度目标与里程碑（覆盖率 / 缺口 / 完成率 / 错配）
├── mock-mcp/                  # 仅 demo 用，不属于交付物
│   ├── _db.py                 # MySQL 连接（只读用户，密码不进日志）
│   ├── _store.py              # 只读查询层 + 口径规则 + 字段管控
│   └── server.py              # 31 个语义化取数工具（Streamable HTTP MCP）
└── tests/
    ├── smoke_test.py          # 415 条契约断言，不花模型 token
    └── baseline.py            # 396 题准确率基线（LLM 判定）
```

## 取数契约

31 个语义化工具，SQL 与口径规则固化在服务端，agent 侧不产 SQL：

| 工具 | 用途 |
|------|------|
| `weekly_schema` | 看板、分类树、**表字段清单**、口径说明 |
| `weekly_task_query` | 按看板/分类/状态/负责人/关键词/**专项组**查正式任务 |
| `weekly_task_detail` | 单任务详情（明细 + 近期进展 + 年度目标）；**id 存在但不属正式任务时报 `task_not_formal` 并写明卡在哪个 `workflow_status`、指出提交单/审批动作/附件三个工具按同一 id 仍可查，库里真没这行才报 `task_not_found`**；**集团看板任务的 `recent_progress` 恒为空（进展不在 `task_progress`），返回时写明当期进展就在同一次给出的 `group_detail.progress_effect` 里、历次报送用 `weekly_group_history`**；**同时返回两套负责人列时判胜负：集团看板 46 条任务的 `task.lead_owner_name` 与 `group_detail.lead_owner_names` 全都不一致（101 号任务「陈志远」vs「刘海涛,韩雪峰」），caliber 直接判给多值列** |
| `weekly_progress_history` | 进展版本回溯，`version_no` 倒序，**同名系列的兄弟任务随 `same_name_series` 回报** |
| `weekly_progress_coverage` | 进展历史覆盖度（行数/任务数/起止/最大版本、**平均每条有进展的任务报了几期 12.92**）、**已发布/未发布/合计一行给全（943/123/1066）**、未发布进展分档（**每档同时给行数与涉及任务数**）、**待审核清单带对外可见期号**、期号缺号、**每任务最新一期的下一步安排**、最新一期缺下一步计数、**从未报过进展两个口径并列（`total_count` 55 = `task_progress` 无已发布行，含报过的 46 条集团任务；`never_reported_either_table` 9 = 两张表都没报过，问「从来没上报过进展」答这个）**、**导入 vs 手工一行给全（`import_split`：943 全部来自导入、手工 0；判据只有 `import_id`）** |
| `weekly_aggregate` | 按 board/category/**primary_category**/status/project_group/owner/**workflow_status** 聚合，`top` 硬切前 N 组；**专项组同排给 `finished` 与 `finish_rate_pct`，`order_by="finish_rate"` 可按率定序（完成率最低 3 组：标准安全组 1/19=5.3%、数据基础设施组 2/15=13.3%、治理合规组 2/10=20.0%）**；**`top_sub_per_primary` 排的是「每个一级分类下任务数最多的二级分类」共 11 行——被排名的单位是分类，与 `weekly_rank per_group` 排任务不可互答** |
| `weekly_scale` | 多子表一次成表：规模（里程碑/附件/年度目标，**全部 `COUNT(DISTINCT)` 防 JOIN 放大**）/ 完备度（有该项的任务数）/ 进展密度（分母含零期任务） |
| `weekly_field_completeness` | 字段填报完整度（R-07/R-19），字段走白名单，**完整率 `filled_pct` 服务端算好** |
| `weekly_task_ranking` | 按子表计数排名（附件/进展/里程碑/提交单） |
| `weekly_rank` | 排名并列口径三选一：硬切 N 条 / 保留并列 / 每组各自第一，可限定看板并回显 `total_count`；**另有两档不是名次：`distribution` 给五数概括（q1=2 / 中位 6 / q3=15 / 均值 7.37 / 分母 128）、`quartiles` 给 NTILE(4) 等量四档（各 32 条，区间 0-0 / 0-5 / 6-14 / 14-18）** |
| `weekly_milestone_query` | 里程碑清单（已复核正式任务口径），单任务按 `sort_order` 编排，**同名系列的兄弟任务随 `same_name_series` 回报** |
| `weekly_workflow_query` | 审批动作流水（谁在哪个环节做了什么），可按 action/看板过滤、按任务聚合次数；**按环节+动作两维分档**、人均动作数（分子分母同回）、**按动作时间倒序并带任务名与填报人（回答「最近谁被驳回了」）** |
| `weekly_submission_query` | 审批提交单（`round_no` / `status` / 填报人），可查任务状态与最新单状态不一致；**按类型分档**（initial / progress）、O2OA 外部标识填充率、在途单（按成员枚举而非取反）、**在途总数/按看板分档/一任务多单**、**会签需求与按人分档/会签耗时对比**、人均轮次、已发布进展单 vs 已发布进展行；**`board=` 由服务端下推看板（提交单行里没有 `board_id`）：462 张单而清单封顶 200 行，手挑必残缺（宋佳明跨看板 32 张、集团 18 张）**；**`inflight_by_kind` 状态 × 类型九档（与看板 × 状态不可互答）**、**`rejected_by_board` 按看板的驳回率（技术组 9/293 = 3.07% > 集团组 4/169 = 2.37%，分子分母都在提交单上）** |
| `weekly_owner_roles` | 按角色分别计数（as_owner / as_lead / any_role） |
| `weekly_person_stats` | 人员统计（任务量/人均/独苗/跨组/双角色/标识写法/填报人/审核人/自审/**按专项组点名去重**） |
| `weekly_attachment_stats` | 附件统计（容量/类型/最大/上传人/挂载去向/**零附件任务清单**/在途提交单/逐月/软删/孤儿）；**传 `task` 即单任务档，一次给出该任务附件总量（任务 2：2 个文件 6914081 字节），按外键取数不加正式任务闸门；`zero_attachment` 等全表口径传 `task` 报 `task_not_applicable` 而非静默忽略** |
| `weekly_attachment_query` | 附件清单（不含 `storage_path`），可按任务或**看板**筛 |
| `weekly_import_audit` | 导入批次对账，`reconcile_rows` 反查实际落库行核对声明值，`orphans` 查批次引用完整性；**`latest_finished=True` 一次做完「选批 + 列受影响任务」（跑完 = `status = 1`，最近才按 `data_date`：第 19 批 / 17 个任务；按日期最新的第 20 批 `status = 0` 且实落 0 行）** |
| `weekly_freshness` | 各看板最新进展时间 |
| `weekly_health` | 连通性自检与各表行数 |
| `weekly_progress_range` | 时间窗内的进展（全表跨任务，可按月/季/任务分组计数），`peak` 直接给峰值组；**进展行按月上报，短于半月的窗口恒为 0 行，此时口径点明并指向 `weekly_freshness_distribution recent_days=N`** |
| `weekly_task_lifecycle` | 任务创建/发布的时间分布与建到发的时长（分档时**同排给出 `currently_finished`：2025 建 105/已完成 26，2026 建 23/完成 5**，两档相加即全库 31 条；表无完成时间列，故这是按建单档看当前状态） |
| `weekly_freshness_distribution` | 新鲜度分桶（30/90/180 天，**可加 `in_flight` 只看在办：从未报进展在办 8 条、不限状态 9 条**，随返回给 `task_total` 供各档自校）、自定义天窗、**时间漂移检出（73 条，双向不一致）**、滞后清单（含从未上报）、**按看板/专项组分组的滞后占比（带分母 `total` 与 `stale_pct`/`active_pct`：国家工程办 4/15=26.7% 高于条数更多的标准安全组 5/19=26.3%）**、近期上报清单；**清单档可加 `reported_only` 排除从未上报的（否则它们无天数可比却占满前几行，答「最久没上报的 5 个」会零命中；排除后首条任务 1 达 250 天，两档都随返回 `never_reported_count`）** |
| `weekly_approval_turnaround` | 审批时效（汇总/按看板/**最慢：带 `task_id`、并列按 id 定序、随返回 `top_tie_count`**/待审积压） |
| `weekly_group_detail_query` | 集团组明细（目标成果/实施举措/进度成效/完成时间文本/多值负责人，**附人数**），可按 `status` 与 `non_empty` 交叉筛矛盾数据，**`order_by="progress_time"` 按最新进展倒序供「当期/前 N 条」取用**；**按年份数字过滤自由文本 `completion_time`（`contains=2026` 得 31 条，检索词比裸年份长时自报全年真数并给出改法）** |
| `weekly_group_owner_query` | 集团组按牵头人或项目负责人查任务（多值精确匹配）；**查的是明细表多值列，与 `weekly_task_query owner=` 的 `task.lead_owner_name` 单值列是两个总体（李建华 3 条 vs 14 条），不可相加**；**不带人名时是按挂名人数倒序的榜而非花名册，且一次只带一个角色列，「各任务牵头人和项目负责人前 N 条」改用 `weekly_group_detail_query`** |
| `weekly_group_history` | 集团组历史进展（专表，可按年/月/季/**任务（带 `task_id`，并列按 id 定序）**/填报人/**滞报天数**/**提交单挂接率**分组，天窗可用 `last_days` 或**日历月** `last_months`） |
| `weekly_group_stats` | 集团组统计（负责人构成/分隔符写法/一栏几人/完成时间**写法分档**与去重取值/字数/附件/期数/成效一致性/**超期（归一化标准日期与 `YYYYQn`，1 条超期＋34 条写法判不了单列 `unparsable_count`）**/**状态与成效自相矛盾（未开始却填了成效，6 条）**、**附件条数分布（`attachment_distribution`：0→18/1→17/2→3/3→5/4→2/6→1，相加 46；清单档 `top` 默认 8，截断时自报只有一页并指向本档）**） |
| `weekly_year_goal_query` | 年度目标条目（按任务/年份，带里程碑摘要）；**`board=` 由服务端下推看板（目标行里没有 `board_id`）：集团组 109 行 / 46 任务，全量 313 行 / 128 任务，不要按任务逐个循环** |
| `weekly_year_goal_stats` | 年度目标统计（分年/覆盖率/缺口，可限在办/缺口分组/跨年跨度/连续设标） |
| `weekly_milestone_stats` | 里程碑统计（完成率/多维分解（**含 `primary_category` 任务一级分类轴：改革与治理 67.5% 居首，与里程碑自己的 `category` 轴答出的国家任务 58.9% 不是同一回事**）/软删审计/**里程碑被全删的 3 条任务**/每任务分布（**带 `top_tie_count`：最多那档 23 条并列**）/任务与里程碑错配） |

`weekly_workflow_query` 与 `weekly_submission_query` 是两张表，不可互相替代：
动作流水**聚合不出**提交单状态。混用会答出「5 个提交单全部通过」而真值是
「2 个全部驳回」。

集团组的进展**不在** `task_progress` 里，而在自己的 `task_group_progress_history`
（362 条已发布）。所以 `weekly_progress_history` / `weekly_progress_range` 查集团
任务一律返回空，必须走 `weekly_group_history`。同理，目标成果、实施举措、完成时间
文本和多值负责人只在 `task_group_detail`，`weekly_task_query` 没有这些列。

每个返回都自带 `caliber`（本次生效口径）与 `snapshot_note`（演示数据声明），
agent 据此给出依据、也据此判断不可答。

### 服务端固化的口径

| 规则 | 落点 |
|------|------|
| R-01 正式任务口径 | `_store.formal_task_clause()`，被所有任务类查询强制附加 |
| R-02 / R-08 空分组保留 | 聚合走 LEFT JOIN，口径条件写在 ON 上 |
| R-04 / R-14 敏感字段 | `opinion` / `review_comment` 凭 bearer token 分级，见下节 |
| R-07 / R-19 填报完整度 | `weekly_field_completeness`，空串按未填计入 missing，完整率随行返回不由模型口算 |
| R-09 / R-10 导入对账 | 批次数 vs 去重快照日期数 vs 去重导入时间数 |
| R-11 / R-13 多值负责人 | 去空格后匹配；分管领导按填法枚举计数 |
| R-12 完成时间是文本 | 任务详情的顶层 `caliber` 无条件声明 |
| R-17 里程碑复核 | JOIN 回 task 表复核正式任务口径 |
| 附件路径不外泄 | `storage_path` 在 `BLOCKED_FIELDS`，不进任何返回 |
| 相对时间窗锚定快照日 | `_store.AS_OF = 2026-08-15`，非 `CURDATE()`，见下节 |
| 集团历史双闸门 | `_store.group_history_gate()`，任务侧 R-01 + 行级 `is_published = 1` |
| 集团组多值负责人 | `FIND_IN_SET` 逐元素匹配，不用 `LIKE` 以免跨人误命中 |
| 没设目标算 0 不算没有 | 覆盖率/缺口走 `NOT EXISTS` 全表口径，`JOIN` 会把 11 个缺口任务整行丢掉 |
| 里程碑完成状态是两值码 | `status` 只有 1（未完成）/ 2（已完成），无「进行中」档，别按三态解读 |
| 里程碑软删审计看全表 | `deleted` 口径故意不加任务闸门：问的是表本身，按任务过滤会少算 |
| 提交单状态另有一套码值 | 已发布叫 `published` 不叫 `approved`；给值域外的词过滤会静默失效，工具随结果回 `status_domain` 并点名该条件未生效 |
| 附件大小是字节不换算 | `file_size` 原样报出，换成「约 3.8MB」即与精确值不一致 |
| 组内人数由服务端去重 | `group_by=project_group` 直接给 `lead_owner_count` / `project_owner_count`，让模型数人名会数错 |
| 姓名列填满≠ID 列填满 | `project_owner_name` 128 条全满而 `project_owner_id` 只有 119 条；只看姓名列会如实答「无缺失」，与真值 9 条相反 |
| 填报闸门与审核闸门不同 | 填报统计加 `p.is_published = 1`，审核统计**不加**——审过但未发布的进展同样算审过 |
| 多值负责人栏「单人」是一档 | 分隔符统计里 `单人无分隔符` 与逗号、顿号并列成档，不是缺失 |
| 附件挂载一条只进一档 | 优先级 进展 > 提交单 > 任务本体，各档相加等于总数；「在途」按提交单自己的 `status <> 'published'` 判 |
| 孤儿行必须走 NOT EXISTS | 附件 `task_id` 对不上任务表的有 3 条，用 JOIN 查会恒等于 0 |
| 软删审计不加任务闸门 | 问的是表本身（543 行中 33 条已删），按任务过滤会少算 |
| 多子表同查必须逐项去重 | `weekly_scale` 三张子表一次 JOIN，每个计数都是 `COUNT(DISTINCT ...)`：不去重时技术组 294 个里程碑会被附件行数乘成 1363；口径同时给出自检法——各组里程碑相加应等于全库总数 474 |
| 子表条数≠有该项的任务数 | `mode=totals` 答「多少个里程碑」（294），`mode=completeness` 答「多少任务有里程碑」（80），拿一个答另一个必错 |
| 「在途」按成员枚举不用取反 | `status <> 'published'` 会把 `cancelled` 那 1 张算进来（60 vs 59）：它既未发布也不在途 |
| 外部标识三列填充率不同 | `o2_process_id` / `o2_work_id` 各 460 而 `o2_task_id` 只有 60，拿一列代答另一列会把缺失率答反 |
| 期数按 `version_no` 去重 | 一期可能有多行，`COUNT(*)` 会把「几期」答成「几行」 |
| 声明值必须反查才算对账 | `changed_tasks` 是批次自己声明的数字，`reconcile_rows=True` 才反查实际落库；`LEFT JOIN` 不可换 `JOIN`——声明 43 实落 0 那批正是最极端的对不上 |
| 「最长的标识」问标识不问任务 | 同一个标识挂 3 个任务只算一个标识，不去重会返回 128 行、并列几个也数不出来 |
| 逐任务清单看 `total_count` | 问「每个任务各多少」时 `top` 只是页大小，`total_count` 与 `row_count` 相等才说明列全了 |
| 审批流转状态是唯一不加发布闸门的分组 | `group_by=workflow_status` 若照例先筛 `published`，七档只剩一档 128，未发布的 22 条全部消失；它与 `group_by=status`（未开始/进行中/已完成/已停用）是两套词汇、两个总体（150 vs 128） |
| 「未发布」用取反不用相加 | `workflow_status <> 'published'` 得 22；把在途各档相加会漏掉 `cancelled` 那 1 条——它既未发布也不在途 |
| 「最近三个月」是日历月不是 90 天 | `last_months=3` 从 2026-08-15 回到 05-15，`last_days=90` 落在 05-17，中间三行让 5 月由 16 变 13；两个参数互斥，同时给会得出第三个窗口，服务端直接报错 |
| 「最新一期」按 `version_no` 定序 | `weekly_progress_coverage` 的 `latest_round` 用 `ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY version_no DESC, id DESC)`；按 `progress_date` 取最新会错——补报的老期号可能日期更晚，而不收敛则 16 期任务出 16 行、最老那期的下一步被当成现在的安排 |
| 完成时间「写法分档」≠去重取值数 | 分档得 6 类（46 条各进一档，相加等于 `total_count`），去重取值是 28 个，两者差一个量级；判别顺序即优先级，`2026年6月底` 固定进含「底」档 |
| 滞报天数取最后一次上报 | `grouping=lag` 用 `MAX(report_time)` 与快照日之差，用 `MIN` 会把老任务全排到榜首；同时回 `total_tasks`，因为从未上报的任务不在这张表里，拿行数当集团组任务数会少算 |
| 孤儿引用与「未走导入」是两件事 | `orphans=True` 按 `NOT EXISTS` 判 `import_id` 有值却查不到批次，结果 0 即引用完整；`import_id IS NULL` 的 120 条是手工填报，单列为 `rows_without_import`，混进孤儿数会把它们全报成异常 |
| 0 是结论不是空结果 | 孤儿数 0、最新一期缺下一步 0，口径里直接写明「这是结论本身，不要换口径重算」，否则模型会反复改条件去凑非零 |
| 提交单不加任务发布闸门 | `by_kind` 得 312 progress + 150 initial = 462；加上发布闸门会缩成 310/128，把未发布任务的提交单一起吞掉。在途任务的提交单同样是提交单 |
| 「一个都没有」用 `NOT EXISTS` 一次列全 | `zero_attachment` 直接给 22 条零附件任务，并另给分母 128；缺这一档时模型只能对 128 个任务逐个调 `weekly_attachment_query` 看谁返回空 |
| 看板在 `task` 上不在附件行上 | 按看板筛附件必须 JOIN 回 task（`weekly_attachment_query` 的 `board` 参数），并顺带带出 `task_name`；否则「集团组有哪些附件」只能按 46 个任务逐个调，还算不出看板总数 52 |
| 计数题一律服务端聚合，不许翻清单手数 | 清单封顶 200 行，手数只看得到第一页——基线里模型自己写过「无法精确求出全库总数」。在途 61、动作 1578、集团历史 404 都远超 200，所以各自都有一次成型的聚合档 |
| 「需会签」与「正在会签」是两个问题 | `sign_summary` 的 `need_sign = 1` 有 155 张（另 307 张不需，合计 462），而在途 `status = 'signing'` 只有 9 张；拿后者答前者会少一个量级 |
| `rejected` 也是在途的一档 | `inflight_by_board` 九档相加等于 61；漏掉 `rejected` 则集团组少 4、技术组少 9 |
| 耗时均值只算已完结的单 | `sign_turnaround` 要求 `completed_at` 与 `submitted_at` 均非空，两档 274 + 128 = 402 小于总数 462；未完结的单没有耗时，硬塞进分母会把均值拉低 |
| 会签人空值是「没有会签人」 | `by_signer` 排除 `signer_name IS NULL`：空值不代表某人签了 0 单，混进来会多出一个不存在的「人」 |
| 人均类指标分子分母同回 | `rounds_per_task` 3.08 = 462 / 150、`actions_per_task` 10.52 = 1578 / 150，分母都是「有记录的任务数」而非已发布的 128；只回均值时模型会自己拿别处的任务数去除 |
| 同一动作在不同环节各自计数 | `by_node_action` 按 `node_type` + `action` 两维分 6 档；只按 `action` 分会把 955 条 `approved` 揉成一档，答不了「哪个环节驳回得多」（`audit/rejected` 13） |
| 已发布进展「单」与「行」不同表 | 提交单侧 272（只数 `submission_kind = 'progress'`，含 initial 会变 400），`task_progress` 侧 943；再并入集团组专表会得到 1305，三个数答的是三个问题 |
| 挂接率的分母不加行级发布闸门 | `by=linkage` 走全部 404 行而非过闸的 362 行；`linked_rows = 0` 是结论——集团成效历史与审批提交单没有外键落库，不是查不到 |
| 完整率随行返回不由模型口算 | `filled_pct` 由服务端按 `total` 算到一位小数（`project_owner_id` 128 / 119 / 93.0）；只回计数时基线里模型答成「完整率 100%」，与它自己引用的 119/128 自相矛盾 |
| 集团看板负责人两列值不一致 | 46 条任务上 `task_group_detail.project_owner_names` 与 `task.project_owner_name` 对不上（97 号任务一边「胡建国,方永康,邓少华」、一边「秦怀瑾」）；看板问题读明细表那一列，读错列时答案自洽也仍是错的 |
| 多值负责人人数由服务端算 | `lead_owner_count` / `project_owner_count` 随行返回，顿号与逗号都扣过；模型按逗号自己数会漏掉顿号那几行 |
| 「某组的人都有谁」是去重题 | `scope=group_roster` 行数即人数（标准安全组 9 位牵头人）；拿该组 19 条任务清单自己数，同一个人会按任务重复计数 |
| 专项组是独立一列不是分类 | `weekly_task_query` 的 `project_group` 精确匹配；塞进 `category` 或 `keyword` 会静默返回错的集合 |
| 「最近」是排序题不是筛选题 | `scope=recent` 按动作自身时间戳倒序（不是任务 id、不是轮次号），取头几条即可；把 13 条驳回全铺开答的是「有哪些」而非「最近有哪些」 |
| 存在性按明细表判，不拿汇总列判空 | 「从来没报过进展」用 `scope=never_reported` 按 `task_progress` 有无已发布行判定得 55 条；按 `t.latest_progress_time` 判空只得 9 条，漏掉集团看板那 46 条（成效在集团历史表，汇总列有值而 `task_progress` 无行）。55 + 有进展的 73 = 正式任务 128 |
| 一句话两个数就一次取全 | 分开取的风险不是加错，是两次闸门不同一：已发布/未发布按正式任务口径为 943/123/1066，漏掉任务闸门变 945/1068（基线即答成 125/1068）。`scope=publish_split` 一行给全；「多少条 / 涉及多少任务」也是两个数，驳回 39 行落在 33 条任务上，各档任务数不可相加（去重后 72 条） |
| 问「在办」就加在办闸门 | `in_flight=True` 后「从未报进展」是 8 条，不限状态的同一档是 9 条，多出的是已完成的任务 88；返回的 `task_total`（在办 92 / 全量 128）即本次闸门下的总数，各档相加应等于它 |
| 均值的分母是「有记录的那些」 | `avg_rounds_per_task` 12.92 = 943 期 / 报过进展的 73 条，不是正式任务 128（那样得 7.37）；分子分母与均值同行返回，模型不必也不该自己挑分母 |
| 名次并列按 `task_id` 不按任务名 | `by=task` 的 11 期档有 8 条并列：按名排前 5 是 127/105/133/120/104，按 id 排是 104/105/115/120/127——两批不同的任务而非换序。分组行里带 `task_id`，与其它榜单同一套定序键 |
| 「最……的那一条」榜首常有并列 | `scope=slowest` 的 59 天有两轮并列（任务 76 与 143），随返回 `top_tie_count=2`；问「最慢的一轮是哪条任务」取首行一条，把并列都列出来而不说明是并列，读起来就成了两个独立答案 |
| 「全部删掉」按 `NOT EXISTS` 判，不按「删过」 | `scope=fully_deleted` 得 3 条（删干净的），「有删过里程碑」是 23 条，差一个量级；`scope=deleted` 只给全表 566/36/602，各清单口径又都带 `m.is_deleted = 0` 把被删行滤掉，所以缺这一档时这问题无路可走——基线答「无法确认」是照实说 |
| 漂移清单是双向的，不是漏报清单 | `drift=True` 的 73 条里，汇总列偏早（进展比它新）和偏晚（它比进展新）都算不一致；行数即任务数，按 73 报，别截前几条当全部 |
| 同名系列是各自独立的任务 | 「数据资源登记体系建设」与其（2期）（3期）（4期）是四条任务各有期次；按裸名查落到其中一条是对的，`same_name_series` 把兄弟任务显式列出（41/60/79），避免把整个系列的期次并成一段历史 |
| 占比题必须带分母，不能只给计数 | 「哪个组滞后占比最高」拿计数答会排错：标准安全组滞后 5 条最多，但分母 19 条只有 26.3%，低于国家工程办的 4/15=26.7%。`weekly_freshness_distribution` 的 `by=board`/`by=project_group` 把 `total`、`stale_pct`、`active_count`、`active_pct` 与计数同排返回并按占比降序，模型不必也不该拿别处的任务数手工相除；分组视图默认不加在办闸门（分母 128），要只看在办加 `in_flight=true`（分母 92、滞后 46） |
| 按建单档看当前状态 ≠ 那一年完成的 | 任务表没有完成时间列，`weekly_task_lifecycle by=year` 给的是「那年建的任务里当前已完成多少」：2025 建 105/完成 26，2026 建 23/完成 5，两档相加正是全库已完成 31 条。缺这一档时模型要么答成建单数，要么把全库的 31 条硬套到某一年；口径里写明跨档完成的任务仍记在建单档 |
| 一级分类与里程碑自己的类别是两个轴 | 任务分类挂在 `t.category_id` 且只到二级，一级要再往上跳一层 `parent_id`；`by=primary_category` 得改革与治理 40/27=67.5% 居首，`by=category` 得的是里程碑类别文本的国家任务 58.9%。名字像但答的不是同一个问题，问「哪个一级分类完成率最高」只有前者算得对 |
| 展示文本归一化后「判不了」≠「没超期」 | `completion_time` 是展示文本（R-12），46 条里只有 12 条能归一化（6 个标准日期原样用 + 6 个 `YYYYQn` 取季末日），`scope=overdue` 由服务端算出 1 条超期（任务 123，2026Q2→2026-06-30，逾期 46 天），余下 34 条随 `unparsable_count` 单列。只按标准日期写法看一条都查不到，季度那几条归一化后才露出来；把两者混在一起会把「无法判断」说成「都没超期」 |
| 行内自相矛盾与两表文本不一致是两回事 | `scope=status_effect_conflict` 判同一行内部：`status = 0`（未开始）却填了非空 `progress_effect`，共 6 条（97/108/130/137/140/142）；`scope=effect_consistency` 比的是明细表与历史表两处文本是否一致，两处写着同一句话也算一致，答不了「状态与成效矛盾」 |
| 排序键要选问句真正问的那个量 | 「完成率最低的 3 个组」按率定序（`weekly_aggregate order_by=finish_rate ascending=true`）得标准安全组 5.3% / 数据基础设施组 13.3% / 治理合规组 20.0%；拿任务数排出来的是另一批人：完成数同为 2 条时，治理合规组 2/10=20.0% 反而高于数据基础设施组 2/15=13.3%。完成数最少 ≠ 完成率最低 |
| 「最久没上报」与「从来没报过」是两问 | 从未上报的任务没有天数可比，却在默认排序里排最前，把「最久没上报的前 5 个」整个占满，与真答案零交集。`reported_only=true` 把它们排除后首条是任务 1（250 天），两档都随返回 `never_reported_count`（清单档在办闸门下 8 条），报结论时要说明排除了几条 |
| 中位数与分档不是名次题 | 在名次档（`mode=cut`）的可见 5 行里取中间值会答成 14，真值是 6：问分位用 `mode=distribution`（q1=2 / 中位 6 / q3=15 / 均值 7.37 / 分母 128），问「分成四档」用 `mode=quartiles`。NTILE(4) 是等量分档，四档各 32 条（按期数区间等宽切会得 17/39/41/31，那是另一种分法）；等量分档下边界期数跨档重复出现是正常的。两档都保留 0 期任务，32 条 0 期恰占满第一档 |
| 「不属正式任务」不等于「查不到」 | 任务 2 存在、有完整审批流水，只是 `workflow_status='rejected'` 没过 R-01。旧报错只说「未匹配到正式任务」，读起来和 id 打错一模一样，模型于是改用按名字搜——落到同名系列的（3期）（4期）两条**别的**正式任务上，同时提交单与动作两个工具又能正常返回任务 2 的数据，三方信号互相矛盾，M2-01 的 6 轮 13 次调用全耗在仲裁上。现在报 `task_not_formal` 并写明卡在哪个状态、指出三个外键工具仍可查、明确警告不要改用名字搜；「库里没这行」另报 `task_not_found`。诊断不等于放宽：正式任务闸门原地不动 |
| 过滤词不在值域内等于没过滤 | 提交单状态只有 7 个值，没有 `approved`（那是审批动作的词）。gold 的 `status <> 'approved'` 因此等于没写，把 25 条**已发布**的也列成了「还没发布」；真答案是 4 条（`exclude_status=published`）。传域外值时 caliber 会写明「该过滤条件未筛掉任何行」，此时结果是全量，不能说成「已排除」 |
| 被排名的单位是什么，看问句在数什么 | 「每个一级分类下任务数最多的二级分类」数的是分类，用 `weekly_aggregate group_by=top_sub_per_primary`（11 行）；「每个一级分类下进展最多的任务」数的是任务，用 `weekly_rank mode=per_group group_by=primary_category`。两者都是「每组第一」，但胜出者一个是分类、一个是任务，互相代答就答错了对象 |
| 过滤只落在计数上、没落在清单上 | `group_by=category` 原先把看板条件放进 LEFT JOIN 的 ON 子句，于是**行清单永远是跨看板的 47 个分类**，另一看板的 19 个只是变成 `cnt=0`——和「本看板确实没有任务的分类」长得一模一样，问「技术组有哪些分类」就答成 47。现在看板同时过滤分类树（`c.board_id`）：技术组 28 个（7 个一级 + 21 个二级）、集团组 19 个（5 + 14），并随行返回 `parent_id` 让两级可分别报 |
| 单任务的量要有单任务的档 | 「任务 2 的附件一共多大」原先只能翻 `weekly_attachment_query` 的清单手工加总，O3-03 的 6 轮全耗在这里。`weekly_attachment_stats` 加 `task` 后一次即得（2 个文件 6914081 字节）。该档**故意不加**正式任务闸门：附件挂在 `task_id` 外键上，任务 2 是 `rejected` 却确有附件，加闸门会把 2 静默答成 0。反过来 `zero_attachment`/`deleted`/`orphan` 这类跨任务与全表口径传 `task` 直接报错，不做静默忽略——静默忽略会让人以为拿到的是单任务数 |
| 空结果也要说清是哪一种空 | 「最近一周哪些任务更新了进展」问的是 `task.latest_progress_time`（23 条），不是 `task_progress` 的行。进展行按月上报（最新一批 `progress_date` 是 2026-07-31，距快照日 15 天），所以 `weekly_progress_range last_days=7` 必然 0 行。那个 0 是真的，但读起来像「本周没人报」，模型于是退到「最新一批进展」的 17 条——那是 07-31 那一期的期数，第三个数字。现在空窗口的 caliber 点明月度节奏、给出 23 条的正解路径（`weekly_freshness_distribution recent_days=7`），并明确否掉退成 17 条这条岔路 |
| 同一个人名有两个总体，不能相加 | 「李建华负责哪些任务」在 `task.lead_owner_name` 单值列上是 14 条（`weekly_task_query owner=`），在集团看板明细表的逗号多值牵头人列上是 3 条（`weekly_group_owner_query person=`）。基线把两边并起来，于是多出两条只在明细表挂名的、又漏掉一条 task 上牵头而明细表没列名的（O6-01）。两工具的 caliber 现在互相点名，默认按 task 表口径答 |
| 空清单不代表没数据，得说清数据在哪张表 | 集团看板任务在 `task_progress` 里 0 行（那张表全属技术看板），它的当期进展在 `task_group_detail.progress_effect`、历次报送在 `task_group_progress_history`。`weekly_task_detail` 原先只是回一个空 `recent_progress`，读起来像「这任务没报过进展」，于是 Q1-02 在 progress_history / progress_range / milestone_stats 之间转了 6 轮 13 次调用——而答案就在同一次返回的 `group_detail` 里。现在空 `recent_progress` 自报进展在哪张表、指向 `weekly_group_history`，技术看板任务不受影响 |
| 分布要用分布档，别拿清单的一页去数 | 「集团看板每个任务各有几个附件」的真值是 0→18 / 1→17 / 2→3 / 3→5 / 4→2 / 6→1（相加 46）。`scope=attachments` 是清单且 `top` 默认 8，Q2-03 照那 8 行手数，得出 21/4/4。新增 `scope=attachment_distribution` 由服务端算完再回；截断的清单现在自报「只有一页、共 46 条」并指向分布档，给满 46 行时不加这句 |
| 「前 N 条」得先问清按什么排 | `weekly_group_owner_query` 不带人名时是按挂名人数倒序的榜，且一次只带一个角色列，所以「集团看板各任务的牵头人和项目负责人、给我前 8 条」在它上面落到 101/105/107… 而 gold 是按 `task_id` 顺序的 97..104 两列齐出（Q3-01 整张列表一条都没对上）。正解是 `weekly_group_detail_query fields=lead_owner_names,project_owner_names`，现在榜档的 caliber 直接指过去 |
| 自由文本按年份过滤只能扫年份数字 | `completion_time` 是展示文本，集团看板 46 条里有 28 种写法（`2026年内` / `2026年底前` / `2026Q4` / `2026-12-30` / `2026年9月30日` …）。「哪些任务要求 2026 年内完成」按 `contains=2026` 扫得 31 条；Q4-03 拿「2026年内」当检索词，只命中字面相同的 5 条，漏掉 26 条同年到期的。现在裸年份会说明这是唯一可靠的过滤方式，检索词比裸年份长时则**自报全年真数并给出改法** |
| 取不到数与真值是 0 要分开 | 「有多少条进展是手工填的」判据只有 `task_progress.import_id` 一列，此前没有任何工具暴露它，Q5-04 只能答「无法精确统计」，而真答案是 0（943 条已发布进展全部来自导入）。新增 `weekly_progress_coverage scope=import_split`，并把另一套闸门的数一并写进 caliber：去掉发布闸门是 1066 行里 948 导入 + 118 手工，那 118 条全部未发布 |
| 子表里没有看板列，看板条件必须由服务端下推 | 看板挂在 `task` 上，`task_workflow_submission` / `task_year_goal` 行里都没有 `board_id`。没有参数时模型只能拉全量再手挑：462 张提交单而清单封顶 200 行，R3-05 于是把「宋佳明未发布的集团任务」答成 1 条（真值 18 条，他跨看板共 32 张）；R4-01 更是循环调了 13 次 `weekly_year_goal_query` 还是混进技术看板的任务，且循环永远算不出该看板自己的总数。两个工具都加 `board=`：集团年度目标 109 行 / 46 任务，全量 313 行 / 128 任务 |
| 「最近一批跑完的」两个词都是条件 | 「跑完」是 `status = 1`，「最近」才是按 `data_date`。默认清单按日期倒序，头一条是第 20 批——它 `status = 0` 且实落 0 行，拿它答「影响了哪些任务」根本无从答起，R7-03 六轮耗尽也没给出答案。`weekly_import_audit latest_finished=True` 一次做完选批与列任务（第 19 批 / 2026-07-31 / 17 个任务）。选批与列任务不能分两次调用：挑批次正是会错的那一步 |
| 口径规则不能写成否定句，否则会过火 | 「从来没报过进展」有两个都成立的口径：两张表都没报过是 **9** 条，`task_progress` 里没有已发布行是 **55** 条（后者把报过的 46 条集团任务也算进来，它们的成效写在 `task_group_progress_history`）。Q 类那批把 caliber 写成「不要用 `latest_progress_time` 判——那样只得 9 条」，把 55 定成了唯一正解，于是模型在**任何**场合都答 55，还顺手把集团历史行并进各种进展计数（今年上半年答 517/272，真值 349/237）——一句否定句牵连 8 题回归。现在改成并列口径：两个数各自回答哪个问题写清楚，一个也不否掉 |
| 一个工具返回两套竞争列，就得判个胜负 | `weekly_task_detail` 同时回 `task` 行上单值的 `lead_owner_name` / `project_owner_name` 和 `group_detail` 里多值的 `lead_owner_names` / `project_owner_names`，集团看板 46 条任务两边的值**全都不一致**（101 号任务前者「陈志远」、后者「刘海涛,韩雪峰」）。谁排在上面谁就被当成答案，R8-02 照 task 行答了「陈志远」。现在有 `group_detail` 行时 caliber 直接点名两边的值并判给多值列，没有该行的技术看板任务不加这句 |

### 相对时间窗以快照日为基准

「最近 30 天」「今年以来」这类问法，基准是数据快照日 `2026-08-15`，
不是机器墙钟。数据止于 `progress_date` 2026-08-01，而墙钟已经走过去了：
按当前时间算窗口会静默滑出数据区间，答出一个比真值小的数。

锚点固定在服务端，模型因此既不需要知道今天几号、也无法用自己的日期替换它。
每个返回的 `caliber` 会写明本次生效的窗口与基准日。

### 敏感字段权限分级

R-04/R-14 要的是「按权限返回」。一律遮蔽同样不满足需求——那等于这条 P0 能力
没实现，而且不可测。判定依据是传输层的 `Authorization` 头，**不是模型说的话**：

| 凭证 | `opinion` / `review_comment` |
|------|------|
| `GUOSHU_WEEKLY_MOCK_TOKEN`（默认 `demo-token`） | 遮蔽为「[按权限不展示]」 |
| `GUOSHU_WEEKLY_MOCK_ADMIN_TOKEN`（默认 `demo-admin-token`） | 返回原文 |

`caliber` 字段会如实说明本次凭证拿到的是哪一档。agent 侧的 token 来自启动方的
环境变量，**模型无法自选凭证提权**——这是刻意的，用户或提示词都不能放宽它。
生产环境把这个 header 换成 OA 身份 + 行级策略即可，判定位置不变。

## Demo 与生产的差距

以下都是**有意未做**，不是遗漏。按方案文档的分期推进：

| 项 | demo 现状 | 生产需要 |
|----|-----------|----------|
| 数据源 | 本机 MySQL 8.4 + weekly_mock | 入口组 MCP + oa_biz 真实库 |
| 鉴权 | 单个进程级 token | per-user token map + BFF 身份映射 |
| 数据权限 | 敏感字段按 token 两档分级 | 按 OA 真实身份做行级权限 |
| 前端 | 无（经 psi-agent 既有接口） | 专建对话应用 + BFF（方案第六章） |
| 材料生成 | 无 | 报告下载与图表（P1，第 5 期） |
| 评测 | 415 条契约断言 + 396 题基线 | 再加 200 题真实库集 + 多轮追问集 |

### mock 数据层的两处不可外推

- **性能**：引擎虽与生产同为 MySQL 8.4，但数据量（1.1 MB）、网络与并发都不同，
  `≤10s / ≤30s` 的验收仍须在真实库重测。
- **脏值口径**：R-11（分管领导多种填法）在干净的 mock 数据上测不出真实价值，
  要等真实库适配。

`gold_sql` 在本机 MySQL 上的可跑率是 **396/396（100%）**，含两道查
`information_schema` 的权限边界题——它们能跑，但 Agent 侧仍应判为不可答。

## 切到真实服务

```bash
export GUOSHU_WEEKLY_MCP_URL=https://weekly.example.internal/mcp
export GUOSHU_WEEKLY_MCP_TOKEN=<入口组签发>
```

`mock-mcp/` 整个目录不参与交付。agent 侧**不含任何 mock 专属分支逻辑**——
这是刻意的，否则切真实库时会带出隐藏路径。

## 配置

| 变量 | 必需 | 说明 |
|------|------|------|
| `GUOSHU_WEEKLY_MCP_URL` | 是 | 路径必须是 `/mcp`；非 loopback 强制 HTTPS |
| `GUOSHU_WEEKLY_MCP_TOKEN` | 是 | bearer token，由启动方提供 |
| `GUOSHU_WEEKLY_MCP_TIMEOUT_SECONDS` | 否 | 默认 30，限 0.1~120 |
| `GUOSHU_WEEKLY_MCP_MAX_RETRIES` | 否 | 默认 2，限 0~5，仅读操作重试 |
| `GUOSHU_WEEKLY_MYSQL_HOST` | 否 | mock 库地址，默认 `127.0.0.1` |
| `GUOSHU_WEEKLY_MYSQL_PORT` | 否 | 默认 `3306` |
| `GUOSHU_WEEKLY_MYSQL_USER` | 否 | 默认 `weekly_ro`（只读） |
| `GUOSHU_WEEKLY_MYSQL_PASSWORD` | 否 | 只读用户口令 |
| `GUOSHU_WEEKLY_MYSQL_DB` | 否 | 默认 `weekly_mock` |

Agent 不读、不写、不打印 token，不改 `.env`，不向用户索要凭证。
连不上就如实报错——**没有本地兜底**，也不得启动本地周报服务。
