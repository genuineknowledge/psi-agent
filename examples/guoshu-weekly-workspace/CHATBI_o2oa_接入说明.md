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
| `mock-mcp/_admission.py` | 硬约束与值域:发布准入、进展正式版、submission published 轮、枚举校验、year 显式、时间归一化片段 |
| `mock-mcp/_o2oa_templates.py` | 首批 PG 查询模板:①已发布任务清单(5.1)②最新正式进展(5.2)③任务详情+年度目标+里程碑+集团扩展(5.3) |
| `tests/test_o2oa_pg.py` | 上述规则的纯单元测试(不连库) |

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
6. 软删过滤;空长文本答“未填写”;时间输出经 `normalize_ts_sql` 归一化。
