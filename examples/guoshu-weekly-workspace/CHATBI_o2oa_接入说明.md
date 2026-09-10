# ChatBI 正式数据接入说明(o2oa / O2OA PostgreSQL)

> **进度快照(2026-09-10,第 34 轮)**
>
> - **工具接线:31 / 31 —— 全部完成**。31 个 MCP 工具都已走正式源(逐名核对过:无遗漏、
>   无多余);仍未迁移的只剩**参数组合**(如 `weekly_freshness_distribution task=`、
>   `byteam` 名字解析),它们照旧回落演示路径,行为不变。
> - **三套真库验收(同构 PG 实例 + 演示库数据)**:
>   - 端到端 `verify_end_to_end.py`:**408 / 408** 断言通过;
>   - 口径验收 `verify_numbers_v2.py`:**38 / 38** 通过(mock docstring 里写死的契约数字逐条复现);
>   - 列集合对照 `column_parity.py`:**123 / 123** 一致(原先剩的 3 处已查清:全是**对照器侧**的
>     解析与桩问题,不是正式源的列对不上 —— 详见 3.0.6 与 3.1.5)。
> - **交付形式**:服务 / Docker(`Dockerfile`,streamable-http,默认 18900);不含前端,由主 Agent 经 MCP 调用。
> - **真库已打通(2026-09-10,本条已取代原先"直连尚未打通")**:物理库名是 **`o2oa`**
>   (字段说明里的 `O2OA-DB` 是业务叫法,集群 `pg_database` 里没有该 database;`oa_biz` 是
>   历史空表别用)。卡点根因不是"忘了授权",而是 **`o2oa` 的 `datacl` 被显式改过**
>   (`{=T/admin,admin=CTc/admin}`):**PUBLIC 没有 CONNECT**,必须由库 owner 显式
>   `GRANT CONNECT ON DATABASE o2oa TO <只读账号>` 才连得上。授权生效后实测:
>   只读账号 `task_board_readonly` 已可连,**12 张 `task_*` 表全部可见**。
> - **真库首次核对:31 / 31 个出口正常返回**,列集合与参考一致(脚本 `verify_real_o2oa.py`;
>   连法:本机 → H100 → opl 建 `chain.py forward` 端口转发,用本机 Python 3.14 跑)。
> - **真库基线交叉核对:21 / 21 通过**(`verify_real_baseline.py`)—— 每个关键数字都拿一条
>   **直接 SQL** 去对。这类断言的价值是**不依赖具体数字**:活库每天在变,「工具口径 == 直连口径」
>   这条关系不变量始终成立。据此补齐的真值:`never_reported` **35**;`task_query` tech **48** /
>   group 40(合计 = 已发布 88);`year_goal(2026)` **54**;审批动作(软删闸门)**91**;
>   提交单(软删闸门)**28**;近 120 天正式进展 **9** 行;近 30 天有更新 **7** 条;
>   `task_ranking(progress)` 榜首仅 **1 期** —— 真库里多数任务只有一期正式进展,
>   所以"谁进展最多"类问题在真库上几乎全是并列(`rank` 的 `tied_at_top` 达 53)。
> - ⚠️ **真库冒烟(31 个工具各调一次):活着 27 / 31**。四个出口在**默认/空参数**下仍会回落
>   演示路径 —— `weekly_aggregate()`、`weekly_owner_roles()`、`weekly_submission_query()`、
>   `weekly_workflow_query()`:它们的**具名 scope 已迁移**(31/31 与 21/21 的核对都覆盖了那些
>   scope),但**默认清单分支没迁**。生产环境没有演示 MySQL,**回落就等于 `store_unreachable`
>   报错**,所以这四处必须补迁移,或让默认分支明确报"该参数组合未迁移"而不是去连一个不存在的库。
> - 另:`weekly_task_detail` / `weekly_schema` / `weekly_field_completeness` 返回的是**结构化多块
>   信封**(`task` / `year_goals` / `group_detail`、`table_columns`、`supported_fields`),本就没有
>   `rows` —— 这不是缺陷,但 agent 侧读法与 rows 类工具不同,应在工具说明里点明。
> - **真值快照(2026-09-10;活库会变,数字带日期,形状与口径才是不变量)**:`task` 105 行 /
>   已发布 **88**;`task_progress` 197 行 / `is_published=1` 仅 **56**;`task_milestone` 20(状态
>   全为 0 未完成);`task_attachment` 30;`task_workflow_submission` 29;
>   `task_group_progress_history` 6;`task_workflow_action` 92;`task_group_detail` 55;
>   `task_year_goal` 57;`task_category` 51。`drift` **0 行**;`rank(progress_rounds)` 的
>   `tied_at_top` = **53**;人员首位 陈海波 22;`workload_summary` 42 任务 / 7 人 / 6.00;
>   附件 30 个 / 11.6 MB / 6 任务 / 5 上传人;`by_ext` 15 档;多值负责人「、」在真库真实存在。
> - ⚠️ **演示库数字与真库差一个量级且形态不同**:既有断言断言的是演示库数字
>   (943/128/454/462…),**需按真值重建或标注为演示口径**;"真库当前无数据"的分支
>   (漂移 0、进展状态只有"已通过"一档、里程碑全部未完成)要求 agent **如实回答"没有/未完成"**;
>   真库里还存在明显测试数据(`test001`、`测试-集团重点-0821` 等),统计口径需注意。
> - 已知口径陷阱、性能与索引要求、列集合纪律等,**都在下面各节留痕**,改代码前请先读第 3 节与第 6 节。

> 依据:国数方《ChatBI数据权限开通以及表字段说明》(2026-09-08,oa-weekly 核对版)。
> 本说明是 agent 包侧的接入与迁移指南;字段字典、关系图与示例 SQL 以国数方
> 该说明为准,这里只描述 **代码如何接入** 与 **迁移批次**。

## 1. 接入原则

- agent(31 工具契约)与 mock 语义不变;正式源 = 同一契约的 **PostgreSQL 实现**;
- 生产数据源:**O2OA 的 PostgreSQL**(物理库名 **`o2oa`**,schema `public`)。⚠️ 字段说明
  里写的 `O2OA-DB` 是**业务叫法**:2026-09-10 用只读账号直连复核,集群里 `pg_database`
  只有 `he3mysql / o2oa / oa_agent / oa_biz / postgres`,**没有 `O2OA-DB`**;
  且 `o2oa` 的 `datacl` 被显式改过(`{=T/admin,admin=CTc/admin}`),**PUBLIC 没有 CONNECT** ——
  必须由库 owner 显式 `GRANT CONNECT ON DATABASE o2oa TO <只读账号>`,否则一律
  `permission denied for database "o2oa"`;**不要**给 MySQL `oa_biz` 的同名 task_* 旧空表授权;
- 只读:连接角色仅授 SELECT;本包连接层强制 read-only 会话(双保险);
- 发布准入等硬约束由服务端固化,见 `mock-mcp/_admission.py`,不得写散在单条查询里。

## 2. 配置
| 环境变量 | 用途 | 默认 |
|---|---|---|
| `TASK_BOARD_DATA_SOURCE` | 数据源选择:`mock`(演示)/`o2oa`(正式) | mock(未设置时) |
| `PGHOST` / `PGPORT` | PG 地址 | 127.0.0.1 / 5432 |
| `PGDATABASE` | 正式库名(**`o2oa`**,不是 `O2OA-DB`) | `o2oa` |
| `PGUSER` / `PGPASSWORD` | 只读账号(实测账号为 `task_board_readonly`) | chatbi_read / 空 |
| `PGSCHEMA` | schema | public |

凭据只经环境/Secret 注入,不落代码与镜像。

### 2.1 交付形式:服务 / Docker(2026-09-10)

取数能力以**独立 MCP 服务**交付,不附带任何前端 —— 由主 Agent 通过 MCP 调用。
仓库里的 `Dockerfile` 就是这层封装(streamable-http,默认 18900):

```bash
docker build -t guoshu-weekly-mcp:latest examples/guoshu-weekly-workspace
docker run --rm -p 18900:18900 --env-file o2oa.env guoshu-weekly-mcp:latest
```

`o2oa.env` 至少要有:`TASK_BOARD_DATA_SOURCE=o2oa`、`PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD/PGSCHEMA`;
联调期再加 `GUOSHU_AS_OF=2026-08-15`(固定基准日,与演示快照日对齐)与
`TASK_BOARD_GRANTED_OPTIONAL_TABLES=task_attachment,task_group_progress_history,task_workflow_action`。

三条交付注意:

1. **`mcp` 必须钉在 1.x**(镜像里已写死):2.x 把 `FastMCP` 改名成 `MCPServer`,直接导入失败;
2. **启动不再探演示库**:正式源模式下没有 MySQL 是正常的,此前探测失败会让容器以退出码 2 起不来 ——
   现已按当前实际使用的源来探,并在日志里打印"用的是哪个源";
3. **端点鉴权由部署侧负责**:容器绑 `0.0.0.0` 只为容器外可访问,生产须置于内网/反代之后;
   库侧只读由连接参数保证(`default_transaction_read_only=on` + `statement_timeout`)。

## 3. 已落地的第一批代码

| 文件 | 内容 |
|---|---|
| `mock-mcp/_pg.py` | PG 只读连接(psycopg 3 延迟导入;read-only 会话 + 固定 schema) |
| `mock-mcp/_admission.py` | 硬约束与值域:发布准入、进展正式版、历史版本(status=3)、submission published 轮、枚举校验、year 显式、时间归一化与比较片段、可选表降级文案 |
| `mock-mcp/_o2oa_templates.py` | PG 查询模板(16 个):①已发布任务清单(5.1)②最新正式进展(5.2,`DISTINCT ON` 单次扫描,定序键 `version_no DESC, id DESC`)③任务详情+年度目标+里程碑+集团扩展(5.3)④历史版本进展(rule 2 例外)⑤分类路径(`WITH RECURSIVE`)⑥附件元数据(仅元数据,未授权时降级)⑦任务检索 ⑧进展窗口 ⑨覆盖率四 scope ⑩年度目标清单 ⑪里程碑清单 ⑫新鲜度分档/总览/任意窗口/滞后清单 ⑬漂移检查 |
| `mock-mcp/_formal.py` | **正式源后端**:把工具调用映射到 PG 模板并包成与演示源同构的信封;未迁移的组合返回 `None` 回落演示路径 |
| `tests/test_o2oa_pg.py` | 69 项纯单元测试(不连库):规则、域值、模板形状、参数与占位符一致、每个模板必带准入守卫、正式源后端的回落判定 |

### 3.0 批次 2:口径移植以"契约数字"为验收标准(2026-09-10)

原 31 工具的口径写在 `mock-mcp/server.py` 的 docstring 里,里面有一批**可复现的数字**,
它们才是移植是否忠实的判据(不是"看起来像就行")。批次 2 的模板已在真 PostgreSQL 15.5 上
逐条复现:

| 口径 | 契约数字 | 模板实测 |
|---|---|---|
| `publish_split`(带正式任务门) | 943 / 123 / 1066 | ✅ 一致 |
| 对照:不带任务门 | 945 / 1068 | ✅ 一致(证明任务门必须带) |
| `import_split` | 943 / 943 / 0 | ✅ 一致 |
| `unpublished` 驳回(status=2) | 39 行 / 33 任务 | ✅ 一致 |
| `never_reported`(`NOT EXISTS`) | 55 条 | ✅ 一致 |
| 对照:`latest_progress_time IS NULL` | 只有 9 条 | ✅ 一致(故不能用 NULL 判据) |
| 新鲜度「从未报进展」 | 全量 9 / 在办 8 | ✅ 一致(多的那条是任务 88,已完成) |
| 各分档之和 | = 任务总数(可自校验) | ✅ 128 = 128 |
| `summary` | 943 行 / 73 任务 / 平均 12.92 期 | ✅ 一致(分母是"报过进展的 73 条",不是 128) |
| `formal_coverage`(两表并集) | 128 / 119 / 93.0% | ✅ 一致(只看技术组只有 73,集团组 46 条写在历史表) |
| `latest_round` | 一任务一行,共 73 行 | ✅ 一致(绝不返回全部历史:19 期任务的旧计划会被读成现在的计划) |
| `missing_next` | 0(73/73 都写了下一步) | ✅ 一致 |
| `pending_review` | 58 行 / 47 任务,任务 48 的 public_version 为空 | ✅ 一致 |
| `unpublished_by_task` | 72 条任务(按 version_no 去重) | ✅ 与直接查询一致 |
| `version_gaps` | 5 条任务缺号,缺号 = 最大期号 − 实际期数 | ✅ 逐行自洽 |
| 提交单总数 | 462(= 470 行减 8 个软删任务的单) | ✅ 一致 |
| `external_ids` 三个 O2OA 标识 | 460 / 460 / 60 | ✅ 一致 |
| `rejected_by_board` | 技术组 9/293 = 3.07%、集团组 4/169 = 2.37% | ✅ 一致(分子分母都在提交单上) |
| `inflight_external` | 枚举 59 / 取反会得 60 | ✅ 一致(cancelled 那张既未发布也不在途) |
| `by_kind` | progress 312 / initial 150 | ✅ 一致(合计 462) |
| `inflight_by_kind` | 状态 × 类型 **九档** | ✅ 一致 |
| `rounds_per_task` | 150 任务 / 462 单 / 3.08 单每任务 | ✅ 一致(分子分母都给出) |
| 文本规则 `number_conflict` | **任务 103 的 V8 冲突在集团历史表里**,三种冲突类型齐全 | ✅ 一致 |
| 对照:不看集团历史表 | 任务 103 命中 0 条 | ✅ 一致(证明必须扫两张表) |
| 文本规则 `availability` | 只返回「可用性 NN%」低于 90 的 | ✅ 一致(命中 19 条) |
| 文本规则 `keyword` | 默认 协调/协同/联动/牵头组织 | ✅ 一致(命中 19 条) |

三条由此固化的铁律:

1. **相对时间窗一律以 `as_of`(数据快照日)为基准,模板内禁止 `now()`** —— 演示数据停在
   2026-08-01,用系统时钟算"最近 30 天"会把窗口滑出数据,给出偏小的数(原工具把这个陷阱
   记作 `now_instead_of_as_of`)。`freshness_*` / `stale_tasks` 的 `as_of` 是必填参数,
   不给就 `ValueError`。
2. **"从未报进展"必须用 `NOT EXISTS` 判**(有没有已发布进展行),不能用
   `latest_progress_time IS NULL` —— 后者只找得到 55 条里的 9 条。
3. **`unpublished_by_task` 不带发布门**:它的筛选条件是"提交单已发布、进展未发布",不是"任务已发布";而 `pending_review` 的 `is_published = 0` 与 `status = 1` 必须各判一次(两套码值)。
4. **冗余列会漂移**:`task.latest_progress_time` 与真实最新已发布进展不一致的任务,在演示
   数据里有 **73/128 条**。只按冗余列回答新鲜度,错误答案与正确答案从外观上无法区分,
   因此必须提供 `latest_progress_drift` 这条检查。
5. **提交单域只加 `t.is_deleted = 0`**(462 = 470 − 8 个软删任务下的单),**不带任务发布门**;看板在 `task` 上,按看板提问必须从任务侧下推(462 张单 vs 清单封顶 200 行);
6. **「在途」按成员枚举 `SUBMISSION_INFLIGHT`**(含 `rejected`、不含 `cancelled`),写成 `status <> 'published'` 会多算 cancelled 那张(60 vs 59);
7. **文本规则的进展正文在两个地方**:技术组 `task_progress.latest_progress`、集团组 `task_group_progress_history.progress_effect`。只扫前者会漏掉集团任务的历史版本(任务 103 的 V8 冲突就在历史表里),必须 UNION 两张表,并在集团表未授权时显式说明这一限制;
8. **SQL 文本里的字面 `%` 必须写成 `%%`**:psycopg 会对整条 SQL 做占位符解析,`可用性(\d+)%` 这种正则会被当成参数标记并报 `only '%s', '%b', '%t' are allowed as placeholders`。这是真库验证抓到的运行时错误,纯单测与语法解析都照不出来;(**注释也一样**:写在 SQL 文本里的注释含单个 `%` 会被同样解析 —— 实测把说明写成 SQL 注释后,`id_format` 直接报 `incomplete placeholder: '%'`;注释要写在 Python 侧)
9. **`latest_round` 按 `version_no DESC, id DESC` 取最新一期**,不按 `progress_date`:补报的老期号可能有更晚的日期。

### 3.0.1 性能与索引要求(2026-09-10 实测,真 PG 15.5)

加上正式库应有的索引与统计信息后,批次 2 全部模板在演示库规模下耗时:

| 模板 / scope | 中位耗时 |
|---|---|
| 新鲜度分档 / 总览 / 任意窗口 / 漂移 | 0.29 – 0.67 ms |
| publish_split / import_split | 0.40 – 0.60 ms |
| `summary` / `version_gaps` | 0.90 / 0.92 ms |
| `pending_review` / `unpublished_by_task` | 0.96 / 1.33 ms |
| `latest_round` / `formal_coverage` | 1.42 / 1.60 ms |

放大到十万行进展(每任务追加 99 个历史版本)后,各模板耗时**没有增长**(0.13–2.63 ms),
执行计划在十万行时自动切到索引扫描 —— 无 N+1,距库级 `statement_timeout = 20s`
有三个数量级余量。

排名与时间轴两个出口同样测过(`bench_pg.py` 已覆盖,共 16 条模板):

| 模板 | 演示库规模 | 十万行进展时 |
|---|---|---|
| `progress_range(07-01~08-15)` | 1.03 ms | 1.09 ms |
| `progress_range_totals(今年以来)` | 1.16 ms | 3.36 ms |
| `published_progress_recency` | 0.93 ms | 0.59 ms |
| `rank_tasks` 三种并列语义 | 0.91 – 1.59 ms | 0.74 – 1.06 ms |
| `rank_tasks(shape=count)` | 0.80 ms | 0.76 ms |

> `progress_range_totals` 是唯一随行数增长的一条(1.16 ms → 3.36 ms):它要给出**总数**,
> 数行数没有提前退出的余地 —— 这是"总数活过截断"的价格,3 ms 量级仍可接受。
> 想省这一次查询就得接受"被截断时报不出真值",不划算。

**我们对索引的依赖(请数据侧确认正式库存在等价索引,或允许我们提交索引申请)**:

```sql
-- 最关键:待审核/最新一期/漂移检查都靠它;缺它时 pending_review 从 0.96ms 退化到 9.81ms
create index ix_task_progress_published on task_progress (task_id, version_no desc) where is_published = 1;
create index ix_task_admission        on task (board_id, sort_order) where is_deleted = 0 and workflow_status = 'published';
create index ix_task_progress_status  on task_progress (task_id, status);
create index ix_tws_task_status       on task_workflow_submission (task_id, status, round_no);
create index ix_task_latest_progress  on task (latest_progress_time);
create unique index ux_task_progress_task_version on task_progress (task_id, version_no);
-- 其余见核对目录 pg_indexes.py(共 20 条,按 PDF 主键/外键推导)
```

**测量方法上的三个坑**(都会让同一模板的读数差一个量级,别据此优化模板):

1. 批量导入后**没跑 `ANALYZE`** → 规划器按默认选择率估算,首测偏慢;
2. 重载数据时**索引一起被删** → 相关子查询退化成逐行全表扫描(`pending_review` 0.96ms → 9.81ms);
3. **不预热直接取单次读数** → 把首次规划与冷缓存算进去(同一模板能读到 62ms)。

正确做法:载入 → 建索引 → `ANALYZE` → 预热一次 → 取 N 次中位。

### 3.0.2 正式源后端与工具接线(2026-09-10)

`TASK_BOARD_DATA_SOURCE=o2oa` 时,已迁移的工具走正式源;其余仍走演示路径,**半迁移状态下服务仍然自洽**。

| 工具 | 状态 | 说明 |
|---|---|---|
| `weekly_task_query` | ✅ 已接线 | 关键词 / 分类 / 负责人 / 状态 / 项目组 / 看板;不指定看板也可用 |
| `weekly_progress_coverage` | ✅ 已接线 | 8 个具名 scope + `scope=text_check` 的三条文本规则(含 `task=` 按 id 或名字过滤) |
| `weekly_freshness_distribution` | ✅ 已接线(全部分支) | 分档 + 总览(同一信封)、任意窗口 `within_days`、滞后清单 `stale_days`(带 `reported_only`)、活跃清单 `recent_days`、按 `by=board/project_group` 的滞后/活跃占比(带服务端合计行)、`lag_bands` 每看板陈旧度分档、`drift` 漂移清单;`task=` 单任务档未迁移 |
| `weekly_year_goal_query` | ✅ 已接线 | 年度目标**行**清单(year=0 表示所有年度);集团板 109 行 / 46 任务、全看板 313 行 / 128 任务 |
| `weekly_milestone_query` | ✅ 已接线 | 支持按任务收窄(不带 `task=` 会答成整个看板第一页);任务 19 → 2 行 |
| `weekly_attachment_query` | ✅ 已接线 | 仅元数据(无 `storage_path`);集团板 52 条 / 28 任务;可选表未授权时报 `table_not_granted` |
| `weekly_owner_roles` | ✅ 已接线 | 角色拆分(主责/项目负责人/牵头领导/去重并集);孙立群 → 0/2/12/14,u3118 → 2/2/12/14 |
| `weekly_group_detail_query` | ✅ 已接线 | 集团板扩展表(目标成果/落实举措/完成时间/进度成效/多值负责人);46 行;`status=0 + non_empty=progress_effect` → 6 行矛盾;`completion_time` **按文本**匹配 2026 → 31 行 |
| `weekly_health` | ✅ 已接线 | 逐表精确行数(可选表不存在时返回 NULL 而非报错);12 张表 / 5,369 行 |
| `weekly_submission_query` | ✅ 已接线 | 9 个聚合 scope:提交单 462 张、外部标识 460/460/60(缺 402 行 = 87.0%)、驳回率 技术组 9/293 = 3.07% > 集团组 4/169 = 2.37%、按类型 progress 312 / initial 150、在途带进程号 59;带明细筛选的请求不迁移(回落演示路径) |
| `weekly_workflow_query` | ✅ 已接线 | 审批动作流水(可选表):分布 955/460/150/**13**、日志 **1,578** 行 / 150 任务 / 10.52、node×action **6 档**、`scope=recent` 按动作时间倒序;**`opinion` 按权限才出列** |
| `weekly_scale` | ✅ 已接线 | 三种 mode × 三种分组轴;技术组 totals 82/77/294/402、集团组 46/40/180/52(各组里程碑相加 = 全库 474,自校验未被 JOIN 放大);completeness 82/77/80/73;intensity 82 任务 / 943 行 / 11.5 |
| `weekly_rank` | ✅ 已接线 | 三种并列语义 × 六种子表度量:**cut 前 3 名 = 3 行**、**keep_ties 前 3 名 = 12 行**(第 3 名并列)、per_group 每组一行;附件第一名任务 73(20 个);未授权表(附件/集团历史)报 `table_not_granted` |
| `weekly_person_stats` | ✅ 已接线(9/14 scope) | 牵头人任务量首位 吴晓东 **14** 个且 **tied_at_top=3**;workload_top 保留三名并列;汇总 128 任务 / 16 人 / 全局均值 8.0;只带 1 个任务 4 人;标准安全组 **9 位牵头人 / 19 条任务**;跨组 12 人;双重角色 6 人;工号写法 69/50/9;填报首位 10515(63 轮 / 4 任务)。未迁移的 4 个 scope 仍走演示路径 |
| `weekly_attachment_stats` | ✅ 已接线(summary/by_ext) | 存活附件 **454 条 / 106 任务**;总字节 **1,954,375,767**(原样报出) / 1863.8 MB / 均 4203.9 KB;上传人 46;挂载点 315/58/81;扩展名 pptx130/xlsx116/pdf107/docx101 |
| `weekly_group_history` | ✅ 已接线(8 个 scope) | 明细 / `year` / `month` / `quarter` / `task` / `reporter` / `lag` / `linkage` + 日期窗(`date_from/to`、`last_days`、`last_months`)与 `latest_only`;已发布 **362** 行 / 46 任务、草稿 **42** 行;**`linkage` 分母是表内全部 404 行**(挂接率题);`lag` 按基准日算天数 |
| `weekly_group_owner_query` | ✅ 已接线 | 多值负责人**元素级精确**匹配:吴晓东 → 4 个集团任务(按 id `u3124` 同样 4 个);role=project 列出 46 行 |
| `weekly_task_ranking` | ✅ 已接线 | 按子表条数排名(附件/进展/里程碑/提交单):**INNER JOIN 语义**(零条目的任务不参赛),列名照抄参考查询 `id / task_name / cnt`,并回显 `metric` / `metric_label`;附件榜首任务 73(20 个,`tied_at_top=1`);进展榜首任务 4(18 期,`tied_at_top=12`)。未迁移的 metric 回落,附件未授权报 `table_not_granted` |
| `weekly_progress_range` | ✅ 已接线 | 时间轴出口:列集合 `task_id / task_name / version_no / progress_date / report_time / lag_days`;窗口两端闭区间,相对窗口锚在基准日(非系统时间);`total_count` / `total_tasks` **活过截断**;短窗口 0 行时附「按月上报」提示;`by=` / `peak=` / `date_field=report_time` 回落 |
| `weekly_milestone_stats` | ✅ 已接线(6 scope × 10 维度) | summary 474 / 已完成 242 / 51.1%;`deleted` **全表口径** 566/36/602(不套任务闸门);`fully_deleted` 用 NOT EXISTS 得 **3** 条(「有软删行」是 23 条,差一个量级);`per_task` LEFT JOIN 保留零里程碑任务并把 `top_tie_count`(**23**)提到顶层,总览挂 `summary` 键;`mismatch` 两个 kind 是反向量词(6 ↔ 限 2026 只剩 3;8 ↔ 限 2026 涨到 22) |
| `weekly_year_goal_stats` | ✅ 已接线(6 scope) | `by_year` 2025/2026/2027 = 128/117/68(**合计 313**),`include_informal=True` 放开闸门得 **387**(差 74 条挂在非正式任务上);`coverage` 用 **EXISTS** 不是 JOIN,分母恒为全部 128 项(2026 → 117 有 / 11 缺 = 91.4%);`missing` 提到顶层 `total_count`(11;加 `in_progress_only` → 10);`missing_by_group` 各档之和 = 11;`span` 均值 **2.45** 由服务端算(分母只含设过目标的任务);`multi_year` 2026×2025 = **117**(技术组 77);缺 `year` / `board=` 给看板名 / `span` 带 `year` 一律回落 |
| `weekly_schema` | ✅ 已接线 | 复合信封(boards / categories / table_columns / field_notes,**没有 `columns`**):看板 2 条、分类树 47 条(`board=group` → 19 条);字段字典覆盖契约内 **12 张表**(task 表 22 列,含 `is_deleted`)+ 剔除禁止外泄列;`board=` 给看板名回落 |
| `weekly_freshness` | ✅ 已接线 | **数据快照日**,不是新鲜度分布:各看板行(技术组 latest 08-09 / 落后 6 天、集团组 08-14 / 1 天)+ `overall`(全库 08-14 / 1 天 / 128 项)+ `published_progress`(技术组 **07-31** / 15 天 —— 与 08-09 差 9 天即发布滞后;集团组取自集团历史表)+ `tech_import`(跑完批次 **07-31**,最新批次 08-15 仍在处理中);两张可选表未授权时**保留键、值给 null** 并在口径里说明 |
| `weekly_field_completeness` | ✅ 已接线(12 个字段) | `project_owner_id` 128 项里 **119** 项填了 / 9 项缺 = 93.0%,而 `project_owner_name` **128 全满** —— 姓名列与 ID 列不是一回事;明细表字段用 LEFT JOIN(分母仍是 128),并**另给裸表口径**(`task_group_detail` 55 行);`implementation_measure` 裸表 55 行**全非空但只有 1 个不同的值** → 必须报「填写率 100% ≠ 字段可信」;`list_missing` 给缺项清单 + 顶层 `total_count` |
| `weekly_progress_history` | ✅ 已接线 | 单任务各期(任务 3 = **14 期**),`prev_progress` / `gap_days` 由服务端 `lag()` **相邻两期并排**;`gap_summary` 均值 **30.4** / 分母 13(首期不进分母);同名系列显式回报(任务 3 另有 2期/3期/4期 **3 条独立任务**);任务 7 已发布 0 期、含未发布 1 期(`published_only` 开关);`review_comment` 按权限打码 |
| `weekly_import_audit` | ✅ 已接线(4 分支) | 批次 **20** 个(批次数 = 去重日期数 = 去重导入时间数);`latest_finished` 挑的是**跑完**那批(第 **19** 批 status=1,**17** 条任务),不是日期最新的第 20 批(它 status 0、实落 0 行 —— 只按日期取会答成一批没跑的);`reconcile_rows` 声明 vs 实际 **20 批全对不上**(第 20 批声明 43 / 实落 0);`orphans` 孤儿 **0**(手工填报 120 行另计,不混进孤儿数);未授权时报 `table_not_granted` |
| `weekly_task_lifecycle` | ✅ 已接线 | 建立/发布这**另一个钟**(不是"报进展"那个):128 项、均值 **30.3 天**到发布、最长 60 天;`by=year` 各档 `currently_finished` 相加 = 全库已完成 **31**(2025 年 26 + 2026 年 5);口径写明这是"按建单档看当前状态",任务表**没有完成时间列** |
| `weekly_task_detail` | ✅ 已接线 | 复合信封(task 22 列 / group_detail / recent_progress 最近 3 期 / year_goals):任务 3 有 3 期进展、3 条年度目标;任务 101 有 1 行明细、0 期进展 → 口径指路 `group_detail.progress_effect`;两套负责人列打架时**点名**(task 行「陈志远」vs 明细「刘海涛,韩雪峰」)并说明集团板 46 条全不一致;**R-12 无条件在场**(挂子查询会让技术组任务看不到);纯数字 token 只当 id,不拿 LIKE 匹配到的别的任务顶替 |
| `weekly_approval_turnaround` | ✅ 已接线(4 scope) | 已完成轮次 **400**、均值 **14.7 天**、最长 **59 天**;按看板 技术组 257/14.5 + 集团组 143/14.9(= 400,自校验);`slowest` 最慢那档是**并列 2 轮**(任务 **76** 与 **143**,都是 59 天)→ `top_tie_count` 提到顶层并写进口径;`pending` **刻意不套发布闸门**(待审单本就未发布,加 R-01 会得到空队列),积压榜首已等 **583 天**,天数锚数据基准日 |
| `weekly_aggregate` | ✅ 已接线(9 个分组轴) | `board` 82/46 = 128;`category` 不带看板 47 条、`board=tech` **28 条**(7 一级 + 21 二级 —— 过滤**同时**落在分类树上)、`board=group` 19 条,且保留 cnt=0 空分类;`primary_category` 11 档 + `order_by=finish_rate` 首行换成完成率最高的档;`top_sub_per_primary` 一组一行;`status` 14/78/31/5 = 128;`workflow_status` **七档、唯一不加发布闸门**(published 128 + 未发布 22);`project_group` **前 4 组累计 49.22% 未过半、第 5 组才 58.59%**(小数位取 2 的理由)、`top=4` 是硬切且口径写明"共 11 组、不要补列";`owner` 16 档(空值归「(未填)」);`name_series` 64 个家族 / **33 个多期家族** / 涉及 **97** 条任务 |
| `weekly_group_stats` | ✅ 已接线(14 个 scope) | 集团板专表统计:`owners` 46 条(多值牵头 19 / 单人 27 / 未填 0)、**去重 23 位牵头人**(逐元素切,不用 LIKE);`separators` 半角逗号 26 / 单人无分隔符 18 / 全角顿号 2;`completion_time` 6 条标准日期 + 40 条自由文本;`completion_time_values` 去重 **28** 种原样取值 → `completion_time_formats` 归成 **6 档**(含「底」11 档按优先级);`overdue` 只露任务 **123**(2026Q2 → 2026-06-30,超 **46** 天),判不了的 **34** 条单独计数;`attachments` 零附件留在清单里(46 条里 **18** 条没有)、`attachment_distribution` 真值 0/1/2/3 = 18/**17/3/5**(拿 8 行清单手数会得 21/4/4);`history_rounds` 46 条都在、至少 10 期 **13** 条;`status_effect_conflict` **6** 条;`project_group_raw` 两个分母 **55 / 46**;`effect_consistency` 的 `same` 按参考给 **1/0** |

三条硬规则:

1. **信封与演示源逐字段同构**(`ok` / `caliber` / `snapshot_note` / `snapshot_date` / `source_tables` / `columns` / `rows` / `row_count` / `has_more`),agent 侧无需改动;
2. **未迁移的 scope 或参数一律返回 `None` 回落**,绝不返回一个范围更小的答案(例如新鲜度的 `by=` / `lag_bands=` / `recent_days=` 仍是演示路径);
3. **截断判定不能靠 SQL 里的 `LIMIT`**:模板把行数上限写在 SQL 里(性能上正确),但这样"刚好取满 200 行"与"被截断"长得一模一样 —— 信封因此按 `limit + 1` 去查再截回 `limit`,约定"清单类模板把行数上限放在最后一个参数"(`cap_last_param`)。实测踩过:313 行的年度目标被报成 200 行且 `has_more=false`;
4. **四张可选表默认视为未授权**(说明里必开的是 8 张):`_formal.optional_granted()` 读 `TASK_BOARD_GRANTED_OPTIONAL_TABLES`,未授权时返回 `table_not_granted` 错误,**不回落演示路径**(回落会去连演示源,把"没权限"变成"另一个数据源的答案");
5. `snapshot_note` 换成正式口径(「国数正式只读源…非演示数据」),`snapshot_date` 用基准日(`GUOSHU_AS_OF` 可固定,便于与演示快照日对齐)。

**端到端验证**(真 PG + 正式源模式,159 项断言全通过):信封 9 字段齐全、`source_tables` 指向正式表、
publish_split 943/123/1066、summary 943/73/12.92、never_reported 55、任务 103 的 V8 冲突(来自集团历史表)、
新鲜度分档 63/44/8/9/4 且合计 = 任务总数、在办「从未报进展」8、漂移 73、排名的并列自检、
时间轴的 366 行/70 任务与短窗口提示、新鲜度各分支(23 / 18 / 8 / 21-82=25.6% / lag_bands 17-56-9)、
集团历史 8 个 scope 与自然月回溯,未迁移参数全部回落。

### 3.0.3 排名与时间轴两个出口(2026-09-10 真库核对)

本批接线 `weekly_task_ranking` 与 `weekly_progress_range`。两个工具各自有**一份参考查询**,
形状不同就不能共用一条模板 —— 这是本批的主要发现:

| 项 | `weekly_rank`(形状 `rank`) | `weekly_task_ranking`(形状 `count`) |
|---|---|---|
| 子表 JOIN | `LEFT JOIN`,**零值任务保留**("最少"那一端的答案) | `INNER JOIN`,零条目的任务**不参赛** |
| 列名 | `task_id / task_name / metric_value` | `id / task_name / cnt`(照抄参考查询) |
| cut 档自检 | 顶层 `total_count` = 符合口径的任务总数(128) | 无(参考查询也没有),只有 `metric` / `metric_label` |

三个真库核对出来的结论(都已写成断言):

1. **硬切在并列值上的取舍是任意的,必须把并列数交出去**。`tied_at_top` = 与首行同值的任务数:
   进展期数榜首 18 期上有 **12 条并列**(所以 keep_ties 前 3 名是 12 行,cut 只给 3 行);
   附件的 20 个上只有 1 条(任务 73 是**唯一**冠军)。少了这个数,"谁最多"会被答成唯一第一名。
2. **`project_team_size`(项目团队人数)在正式数据里没有区分度**:`task.project_owner_name`
   128 条**全部没有分隔符**(空负责人 0 条),取值集合 = {1}。此时 cut 的前三名由 task id 决定
   (1/3/4),`tied_at_top=128` 才是这个问题的真答案 —— 多值负责人落在另一列
   `task_group_detail.project_owner_names`(只覆盖集团板 46 条),两列不可互换。
   同时补上参考查询那道过滤:空/ NULL 负责人**不算 1 人团队**,直接排除。
3. **时间轴上的 0 行有两种原因,必须区分**。`task_progress` 按月上报:全库正式进展 943 行、
   最大 `progress_date` 是 **2026-07-31**(距基准日 15 天),所以任何短于半月的窗口**必然为空**。
   短窗口取回 0 行时口径里追加提示(数字现查,不写死):改用
   `weekly_freshness_distribution recent_days=7`(得 23 条),也别退而报「最新一批」的期数。

对齐参考查询的另一半是**参数校验收紧**:`last_days < 0`、非 `YYYY-MM-DD` 的日期、起点晚于终点,
一律 `invalid_argument`。不校验就会把 `2026/07/01` 直接喂给 PG(报出来的是驱动层语法错),
或让负天数反算出空窗口 —— 那不是「窗口内没有进展」,是窗口本身不成立。

> 口径提醒:**自检数放顶层,不要塞进行内**。`total_count` / `tied_at_top` 说的是"这个名次站了几个人",
> 不是分页信息;行内多一列会被当成分页字段读。演示源的 cut 档也把 `total_count` 放在顶层
> (不是列),两边的键位必须一致 —— 模型只会读它在演示源下学会的那个键。

> 口径提醒:**两个排名工具不能互相代答**。`weekly_task_ranking` 的 metric 名(attachments /
> progress / milestones / submissions)与 `weekly_rank` 的 metric 名(progress_rounds /
> milestones_done / group_rounds / project_team_size …)是**两套**,同一个数在两边的中文标签也不同
> (进展在 ranking 里叫「正式进展版本数」、在 rank 里叫「已发布进展期数」)。映射表写在
> `_formal._RANKING_METRICS`,标签照抄各自工具侧的地图。


> 口径提醒:**多值列的匹配要按元素、且两种分隔符都要处理**:演示数据里多值负责人文本
> 既用顿号(`任建华、潘启明`)也用半角逗号(`胡建国,方永康`)。只按顿号切会把逗号串当成一个人;
> 用 `LIKE '%名字%'` 则会在不同人之间碰撞(短名是长名的子串)。正确做法是先统一分隔符再切数组判等。

> 口径提醒:**NULL 的排序方向必须显式写**:MySQL 降序把 NULL 放最后、升序放最前,
> 而 PG 默认相反 —— 不加 `NULLS LAST/FIRST` 会算出与演示源不同的名次。
> 口径提醒:**排名度量的并列语义由服务端定**:同一份数据里「进展期数前 3 名」在 cut 下是 3 行、
> 在 keep_ties(RANK)下是 12 行,两个集合不同,不能让调用方拿到明细后自己裁。
> 口径提醒:引用 task 行上列的度量(如项目团队人数)**必须把该列写进 GROUP BY** ——
> PG 只在有主键/唯一非空约束时才做函数依赖推断,缺约束的环境会直接报错。

> 口径提醒:多张子表同时 JOIN 时,**每个子表计数都必须 `COUNT(DISTINCT 主键)`** ——
> 不去重时技术组里程碑会从 294 变成 1363。自校验:各组里程碑相加应等于全库总数(474)。
> 另:比值列在 PG 侧是 numeric,会保留两位(`11.50`),MySQL 侧渲染成 `11.5`,数值相同、按数值比较。

### 3.0.4 新鲜度与集团历史:列集合逐列对齐参考查询(2026-09-10 真库核对)

这一批不是"多接了几个工具",而是把**已接线工具的列集合与分支逐条对齐参考实现**。
起因:数字对得上、列名对不上 —— 模型读的是列名,列名一换,同一个问题的答法就跟着换。

**新立一条纪律(已进第 6 节硬约束):每个已接线出口的 `columns` 必须与参考查询逐列同名。**
按这条查出并修掉的(数字都没错,错的是列名与分支):

| 出口 | 之前 | 参考查询的列 |
|---|---|---|
| `drift=True` | `id / task_no / task_name / denormalized_time / real_newest_progress` | `task_id / task_name / latest_progress_time / actual_latest_report` |
| `stale_days` 清单 | `id / task_no / task_name / status / project_owner_name / latest_progress_time / days_behind` | `id / task_name / status / latest_progress_time / days_since` |
| `within_days` | 单列 `reported_within` | `task_count / newest_progress / days_behind`(一行三列) |
| 集团历史明细 | 多给了 `id / task_no / is_published / workflow_submission_id` | `task_id / task_name / version_no / progress_effect / completion_time / reporter_id / report_time` |
| `by=lag` | 用 `now()` 算天数、列名 `days_since` | `task_id / task_name / lag_days / rounds / last_report_time`,天数按**基准日**算 |
| `by=linkage` | `rows_total / with_submission / without_submission`,分母 **362**(过了发布闸门) | `total_rows / linked_rows / unlinked_rows / published_rows`,分母 **404**(挂接率题的刻意例外) |

两个真错(不是命名问题,是算错):

1. **`by=lag` 用了 `now()`** —— 违反"相对时间一律锚数据基准日"的硬规则。演示数据基准日是
   2026-08-15,用系统当前时间会让整张滞报榜偏移;现已改为 `基准日 - MAX(report_time)`。
2. **`by=linkage` 的分母过了一道不该过的闸门** —— 挂接率问的是"表里有多少行挂上了提交单",
   分母该是全部 404 行;只用已发布 362 行会把 42 条草稿的挂接状况整体丢掉
   (两个数现在同排返回:404/0/404/362)。

**日期相减必须两端都按日期**(`(a)::date - (b)::date`),不能写成 `date - timestamp`:
后者返回 interval,`date_part('day', ...)` 会把 `2026-08-15` 减 `2026-07-31 20:10` 算成 **14** 天,
而 DATEDIFF 语义是 **15** 天 —— 差一天,榜单边界就挪一条。

新接线的分支与真库核对结果:

| 分支 | 真值 |
|---|---|
| `recent_days=7` | **23** 行(docstring 的 23);不加 status 闸门(问有无上报,不是是否在办) |
| `within_days=7` | 23 个任务 / 最新 2026-08-14 / 滞后 1 天(与 `recent_days` 同一份事实,两条出口不许打架) |
| `stale_days=90` | 总数 **18**,其中从未报过 **8**(在办闸门;docstring 的 8 与 C3-04 的合计 18) |
| `stale_days=90 + reported_only` | 排除 8 条无天数可比的;首行 days_since = **250** |
| `stale_days=90 by=board` | 技术组 **21/82 = 25.6%**、集团组 0/46 = 0%;合计行 **128 / 21 / 107 / 2** |
| `recent_days=90 by=project_group` | 11 个组,按 `active_pct` 倒序(算力网络组 92.9% 居首) |
| `lag_bands=True` | 技术组 **17/56/9**(=15-30 天 / 超过 30 天 / 无正式进展)和 **82** 相符;集团组 17/28/1 和 **46** 相符 |
| `in_flight=True` 分档 | 在办 **92** 条,"从未报进展" 8 条 |
| 集团历史 `last_months=3` vs `last_days=90` | **103 vs 100** 行 —— 自然月回溯落在 05-15,90 天落在 05-17,差的正是三条五月的行 |
| 集团历史 `by=year` | 2025 = 137 行 / 39 任务,2026 = 225 行 / 46 任务,相加 = 362 |

> 口径提醒:**只给 `by=` 而不给天数要明确报错并指路**。参考实现为此专门写了一段提示:
> 分组档回的是各组滞后/活跃的条数与占比,必须先有天数才有口径。此前实现会静默回落到
> 全量分档(分组轴连同问题一起丢掉),现在返回 `invalid_argument` 并说明该传 `stale_days=90`
> 还是 `recent_days=90`。

> 口径提醒:**分类题的排序端要跟着问句走**。问"哪个组滞后占比最高"按 `stale_pct` 倒序,
> 问"各组近 N 天活跃度"按 `active_pct` 倒序 —— 排错端等于把末位当第一。两者互补(相加 100),
> 分母 `total` 与服务端算出的占比同排返回,调用方不需要(也不应该)拿别处的任务数手工相除。

> **发现的文档/数据不一致(按数据走)**:mock 注释写「集团组 14/28/4」,真库实测是 **17/28/1**
> (合计都是 46,只差 3 条在 0-7 与 15-30 两档之间搬家)。技术组的 17/56/9 与注释逐字一致,
> 说明分档逻辑本身没错;三种口径变体(只算已发布行 / 不算发布闸门 / 按 `board_id = 2` 判看板)
> 都试过,没有一种能得出 14/28/4。结论:那行注释是旧数据留下的,断言按真库写。

> 测量纪律:**放大数据会污染口径验收**。`bench_pg.py` 会往 `task_progress` 插 99 倍历史版本,
> 之后 `publish_split` 会从 1066 变成 106600、行数断言全部失真(而报告本身看不出原因)。
> 现在 bench 结束会自己删掉 `id >= 1000000` 的合成行并 ANALYZE;`reset_pg.py` 用于事后恢复。

> 口径提醒:**动作数 ≠ 单数**。审批流水里 `rejected` 有 13 条,那是**动作**条数;
> 「驳回率」的分子必须用提交单自己的 `status = 'rejected'`(技术组 9、集团组 4)。
> 审批流水的正确闸门是 `t.is_deleted = 0`(1,578 行);再加任务发布门会掉到 1,519。

> 依赖提醒:本工作区的 mock 服务用 **mcp 1.x 的 FastMCP API**;PyPI 上 mcp 2.x 已把它改名为 `MCPServer`,
> 未钉版本的新环境会直接导入失败。仓库已声明 `mcp>=1.28.1,<2.0.0`(mock 服务运行时沿用同一环境),
> 部署新机时不要放宽这个上界。

### 3.1 第一批迭代要点(2026-09-10)

- **时间字段**:`to_char(文本列)` 在 PG 上直接报错(`function to_char(text, unknown) does not exist`),
  而国数方说明第六条明确时间字段"可能是文本也可能是时间戳"。`normalize_ts_sql` 因此改为
  先 `::timestamp` 再 `to_char`,一条表达式同时适配两种形态;新增 `parse_ts_sql` 供日期窗口比较,
  避免文本列被当成字符串比较。
- **取数提速**:最新进展由"每行一个 `MAX(version_no)` 相关子查询"改为 `DISTINCT ON (t.id)`
  单次扫描加排序 —— 任务数与版本数增长时,前者是成倍的额外往返开销。
- **补齐规则**:历史版本进展(`status = 3`)、递归分类路径(`task_category.parent_id`)、
  附件元数据边界(只出文件名/大小/上传时间,**不出现 `storage_path`**)。
- **域值表**:国数方说明的 `task.workflow_status` 域值表未列 `cancelled`,但真实数据里存在。
  文档域值集合保持原样,另设 `OBSERVED_EXTRA_WORKFLOW_STATUS` 让分布类问答能列出该值
  (准入仍只匹配 `published`)。

### 3.2 字段对齐结论(2026-09-10 静态对账)

以国数方说明为基准,对 12 张表族逐列核对演示库(weekly_mock)结构:

- **11 张表与说明逐列完全一致**(含 8 张必开表):`task_board` / `task_category` / `task` /
  `task_progress` / `task_milestone` / `task_workflow_submission` / `task_group_detail` 等;
- 唯一差异:`task_year_goal` 在演示库缺 `created_at` / `updated_at`(不影响问答);
- 说明未给出 4 张可选表的字段清单,代码里凡用到这些表的地方都在注释中标明"列名沿用演示库结构,
  联调时按真实库核对"。

## 3.3 数据权限现状(2026-09-10 实测)

只读账号已能登录、会话只读有效(写操作实测全部被拒),但**目标库缺 `CONNECT` 授权**,
因此真库数据尚未引入:

| 项 | 实测 |
|---|---|
| 账号 | `task_board_readonly`,隶属 `read_only_all`(`pg_read_all_data`),PG 15.5 |
| 可与不可 | `he3mysql` / `postgres` 可连(仅扩展视图,0 张业务表);**`o2oa` / `oa_biz` / `oa_agent` 拒 CONNECT** |
| 报错 | `FATAL: permission denied for database "o2oa"`(`User does not have CONNECT privilege`) |
| 需要 | `GRANT CONNECT ON DATABASE o2oa TO read_only_all;`(纯只读用途) |

**每轮复核**:2026-09-10 再测一次,矩阵仍是 `he3mysql` ✅ / `postgres` ✅ / `o2oa` ❌ / `oa_agent` ❌ / `oa_biz` ❌,
授权尚未生效。等待期间的口径核对走「一台可写 PG + 演示库数据」的同构实例(见 3.0.1 与第 7 节),
所有契约数字都在那上面复现过;真库一开,同一批模板直接跑 `dump_real_schema.py` 做逐列 diff 即可。

授权一生效,即可用仓库外的核对脚本一次性产出:表/列/注释/行数、枚举真实分布、
时间字段真实形态、最小抽样,并与本说明的字段字典逐列 diff。


## 4. 31 工具迁移批次(未完成部分)

**当前进度:19 / 31 已接线**(见 3.0.2 的表);余下 12 个工具调用时仍走演示路径。

- **批 1(已提交)**:查询类高频工具的三条核心 SQL 模板 + 规则基础设施;
- **批 2**:`weekly_schema / task_query / task_detail / progress_history /
  progress_coverage / aggregate / rank / freshness* / year_goal*` 等任务/进展/
  年度域工具的 PG 化与参数白名单;
- **批 3**:`milestone* / workflow_query / submission_query / approval_turnaround /
  person_stats / owner_roles / attachment* / import_audit / scale / field_completeness`
  等子表域工具;
  - 其中 `weekly_milestone_stats` 的接线要点(第 25 轮侦察,尚未接线):6 个 scope
    (`summary` / `by_dimension` / `deleted` / `fully_deleted` / `per_task` / `mismatch`)×
    9 个维度(`year / category / group_name / project_group / status / task_status /
    primary_category / reporter_id / owner_id`)。三个必须照抄的判据:
    (1) `status` 是 0/1 两值码,「已完成」只认 `status = 1`,不做文本匹配;
    (2) `fully_deleted` 用 **NOT EXISTS**(里程碑全被软删的任务,3 条),不能用
    「有软删行」(那是 23 条);
    (3) `per_task` 要保留零里程碑任务,并给 `top_tie_count` —— 榜首是 **23 路并列**在 6 条;
    另:`group_name` 是里程碑行自己的短标签(6 种),`project_group` 是任务的专项组(11 种),
    两者不是一回事,不可互换;
- **批 4**:`group_detail* / group_owner* / group_history / group_stats` 集团板专表域;

每批交付:PG SQL 模板 + 参数/口径 + caliber 文案 + 契约测试期望值(正式真值)。
联调账号与样例就绪前,以模板+单测+文档先行,真库验收后回填期望值。

### 3.0.5 列集合对照器:把"列名对不对"变成可批量复核的一件事(2026-09-10)

第 3.0.4 节那条纪律靠人读代码去核,一次只能核几个出口。本批把它做成脚本
(`column_parity.py`,放在核对目录):

1. **演示路径**:把 `_store` 里碰 MySQL 的几个函数换成桩,调用工具,取它**返回信封里的
   columns**(演示实现里有几个 scope 是手工拼信封的,只解析 SQL 会把它们误判);
2. **正式路径**:`TASK_BOARD_DATA_SOURCE=o2oa` 调同一个工具,取信封的 `columns`;
3. **判定**:正式源的列集合必须与演示路径**某一个**候选逐字相同(演示源常有"分档 + 总览"
   两次查询,正式源把它们合进一个信封)。

首轮 62 个用例暴露 17 处不一致,修完两批后剩 6 处待办。判定口径说明:参考查询的列必须
**全部在场且同名**;正式源**额外多给**的列是允许的(加信息不加歧义),但要在口径里写明它是什么。

本批按这条修掉的(`_o2oa_templates.py`):

| 出口 | 之前 | 参考查询的列 |
|---|---|---|
| `weekly_task_query` | 少了 `board_id / category_id / status` | 那三列在场(`category` 名称是本移植多给的) |
| coverage `summary` | `earliest_progress / latest_progress / max_version_no` | `earliest / latest / max_version` |
| coverage `publish_split` | 少 `tasks` | `published / unpublished / total / tasks`(tasks = 1066 行涉及 81 条任务) |
| coverage `import_split` | `imported / manual / total` | `total / from_import / manual / manual_unpublished / batches` |
| coverage `unpublished` | `status / progress_rows / tasks` | `status / status_label / cnt / task_count` |
| coverage `never_reported` | `id / task_no / task_name / status / project_owner_name` | `task_id / task_name / board_name / project_group / has_group_history`,并补 `total`(55)与 `both_empty`(9)两个口径 |
| submission `by_kind` | `forms` | `submission_count` |
| submission `by_status` | `forms` | `cnt` |
| submission `inflight_count` | `inflight_forms` | `inflight_submissions`(**61**,docstring 的 61;59 是「在途且带进程号」那档) |
| submission `inflight_by_board` / `inflight_by_kind` | `forms` | `submission_count` |
| submission `rounds_per_task` | 逐任务行 `tasks / forms / rounds_per_task` | 一行三列 `avg_rounds / total_submissions / tasks`(462 / 150 = 3.08) |
| workflow `by_node_action` | `actions` | `action_count` |
| workflow `actions_per_task` | `actions / tasks / actions_per_task` | `avg_actions / total_actions / tasks`(1,578 / 150 = 10.52) |
| workflow `recent` | 少了提交单侧字段,时间列叫 `action_time` | `id / task_id / task_name / round_no / reporter_name / status / node_type / action / operator_name / opinion / acted_at`(**INNER JOIN 提交单**) |
| person `workload_summary` | 只有三列 | 补 `max_tasks / min_tasks`(14 / 1 —— 只有均值时分不清"人人 8 条"与"有人 14 有人 1") |
| year_goal(行清单与任务清单) | 自造 `task_no` / `goal_filled` | `task_id / task_name / year / current_year_goal / milestone_summary` |
| group_owner | `owner_ids / owner_names`(两角色同名) | **按角色给原名**:`lead_owner_names / lead_owner_ids` 或 `project_owner_names / project_owner_ids`,并加 `owner_count`(这行的负责人个数) |

> 口径提醒:**敏感字段是打码而不是删列**。参考实现的 `_scrub` 对 `opinion` 的处理是
> 保留列、值写成「[按权限不展示]」;此前正式源在无权限时**整列不出现**,调用方就分不清
> "这条没有意见"与"我没权限看意见"。现已改为同语义的按值打码。

> 口径提醒:**"新增一档"要标明是新增**。`weekly_workflow_query scope=by_action` 不在参考
> 实现的口径表里(它的分面是 node x action),是本移植为"各动作各有多少条"新增的;
> 列名与 node x action 那档保持一致(`action_count`),并在模板注释里写明它是扩展。

**判定口径(第二轮收紧后的版本)**:参考查询的列必须**同名同序**出现在正式源里;
正式源**额外多给**的列允许(加信息不加歧义),但在报告里单列一栏供人过目 ——
「多给一列」与「少给/改名/换序一列」是两种性质,不能混在同一个"不一致"里。
按这个口径,62 个用例现在是 **59 一致 / 1 待查 / 2 取不到列**:

- `attachment_stats summary` 与 `attachment_query` 的差异原来是**列序**问题
  (同名但顺序不同),已按参考顺序调整;`by_ext` 已改成参考实现的**每档一行**
  (`ext / n / total_bytes / total_mb`,首行 pptx 130),不再与 summary 共用一种形状;
- 剩 1 处 `import_split` 与 2 处 `text_check` / `health` 是**对照器侧**的问题
  (演示实现有手工拼信封的分支,桩还没覆盖到),不是正式源代码的差异 ——
  下轮补桩即可。

### 3.0.6 列集合对照收到 69/69:那 3 处全是**对照器**的错(2026-09-10 第 26 轮)

上一轮把剩下 3 处记成"待查 + 桩未覆盖"。本轮查清:**正式源的列是对的,错在对照器自己**,
三处各有各的成因,一并修掉后 62 个旧用例 + 7 个新增用例全部一致(69/69)。

| 出口 | 症状 | 真因 | 修法 |
|---|---|---|---|
| `scope=import_split` | 演示列解析成 `['total', 'AS']` | `_parse_aliases` 找 `FROM` 用的是**裸四字符窗口**:`AS from_import` 里的 `from` 被当成 FROM,SELECT 列表被切在第二个聚合函数上 | 按**词边界 + 括号深度**找深度 0 的 `FROM`(词边界手写,不用 `\b` + `re.match(text, pos)` —— 实测那个组合在带 `pos` 时压根匹配不上,会把整条 SQL 当成 SELECT 列表) |
| `scope=text_check` | 取不到演示列(NO-DEMO) | `_text_check` 走 `store.all_rows`(全文扫描),桩只替换了 `fetch` / `scalar`,于是它去连真实 MySQL 然后抛异常 | 补 `all_rows` 桩(列名在演示实现里是**手工拼的**,与 SQL 无关,给空行集即可走到那个 return) |
| `weekly_health` | 取不到演示列(NO-DEMO) | 演示实现**不用** `store.fetch`,自己开游标逐表 `count(*)` | 补 `_FakeConn` / `_FakeCursor` 桩;并给对照器加一条**载荷键比对**:没有 `columns` 的出口(体检回的是 `row_counts` 映射)按顶层键比,正式源必须照样给出演示源那几个键(多给允许) |

顺带固化两条:**对照器要能就地跑**(`CHATBI_WORK` / `CHATBI_MOCK_DIR` 两个环境变量,
默认仍是 H100 的 `~/chatbi_check`),以及**语法校验按分支枚举**(`check_pg_syntax.py` 现在把
`milestone_stats` 的每个 scope × 每个维度 × 带不带门槛都过一遍 —— 维度表达式直接拼进 SQL,
漏一个就是一条只在特定问句上才炸的语法错;用例从 21 条升到 41 条)。

### 3.0.7 里程碑统计接线:三条判据与两个"像但不是一个轴"(2026-09-10 第 26 轮)

`weekly_milestone_stats`(6 scope × 10 维度)接线完成。侦察结论里那三条判据**逐条在真库复现**:

| 判据 | 真库实测 |
|---|---|
| `status` 是 0/1 两值码,「已完成」只认 `status = 1` | summary 474 = 已完成 242 + 未完成 232,两值相加恰好等于总数,没有第三档 |
| `fully_deleted` 用 **NOT EXISTS** 未删里程碑 | 得 **3** 条(任务 63/78/110);对照「有软删里程碑的任务」是 **23** 条,差一个量级 |
| `per_task` 保留零里程碑任务 + `top_tie_count` | 128 项里 3 项为 0 / 125 项至少 1 条;榜首 **23 路并列在 6 个**,首行任务 8 |

同一轮里还固化了几条**此前只在文档里、没有出口能验**的性质:

1. **`deleted` 是唯一不套任务闸门的 scope**:全表 566/36/602,套上闸门只剩 474 —— 差 128 行,
   这正是"按任务过滤会少算"的量化证据。它与其余五个 scope 的口径**正好相反**,别顺手统一。
2. **`per_task` 的年度/类别条件必须挂在 LEFT JOIN 的 `ON` 上**:进 `WHERE` 会把"没有该年度
   里程碑"的任务整行删掉,而它们正是要数的部分 —— 分母从 128 缩到 112,覆盖率永远算成 100%。
   挂 `ON` 上得 2026:16 项没配 / 112 项配了 = **87.5%**。
3. **`mismatch` 的两个 kind 是反向量词,限年度一个收紧一个放宽**:`task_done_milestones_open`
   不限年度 6 项、限 2026 **只剩 3 项**(限定年度会漏掉跨年度的更硬矛盾);
   `milestones_done_task_open` 不限年度 8 项、限 2026 **反而涨到 22 项**。两个数都对,
   但答的是不同的话,口径里必须写清是哪一个。
4. **`top` 默认 8,截断必须看 `has_more`**:上一条的 22 项在默认 `top=8` 下只回 8 行 ——
   不报 `has_more` 的话,"限年度没变化"与"被截断"长得一模一样(端到端验收专门钉了这条)。
5. **零里程碑任务与 `fully_deleted` 在本库是同一批 3 条**:里程碑被全部软删后,在 `per_task`
   口径下一条都不剩 —— 两个问句会指向同一批任务,口径里写明,免得被当成两条独立证据。
6. **维度白名单里 `group_name` 与 `project_group` 是两个轴**:真库上 `group_name` 6 个短名
   (区域组/安全组/技术组/标准组/运营组/总体组),`project_group` 11 个项目组
   (关键技术攻关组/算力网络组/国家工程办…);`primary_category` 11 个一级分类,
   首行「改革与治理 67.5%」,与 `category`(里程碑自己的类别,首行「国家任务 58.9%」)不同轴。
   `reporter_id` / `owner_id` 在本库里恰好都有 47 个取值,但**仍是两列**,不可互相代答。

### 3.0.8 年度目标统计接线:两个"必须反着来"的口径(2026-09-10 第 27 轮)

`weekly_year_goal_stats`(6 scope)接线完成。两个 scope 群的口径正好相反,规则写死在模板层:

1. **缺口类口径永远按正式任务算**。`coverage` / `missing` / `missing_by_group` 量的是
   「**正式任务**里有多少没设目标」—— 把分母放宽到已删除、未发布的任务上,这个缺口就不成立了。
   所以 `include_informal=True` 只对 `by_year` / `span` 这类纯计数生效;缺口类即使传了它,
   SQL 里仍然是 `workflow_status = 'published'`,并在口径句里写明「对它无效」。
   真库上两者的差是量化的:`by_year` 加闸门 **313** 条目标,放开是 **387** 条,差 **74** 条挂非正式任务。
2. **`coverage` 必须用 EXISTS,不能用 JOIN**。没有目标行的任务**恰好就是要数的缺口**,
   INNER JOIN 会把它们整行丢掉 —— 于是 `missing_goal` 永远是 0、覆盖率永远是 100%
   (演示实现把这个陷阱记作 `missing_goal_as_zero`)。真库上 2026 年:128 项里 117 有 / 11 缺 = 91.4%;
   用 JOIN 会答成「128 项全都有目标」。

同一轮里另外固化了四条:

3. **`span` 的 `year` 在演示实现里是被静默丢掉的**(它的 SQL 只带任务闸门与看板)。
   docstring 只说「by_year / span 不必给 year」,没说给了会怎样。正式源在这里按年度过滤就会返回
   **范围更小的答案** —— 所以 `scope=span` 带 `year` 一律回落演示路径。这是「不得静默缩小问题范围」
   那条纪律的一个具体落点:遇到参考实现自己都说不清的可选参数,回落比猜更安全。
4. **`multi_year` 的 `HAVING` 不能引用输出列别名**。PG 只在 `ORDER BY` 与 `GROUP BY` 允许别名,
   `HAVING` 里必须把 CASE 表达式写全 —— 照抄演示实现的 `HAVING goal_year_1 IS NOT NULL`
   会报 `column "goal_year_1" does not exist`。模板里两个 CASE 各写了两遍,单测钉住了这一点。
5. **`span` 的年份串要 `string_agg(g.year::text, ...)`**:`year` 是整数,
   不转文本会报 `function string_agg(integer, unknown) does not exist`。
6. **`board=` 只认 `tech` / `group` 两个码**:演示实现的 `resolve_board` 还接受看板**名字**
   (「集团看板」)。名字的解析交给演示路径,正式源不猜 —— 猜错会答成另一个看板。

### 3.0.9 schema 与 freshness:两个"没有 columns"的复合信封(2026-09-10 第 28 轮)

`weekly_schema`(能力发现)与 `weekly_freshness`(**数据快照日**)接线完成。这两个工具
的返回值都**不是一张表**:前者回 boards / categories / table_columns / field_notes 四块,
后者回 看板行 + `overall` + `published_progress` + `tech_import`。三条如实记录:

1. **两者都没有 `columns`**,所以 `_formal.envelope` 那条路用不上 —— 各子查询跑一次再拼。
   对照器也随之补了一条规则:**演示实现返回的信封里没有 `columns` 时按顶层载荷键比对**
   (正式源必须照样给出演示源那几个键,多给允许)。区分"复合信封"与"有一张表的出口"是必要的:
   复合出口的演示实现**也会**发 SQL(`weekly_schema` 的 boards 查询),SQL 别名会解析出
   一组看着像输出列的候选,不区分就会报成假的不一致。
2. **`weekly_freshness` 与 `weekly_freshness_distribution` 是两件事**:前者答"数据更新到
   什么时候了",后者答"各任务多久没报进展了"。E6-01 就是问前者却拿到了后者。真库实测四组数:
   - 各看板 `latest_progress`(= `task.latest_progress_time`,**含未发布行**):技术组 08-09 / 落后 6 天,集团组 08-14 / 1 天;
   - `overall`(全库那一对):08-14 / 1 天 / 128 项正式任务;
   - `published_progress`(**正式口径**):技术组 **07-31** / 15 天 —— 与 08-09 差 9 天,这个差就是发布滞后;集团组取自 `task_group_progress_history` 得 08-14;
   - `tech_import`:跑完(`status = 1`)的批次是 **07-31**,而最新批次是 08-15(**仍在处理中**)——技术组的正式数据其实卡在导入批次上。

   两个实现细节:分流条件是 **`b.code = 'group'`** 而不是演示实现里写死的 `b.id = 2`
   (正式库的看板 id 不保证与演示库一致);天数按 **两个日期相减** 得出,不用 `date_part`
   (`date - timestamp` 是 interval,会少一天)。
3. **可选表未授权时保留键、值给 null**。`tech_import` 的三个日期在未授权时是 `null`,
   键**不删** —— 删掉这个键,调用方就分不清"本来没有批次表"与"批次日期查不到"。
   这与 `weekly_health` 用 NULL 表示"表不在授权内"是同一条口径(打码而不是删列)。
4. **字段字典只列契约内的 12 张表**,不按 `public` 全 schema 列举(正式库 public 下还有别的表,
   列进来是噪音,还会把结果顶到行数上限之外);并**剔除 `storage_path` / `payload`**,
   这样"这个清单即可对外引用的全部字段"那句话才成立。PG 没有 `COLUMN_COMMENT`,
   列注释要经 `pg_class` 的 oid 去 `pg_description` 取(`objsubid` 就是 `ordinal_position`)。

> 对照器本轮还修了一个桩的错:`fake_fetch` 此前返回**空行** `[{}]`,于是手工拼信封的演示
> 实现(`weekly_schema` 的 `by_table` 循环读 `row["table_name"]`)抛 `KeyError`、被 `_guard`
> 包成 `internal_error`,对照器只看到"取不到演示列"。现在按解析出的列名给键 ——
> 桩造出来的行就该像真行。

### 3.1.0 字段完整度与单任务进展历史(2026-09-10 第 29 轮)

`weekly_field_completeness` 与 `weekly_progress_history` 接线完成。两条新踩到的坑:

1. **SQL 文本里的字面 `%` 必须写成 `%%`** —— 第 8 条铁律里已经写过(psycopg 会对整条
   SQL 做占位符解析,`可用性(\d+)%` 会被当成参数标记)。这一轮它以**另一种面孔**又出现:
   `LIKE base.name || '（%期）'` 里的 `%` 后面紧跟的是**多字节汉字**,psycopg 报的不是
   `only '%s', '%b', '%t' are allowed as placeholders`,而是
   `'utf-8' codec can't decode byte 0xe6 in position 1: unexpected end of data` ——
   看着像编码问题,其实是同一个坑。**纯单测与 pglast 语法校验都照不出来**,只有连真库跑才算数。
2. **同名系列的正则作为参数传,不拼进 SQL 文本**。`（\d+期）$` 含反斜杠与全角括号,
   拼进去要处理两层转义(还要再躲一次上面那个 `%` 坑),作为绑定参数传就都没了。

三条口径上的加固:

- **两个分母各自的数都要写进口径**。明细表字段的"填写率"有两个都对的分母:过闸口径是
  **128 项正式任务**(R-08 把无明细行的任务算成缺项),裸表口径是 **task_group_detail 的 55 行**。
  只说"两个口径不同"、不给数,调用方还是不知道该拿哪个当分母,于是问字段质量的拿 128、
  问业务结论的拿 55,两边都答偏。
- **区分度信号按裸表那一档判**。`implementation_measure` 裸表 55 行**全部非空**、填写率 100%,
  但**只有 1 个不同的值**(55 行同一句话复制)。过闸后行数减到 46,如果按过闸那一档判就会
  漏掉这个信号 —— 区分度是表本身的属性,不该被闸门冲淡。信号文案同时带免责口径
  (「规则校验信号,不构成对项目或人员的绩效判断」)。
- **`published_only` 开关与"空结果"的含义**。任务 7 已发布 **0** 期、含未发布 **1** 期 ——
  `row_count = 0` 不等于"没报过进展",口径里把这两件事分开写。

### 3.1.1 导入批次核对与任务生命周期(2026-09-10 第 30 轮)

`weekly_import_audit` 与 `weekly_task_lifecycle` 接线完成。四条口径:

1. **"最近一批跑完的"是**两个**条件**。最近按 `data_date`,跑完按 `status = 1`。只按日期取头一条
   会拿到第 **20** 批 —— 它 `status = 0`、一条进展都没落库,"影响了哪些任务"根本无从答起。
   真库上跑完的是第 **19** 批,影响 **17** 条任务。**选批与列任务必须一次做完**:分两次调用,
   模型会在中间那步就把批次挑错。
2. **声明与实际是三个口径,不是两个**。`declared_tasks` 在批次行上(自己声明的),
   `actual_tasks` 是对 `task_progress.import_id` 反查的**去重任务数**,`actual_rows` 是**进展行数**。
   拿行数去比声明会得出反向结论 —— 所以三个数都要给,`mismatched_batches` 由服务端算(真库 **20/20** 批全对不上)。
   配套:`LEFT JOIN` 不能换成 `JOIN`,否则最极端的那批(第 20 批声明 43、实落 0)会整行消失。
3. **孤儿与"没走导入"必须分开**。孤儿 = `import_id` 非空但批次表里查不到(NOT EXISTS);
   `import_id IS NULL` 是手工填报,**不算孤儿**(真库 120 行)。混在一起会把手工填报的进展全报成孤儿。
4. **建立/发布是另一个钟**。`task.created_at` / `published_at` 与"报进展"的钟不是一回事;
   到发布天数按**两个日期相减**(真库均值 30.3 天、最长 60 天,只统计 `published_at` 非空的)。
   `by=` 分组档是**建单档**:任务表没有"完成时间"列,所以这是"按建单档看当前状态",
   **不是**"那一年完成的任务数"——跨档完成的任务仍记在建单档。口径里必须写明,
   否则会被读成按完成年份统计(各档 `currently_finished` 相加 = 全库已完成 31,是一条自校验)。

### 3.1.2 单任务详情:两条"挂错地方就等于没有"的口径(2026-09-10 第 31 轮)

`weekly_task_detail` 接线完成(复合信封:`task` 22 列 + `group_detail` + `recent_progress` 最近 3 期 + `year_goals`)。

1. **R-12 必须无条件在场**。`completion_time 为展示文本,不可做日期运算` 这句话如果挂在
   `task_group_detail` 那个子查询的 caliber 上,就只对集团看板任务生效 —— 而**技术组任务
   永远看不到它**,规则本来就是为它们立的。所以它拼在顶层 `caliber` 里。
2. **两套负责人列打架时要点名**。集团看板任务的牵头人/负责人有**两套列**:`task` 行上的单值
   `lead_owner_name` / `project_owner_name`,与明细表里的多值 `lead_owner_names` /
   `project_owner_names`。真库上任务 101 的两边分别是「陈志远 / 范修远」与
   「刘海涛,韩雪峰 / 金鹏程」,**46 条集团任务两列的值全都不一致** —— 谁在上面谁就被当成答案
   (参考实现里 R8-02 就是照 task 行答了「陈志远」)。有明细行就直接判给多值列,并把不一致本身
   写进口径,不让模型猜。
3. **`recent_progress` 为空 ≠ 没报过进展**。集团看板的进展不在 `task_progress`(那张表 0 行全属
   技术看板),当期成效在 `group_detail.progress_effect` 里。任务 101 就是 1 行明细 + 0 期进展;
   不点明这点,模型会在 `progress_history` / `milestone_stats` 之间来回试(参考实现里 Q1-02 的
   6 轮 13 次调用就是这么耗掉的)。
4. **纯数字 token 只当 id**。名字兜底的 LIKE 会把一个**错的任务**悄悄顶上来:参考实现里
   `task="2"` 曾落到 LIKE 分支、把另一条名字含 "2" 的任务当成了任务 2。名字查找则是
   精确优先、再取**最短**的子串匹配(最短的名字是对用户输入最少加戏的读法)。

### 3.1.3 审批时长:pending 这一档必须**不带**发布闸门(2026-09-10 第 32 轮)

`weekly_approval_turnaround`(4 scope)接线完成。它与其余所有出口**正好相反**:

1. **`pending` 刻意不套发布闸门**。卡在审批里的提交单按定义就还没发布,套上 R-01 会得到一个
   **空的积压队列** —— 那不是"没有积压",是把问题问没了。只保留 `t.is_deleted = 0`(软删任务下的
   单不该出现在队列里)。其余三档(已完成轮次)照常带正式任务门。真库上积压榜首已等 **583 天**。
2. **最慢那档是并列**,不是唯一第一名。实测 **59 天有两轮**(任务 **76** 与 **143**);只回榜单时
   模型会当作两个独立答案,所以 `top_tie_count` 提到顶层,并在口径里直接写明是哪两条。
   真库汇总:已完成轮次 **400**、均值 **14.7 天**、最长 **59 天**;按看板 技术组 257/14.5 +
   集团组 143/14.9 = 400(一条自校验)。
3. **审批耗时按两个日期相减**(`(completed_at)::timestamp::date - (submitted_at)::timestamp::date`),
   与滞报天数同一条铁律:写成 timestamp 相减得到的是 interval,`date_part` 会少一天。
4. **积压天数锚数据基准日**,不用系统当前时间(`pending_days` = 基准日 − 提交日)。

### 3.1.4 任务聚合:三个轴各有一处"反着来"(2026-09-10 第 33 轮)

`weekly_aggregate`(9 个分组轴)接线完成。三条口径都不能"顺手统一":

1. **`workflow_status` 是唯一不加发布闸门的分组**。问的就是审批流转状态分布,把 `published`
   当前置条件会只剩一档 128,其余**六档(未发布的 22 条)全部消失**。真库七档:published 128 +
   pending_audit 7 + pending_leader 5 + pending_fill 3 + rejected 3 + signing 3 + cancelled 1。
2. **`category` 的看板过滤要同时落在分类树上**。只过滤计数时行清单仍是全部 47 个分类,
   另一看板的 19 个只是变成 `cnt = 0`,与"本看板确实没有任务的分类"长得一模一样 ——
   问「技术组下面有哪些分类」会答出 47 条(真值 **28** = 7 个一级 + 21 个二级,集团组 19 = 5 + 14)。
   所以过滤既进 `LEFT JOIN ... ON`(任务侧)也进 `WHERE c.board_id`(分类树侧),参数要给两遍。
   同时 `cnt = 0` 的**空分类要保留**(R-02:闸门挂 ON 上)。
3. **`order_by=finish_rate` 只对 `primary_category` / `project_group` 有意义**。其余轴上演示实现
   也是忽略它的,正式源同样忽略 —— 两边行为一致(这与 `span`+`year` 那种"正式源会缩小范围"的
   情形不同,不需要回落)。

另外两条数值纪律:

- **`project_group` 的占比小数位取 2 而不是 1**:累计占比要跟阈值比大小。真库上**前 4 组累计
  49.22% 仍未过半,第 5 组才到 58.59%** —— 58.59 舍成 58.6 再跟 55% 比会串档,把 49.22 舍成
  49.2 或多算一组都会把结论答反。按完成率定序时**不返回 `cum_pct`**(累计只在按任务数定序时单调,
  换个序就不单调了,留着是假信号)。
- **`top` 是硬切,不是截断**:截断落在 SQL 里,同时服务端先数出分组总数,把"硬切前 N 组
  (共 M 组)、边界外与末位并列的分组不属于本题答案,不要补列"写进口径 —— 模型看到 4 行就答 4 行,
  不会因为"还有并列的"而自己补成 11 行。对照:`weekly_rank` 的并列语义是另一套(见 3.0.3)。

真库另外两处自校验:`status` 四档 14/78/31/5 = 128;`name_series` 64 个家族、**33 个多期家族**、
涉及 **97** 条任务(这三个数由服务端算,不让模型自己数 `cnt > 1` 的行)。

### 3.1.5 集团板统计与收官:31/31 接线完成(2026-09-10 第 34 轮)

`weekly_group_stats`(14 个 scope)接线完成后,**31 个 MCP 工具全部走正式源**(逐名核对:无遗漏、
无多余)。这一轮最值钱的是**又一处"对照器在撒谎"**,以及一条已经出现三次的铁律:

1. **`%` 必须写两遍 —— 第三次了**。前两次是 `可用性(\d+)%`(报 `only '%s', '%b', '%t' are allowed`)
   与 `LIKE '(N期)'`(`%` 后面跟多字节汉字,报 utf-8 解码错)。这次是 `LIKE '%,%'`(判多值负责人):
   字面 `%` 没写双份,psycopg 直接把它当占位符。**同一条规则三种面孔**,
   而且单测与 pglast 都照不出来 —— 只有连真库跑才算数。
2. **对照器的解析器又一次"报假警"**。`column_parity` 把 `weekly_group_stats(scope=separators)`
   报成「列对不上」,而正式源的列 `separator_kind / n` 一直是对的 —— 原因是解析 SELECT 列表时
   **没有跳过单引号字符串**:那段的 `CASE ... LIKE '%%,%%'` 里有一个**字面逗号**,被当成了列分隔符,
   把表达式切成三截。修法是把「词边界找 FROM / 只认深度 0 / 跳过单引号」三件事收进同一个扫描器
   (单引号里成对的 `''` 是转义的一个引号,要成对跳过),并配了不连库的
   `parity_parser_check.py` 钉住这五种写法。**这是本项目第二次"先怀疑对照器"** ——
   第 3.0.6 节那次也是。

收官时的口径要点(真库实测):

- **完成时间是展示文本**(R-12):46 条里只有 6 条标准日期 + 6 条季度能归一化,其余 **34 条判不了**。
  它们**不是"没超期"**,是判不了 —— 混起来会把"无法判断"说成"都没超期"。超期档只露出任务 123
  (2026Q2 → 2026-06-30,超 46 天);**只按标准日期写法看会一条都查不到**,季度那几条必须算。
  分档的**判别顺序即优先级**:'2026年6月底' 同时命中「含底」与「中文年月」,先判到哪档就算哪档。
- **多值负责人按元素切**:两种分隔符混用(顿号 2 条 / 逗号 26 条 / 单人 18 条),`LIKE '%名字%'`
  会在不同人之间碰撞(短名是长名的子串),所以切数组再判等。
- **零附件的任务必须留住**:46 条里 **18** 条没有附件,那通常正是问句要数的。分布档的真值是
  0/1/2/3 = 18/**17/3/5** —— 拿 8 行清单手数会得出 21/4/4,所以分布必须服务端算,并在清单档的
  口径里**指路**到 `scope=attachment_distribution`(清单被 `top` 截断时)。
- **`effect_consistency` 的 `same` 按参考查询给 1/0**(不是布尔):口径句里写的就是
  "same = 1 一致、0 不一致",列的形状跟参考走。

## 5. 能力边界(未授权表时)
- `task_attachment` 只读元数据:问答只能答“存在附件《文件名》”,文件体在
  O2OA/对象存储,SQL 读不到;
- 4 张可选表(`task_workflow_action` / `task_group_progress_history` /
  `task_attachment` / `task_progress_import`)未授权时,“审批过程意见”“集团板
  历史版本”“附件清单”“进展来源文件”类问题显式不可答(工具口径提示),
  不得用已发布轮次以外数据代替。

## 6. 硬约束速查(每条 SQL 都经过 `_admission`)

1. `t.is_deleted = 0 AND t.workflow_status = 'published'` 是唯一问答准入;
   `task.status` 不作发布开关(仅业务状态参考);
2. 进展只取 `is_published = 1`(同任务 `version_no` 最大);问历史版本须任务已
   发布且版本 `status = 3`;
3. 过程信息只取 submission `status = 'published'` 轮;
4. 分类用 `task_category.parent_id` 拼路径;集团板只在 `board.code='group'`
   且用 `task_group_detail`;
5. 年度目标必带显式 `year`;
6. 软删过滤;空长文本答“未填写”;时间输出经 `normalize_ts_sql`(先 `::timestamp`
   再 `to_char`,文本列与时间戳列同一写法),日期窗口比较经 `parse_ts_sql`;
7. **相对时间一律锚数据基准日**(`_formal.as_of()` / `GUOSHU_AS_OF`),任何 SQL 里
   不得出现 `now()`/`current_date` —— 滞报天数、滞后清单、相对窗口都按基准日算;
8. **天数 = 两个日期相减**(`(a)::date - (b)::date`),不能写成 `date - timestamp`
   (后者是 interval,`date_part('day')` 会少一天,与 DATEDIFF 语义不符);
9. **每个出口的 `columns` 必须与参考实现逐列同名**(含分支里的列):模型的读法建立在
   列名上,数字对而列名不同,等于换了一张表;总数、并列数这类自检数放**顶层键**,
   不放行内列(演示源也是这么放的);
10. **分组/占比一律服务端算**:分母与占比与条数同排返回,并给出合计行;排序端要跟着
    问句走(滞后按 `stale_pct`、活跃按 `active_pct`),排错端等于把末位当第一。

## 7. 校验方式(改模板后必跑)

```bash
# 规则与模板单测(不连库)
uv run pytest examples/guoshu-weekly-workspace/tests/test_o2oa_pg.py -o addopts='' -q
# 静态检查
uv run ruff check examples/guoshu-weekly-workspace
uv run ty check examples/guoshu-weekly-workspace
# PG 语法校验(pglast,离线确认生成的 SQL 是合法 PostgreSQL,且参数与占位符一一对应)
uv run --no-project --with pglast python <核对目录>/check_pg_syntax.py
```

**真库语义与口径验收**(需要一台可写的 PostgreSQL;核对目录里的 harness):

```bash
# 1) 建实例(zonky 预编译二进制,普通用户即可,Unix socket)
bash <核对目录>/start_pg_on_h100.sh
# 2) 载入演示库数据 + 语义断言(准入 / 反例注入 / 最新版本 / 历史版本 / 分类路径 / 附件边界)
python <核对目录>/load_and_verify_pg.py <weekly_mock.sqlite> <mock-mcp 目录>
# 3) 批次 2 口径验收:复现上面那批契约数字(37 项断言)
python <核对目录>/verify_numbers_v2.py
# 4) 端到端验收:以 TASK_BOARD_DATA_SOURCE=o2oa 加载 mock 服务,逐个调用已接线工具(130 项断言)
python <核对目录>/verify_end_to_end.py
# 5) 索引、执行计划与规模计时(放大到十万行,验证无 N+1;结束时会自己清理合成行)
python <核对目录>/bench_pg.py
# 6) 若上一步被中断,用这个把实例恢复成演示库基线(口径验收依赖 1068 / 404 这两个基线)
python <核对目录>/reset_pg.py
# 7) 列集合对照:演示路径 vs 正式源,逐个出口比对 columns(改任何出口后必跑)
python <核对目录>/column_parity.py
```

> 注意:仓库要求 Python ≥ 3.14;3.13 的解析器不接受本仓既有的 `except A, B:` 写法,
> harness 也必须用 3.14 运行。

> 注意:核对脚本**只从 `mock-mcp/` 导入模板**。本批踩过:几个脚本从前一层的平铺目录
> `~/chatbi_check/*.py` 导入,那一份是早期拷贝,于是 bench 与口径验收静默跑了**旧模板**,
> 报告数字看着正常却全是旧的(现象:新加的用例一条都不出现、报告文件时间戳不动)。
> 现在四个脚本都指向 `chatbi_check/mock-mcp`,平铺副本已从 H100 上删除。


