# ChatBI 正式数据接入说明(o2oa / O2OA PostgreSQL)

> 依据:国数方《ChatBI数据权限开通以及表字段说明》(2026-09-08,oa-weekly 核对版)。
> 本说明是 agent 包侧的接入与迁移指南;字段字典、关系图与示例 SQL 以国数方
> 该说明为准,这里只描述 **代码如何接入** 与 **迁移批次**。

## 1. 接入原则

- agent(31 工具契约)与 mock 语义不变;正式源 = 同一契约的 **PostgreSQL 实现**;
- 生产数据源:**O2OA 的 PostgreSQL**(库 `O2OA-DB`,schema `public`),**不要**给
  MySQL `oa_biz` 的同名 task_* 旧空表授权(结构不同、基本为空);
- 只读:连接角色仅授 SELECT;本包连接层强制 read-only 会话(双保险);
- 发布准入等硬约束由服务端固化,见 `mock-mcp/_admission.py`,不得写散在单条查询里。

## 2. 配置

| 环境变量 | 用途 | 默认 |
|---|---|---|
| `TASK_BOARD_DATA_SOURCE` | 数据源选择:`mock`(演示)/`o2oa`(正式) | mock(未设置时) |
| `PGHOST` / `PGPORT` | PG 地址 | 127.0.0.1 / 5432 |
| `PGDATABASE` | 正式库名 | O2OA-DB |
| `PGUSER` / `PGPASSWORD` | 只读账号(chatbi_read) | chatbi_read / 空 |
| `PGSCHEMA` | schema | public |

凭据只经环境/Secret 注入,不落代码与镜像。

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
8. **SQL 文本里的字面 `%` 必须写成 `%%`**:psycopg 会对整条 SQL 做占位符解析,`可用性(\d+)%` 这种正则会被当成参数标记并报 `only '%s', '%b', '%t' are allowed as placeholders`。这是真库验证抓到的运行时错误,纯单测与语法解析都照不出来;
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
| `weekly_freshness_distribution` | ✅ 已接线 | 分档 + 总览**合并进同一个信封**(rows = 分档,另给最新进展/滞后天数/任务总数/分档合计) |
| 其余 28 个工具 | 待迁移 | 调用时 `_formal.dispatch` 返回 `None` → 演示路径,行为不变 |

三条硬规则:

1. **信封与演示源逐字段同构**(`ok` / `caliber` / `snapshot_note` / `snapshot_date` / `source_tables` / `columns` / `rows` / `row_count` / `has_more`),agent 侧无需改动;
2. **未迁移的 scope 或参数一律返回 `None` 回落**,绝不返回一个范围更小的答案(例如新鲜度的 `by=` / `lag_bands=` / `recent_days=` 仍是演示路径);
3. `snapshot_note` 换成正式口径(「国数正式只读源…非演示数据」),`snapshot_date` 用基准日(`GUOSHU_AS_OF` 可固定,便于与演示快照日对齐)。

**端到端验证**(真 PG + 正式源模式,20 项断言全通过):信封 9 字段齐全、`source_tables` 指向正式表、
publish_split 943/123/1066、summary 943/73/12.92、never_reported 55、任务 103 的 V8 冲突(来自集团历史表)、
新鲜度分档 63/44/8/9/4 且合计 = 任务总数、在办「从未报进展」8、漂移 73,未迁移参数全部回落。

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

授权一生效,即可用仓库外的核对脚本一次性产出:表/列/注释/行数、枚举真实分布、
时间字段真实形态、最小抽样,并与本说明的字段字典逐列 diff。


## 4. 31 工具迁移批次(未完成部分)

- **批 1(已提交)**:查询类高频工具的三条核心 SQL 模板 + 规则基础设施;
- **批 2**:`weekly_schema / task_query / task_detail / progress_history /
  progress_coverage / aggregate / rank / freshness* / year_goal*` 等任务/进展/
  年度域工具的 PG 化与参数白名单;
- **批 3**:`milestone* / workflow_query / submission_query / approval_turnaround /
  person_stats / owner_roles / attachment* / import_audit / scale / field_completeness`
  等子表域工具;
- **批 4**:`group_detail* / group_owner* / group_history / group_stats` 集团板专表域;

每批交付:PG SQL 模板 + 参数/口径 + caliber 文案 + 契约测试期望值(正式真值)。
联调账号与样例就绪前,以模板+单测+文档先行,真库验收后回填期望值。

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
   再 `to_char`,文本列与时间戳列同一写法),日期窗口比较经 `parse_ts_sql`。

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
# 3) 批次 2 口径验收:复现上面那批契约数字(12 项断言)
python <核对目录>/verify_numbers_v2.py
# 4) 索引、执行计划与规模计时(放大到十万行,验证无 N+1)
python <核对目录>/bench_pg.py
```

> 注意:仓库要求 Python ≥ 3.14;3.13 的解析器不接受本仓既有的 `except A, B:` 写法,
> harness 也必须用 3.14 运行。


