# 国数周报取数服务(MCP)—— 运行说明

版本 `20260911c` · 镜像 `guoshu-weekly-mcp:20260911c`
本页是交付件的一部分,请与镜像 tar 一起归档。

---

## 1. 这是什么

一个 **MCP 服务**(HTTP / streamable-http),对外提供 **31 个取数工具**,数据源是
国数 O2OA 的 PostgreSQL(库 `o2oa`,只读)。它只负责**取数与口径**,不含界面、
不含 Agent —— 由贵方自己的应用/Agent 作为客户端调用。

```
贵方应用 / Agent ──MCP(HTTP)──▶ 本容器:18900 ──只读 SQL──▶ o2oa 数据库
```

## 2. 起容器

```bash
docker load -i guoshu-weekly-mcp-20260911c.tar
docker run -d --name guoshu-weekly-mcp \
  -p 18900:18900 \
  --env-file o2oa.env \
  guoshu-weekly-mcp:20260911c
```

`o2oa.env`(权限 600,**不要**提交到代码库):

```ini
TASK_BOARD_DATA_SOURCE=o2oa
PGHOST=<国数 o2oa 数据库主机>
PGPORT=5432
PGDATABASE=o2oa
PGUSER=task_board_readonly
PGPASSWORD=<只读账号口令>
PGSCHEMA=public

# 端点鉴权(必填,见第 3 节)
GUOSHU_WEEKLY_MOCK_TOKEN=<常规通道 token>
GUOSHU_WEEKLY_MOCK_ADMIN_TOKEN=<敏感字段通道 token>
```

> 镜像内已内置 `TASK_BOARD_DATA_SOURCE=o2oa`、`PGPORT=5432`、`PGSCHEMA=public`,
> 但**显式写全**更稳妥(便于排查)。

## 3. 鉴权(必读)

服务在 **HTTP 层**做 bearer token 校验,未通过一律 `HTTP 401`。两个 token 分两级:

| 环境变量 | 权限 |
|---|---|
| `GUOSHU_WEEKLY_MOCK_ADMIN_TOKEN` | 全部工具 + **敏感字段明文**(审批意见等) |
| `GUOSHU_WEEKLY_MOCK_TOKEN` | 全部工具,但敏感字段返回 `[按权限不展示]` |

调用方式:

```http
POST /mcp
Authorization: Bearer <token>
```

三条硬规则:

1. **两个 token 必须都配**。正式源模式下缺任何一个,容器**拒绝启动**并打印缺哪一个 ——
   这是刻意的:缺常规 token 等于没有鉴权,缺敏感 token 则敏感字段无人可读。
2. **两个 token 不能相同**。相同则常规通道也能解锁敏感字段,两级分级形同虚设;
   这种情况容器同样拒绝启动。
3. **token 只在容器环境里**。请用 Secret / 环境注入,不要写进镜像、代码或日志。

## 4. 端点

| 路径 | 方法 | 鉴权 | 用途 |
|---|---|---|---|
| `/mcp` | POST | **需要 bearer token** | MCP 服务端点(工具调用都走这里) |
| `/healthz` | GET | 不需要 | 探活,返回 `{"ok":true,...}` |

- 客户端 URL 配置成 `http://<容器地址>:18900/mcp`。**路径必须是 `/mcp`**。
- 注意:部分 MCP 客户端库对**非 `127.0.0.1` 的地址强制要求 HTTPS**。若贵方客户端有此校验,
  请在前面加 TLS 终止(反向代理),用 `https://<域名>/mcp`。
- **本服务不要直接暴露到浏览器或公网**:它是给后端调的,请置于内网 / 反代之后。

## 5. 数据库侧需要开的授权

共 **12 张表**:必需 **8** 张 + 可选 **4** 张。

**必需表(8 张,全授 SELECT)**:

```
task  task_board  task_category  task_progress
task_milestone  task_year_goal  task_group_detail  task_workflow_submission
```

**可选表(4 张)**:`task_attachment`、`task_group_progress_history`、
`task_workflow_action`、`task_progress_import`。

另需(`GRANT CONNECT` **必需**):

```sql
GRANT CONNECT ON DATABASE o2oa TO <只读账号>;
```

> 该库的 `datacl` 被显式改过,PUBLIC 没有 CONNECT。不授这一条,连库都连不上,
> 报 `permission denied for database "o2oa"`。
> 账号本身**只需 SELECT**;服务侧连接已强制 `default_transaction_read_only=on`。

**可选表必须同时做两件事**,少一件那部分能力就用不了:

1. 数据库侧授 `SELECT`;
2. 在 `o2oa.env` 里加一行(把这 4 张表登记为"已授权"):

```ini
TASK_BOARD_GRANTED_OPTIONAL_TABLES=task_attachment,task_group_progress_history,task_workflow_action,task_progress_import
```

第 2 步不是数据库设置,而是**服务侧的授权登记** —— 服务用它判断"这些表我这次能不能读"。
不登记时,相关工具返回可读的报错(不是崩溃、不影响其它工具):

```
table_not_granted: task_attachment 未在本次只读授权范围内,相关问题无法回答
(如需请联系数据侧补开 SELECT 授权)
```

**不登记时的准确影响**(31 个工具里 **5 个整档不可用 + 1 个部分可用**,其余 25 个正常):

| 受影响工具 | 失去的能力 |
|---|---|
| `weekly_workflow_query` | 整档:审批动作、审批环节 |
| `weekly_attachment_query` | 整档:附件清单 |
| `weekly_attachment_stats` | 整档:附件统计 |
| `weekly_group_history` | 整档:集团板历史版本 |
| `weekly_import_audit` | 整档:导入批次核对 |
| `weekly_task_ranking` | **部分**:`metric=attachments` 与默认档不可用;`progress` / `milestones` / `submissions` 三档实测仍出数 |

两个**不受影响**的相邻工具(实测确认,别误判成也坏了):

* `weekly_approval_turnaround`(审批时长)—— summary 档走提交单与动作的通用路径,仍能出数;
* `weekly_submission_query`(提交单)—— 只依赖必需表。

**建议:4 张可选表都开**。否则"附件""审批意见""集团板历史""导入来源"这四类问题,
工具会明确拒答 —— 那正好是周报里常问的几类。

## 6. 自验(载入镜像后建议先跑这一条)

```bash
curl -s http://127.0.0.1:18900/healthz
# 期望:{"ok":true,"service":"guoshu-weekly-mcp"}

curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:18900/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
# 期望:401(没带 token 就该被拒)
```

启动日志里应能看到这四行:

```
auth: 已启用(两级 bearer token);GUOSHU_WEEKLY_MOCK_TOKEN=常规通道, GUOSHU_WEEKLY_MOCK_ADMIN_TOKEN=敏感字段通道
mock weekly MCP on http://0.0.0.0:18900/mcp
health: http://0.0.0.0:18900/healthz
store: 正式只读源(o2oa/PG)
```

> 日志里另有一行 `mock store absent (expected in formal mode): ... mysql ...`。那是
> **正常**的:镜像内保留了演示数据源的代码路径,正式源模式下它会探测一下本机 MySQL
> 并失败、只记一行日志后继续。**不需要装 MySQL**。

## 7. 错误码与处置

工具返回统一信封(JSON):

```json
{"ok": true, "caliber": "…口径说明…", "snapshot_date": "2026-09-11",
 "source_tables": ["task"], "columns": ["…"], "rows": ["…"],
 "row_count": 48, "has_more": false}
```

失败时 `ok=false` 并带 `error.code`:

| code | 含义 | 处置 |
|---|---|---|
| `unauthorized` | HTTP 401,token 缺失或无效 | 检查 `Authorization: Bearer <token>` 与配置的两个 token |
| `formal_source_unreachable` | 连不上 o2oa 数据库 | 查网络、数据库、只读账号与口令。**不是参数问题** |
| `table_not_granted` | 某张可选表未授权 | 见第 5 节(授权 + 登记变量两件都要做) |
| `not_migrated` | 该参数组合未实现 | 换参数。当前仅剩 2 档:`weekly_submission_query` 的「聚合口径 + 明细筛选」、`weekly_freshness_distribution` 的「`task=` × 天数窗」 |
| `invalid_argument` | 参数本身不合法 | 按 `error.message` 里给出的取值域改参数 |
| `task_not_found` / `task_not_formal` | 任务不存在 / 不属正式任务 | 换任务;消息里会说明它属于哪种情况 |

**调用方请注意**:`caliber` 字段是口径说明(时间窗怎么算、0 行是什么意思、哪一列不能做
日期运算)。请把它一并放进 Agent 上下文 —— 丢掉它,0 行会被答成"没有任务"。

## 8. 运维要点

| 项 | 说明 |
|---|---|
| 监听 | 容器内 `0.0.0.0:18900`,镜像已 `EXPOSE 18900` |
| 运行身份 | 非 root(`uid=10001`) |
| 探活 | `GET /healthz`(不查库 —— 库不可用不会让容器被反复重启) |
| 资源 | 1 vCPU / 2 GB 起;实测常驻内存远低于此 |
| 状态 | **无状态**。没有本地库、没有会话文件,重启不丢数据;可直接多副本 |
| 数据库只读 | 连接强制 `default_transaction_read_only=on`,并下发 `statement_timeout=30s`;账号本身只需 SELECT |
| 并发 | 每个工具调用独立建一次连接;高频调用建议在前面加连接/请求限流 |
| 升级 | 换镜像 tag 重启即可;数据库侧无迁移 |

## 9. 已知边界(如实说明)

1. **未实现 2 档参数组合**(见第 7 节 `not_migrated`),会明确报错,不会答错。
2. **镜像内的 `/app/README.md` 是开发期的旧文档,请勿参照**。它写的是演示形态
   (要装 MySQL、导入 mock 数据、"415/415 passed"),与本交付**无关** —— 那是我们内部
   联调时用的说明,镜像里保留了代码路径,正式源模式下用不到。**以本文件为准。**
3. **数据质量事实**:`task_progress.progress_date` 有相当比例为空(实测 197 行中 180 行为空,
   其中已发布 44 行)。影响:按时间分组(`weekly_progress_range` 的 `by=month/quarter`)时
   这些行进不了任何时间档,工具会在信封里回 `unbucketed_rows` 并说明差额 ——
   **建议向数据侧反馈补齐**,而不是在应用侧解释这个差额。
4. **两级 token 不是"按登录身份出数"**。若后续要做"领导与员工各自的口径与可见域"
   (按部门/组过滤行、按角色过滤字段),需要再加一层:身份传递 + 行级可见域 + 逐出口过滤。
   当前这版是那层的地基(至少有"两类调用方"的区分与默认拒绝),不是替代。
   若贵方要把它嵌进内网站点,建议下一步做**身份下推**:由站点后端在校验登录态后注入
   身份头(用户 / 部门 / 角色),服务按该身份收窄可见域 —— 需要在服务侧改代码,不是配置能解决的。
5. **本节未列的其它已知项**:见第 7 节的错误码处置与第 8 节的运维要点。

## 10. 版本与校验

| 项 | 值 |
|---|---|
| 镜像 tag | `guoshu-weekly-mcp:20260911c` |
| 导出文件 | `guoshu-weekly-mcp-20260911c.tar`(197 MiB) |
| 校验和 | 见同目录 `SHA256SUMS`(镜像 tar / 源码包 / 本文件三者的 SHA256) |
| 导出日期 | 2026-09-11 |
| 基线 | `python:3.14-slim`(Debian trixie),运行身份 `uid=10001` 非 root |
| 镜像内文档 | `/app/README.md` = 本文件(镜像里放的就是这份运行说明) |

> **为什么这里不写镜像 ID 与 tar 的 SHA256**:本文件本身被打包进镜像,而镜像 ID 与 tar
> 的摘要又会随本文件的内容变化 —— 写进去就成了"文档包含自己的摘要"这种追不上的循环。
> 所以**校验和一律以同目录 `SHA256SUMS` 为准**,镜像用 **tag** 标识
> (`docker images` 里认 `guoshu-weekly-mcp:20260911c`)。

源码包内容:`Dockerfile`、`RUNBOOK.md`(与本文件同源)、`CHATBI_o2oa_接入说明.md`(完整
迁移与口径记录)、`mock-mcp/`(全部代码)。按包内 `Dockerfile` 可重建出同一套服务。
> 注:构建时 `Dockerfile` 里的文件名必须是 **ASCII**(`RUNBOOK.md`)。实测 BuildKit 不接受
> 构建上下文里含非 ASCII 的文件名,会以 `followpaths ... non-printable ASCII` 中止构建。

**验收记录**(全部在容器内、连国数 o2oa 真库实跑,同一天同一镜像):

| 验收 | 结果 |
|---|---|
| 冒烟(31 个工具各调一次) | 31 / 31 |
| 验收(列集合 + 无数据分支) | 34 / 34 |
| 基线交叉核对(每个数字对直连 SQL) | 62 / 62 |
| 工具级(经工具出口) | 34 / 34 |
| 协议级(经 MCP 协议取真库) | 10 / 10 |
| **鉴权专项**(401 / 两级 / 探活) | **10 / 10** |
| 进度轴与年度目标专项 | 32 / 32 |
| 单测(不连库) | 320 项 |
| 静态检查 | `ruff` / `ty` 通过;SQL 语法 217 条(离线 pglast) |

**交付件自检**(导出后重新载入并逐项复核,均通过):

| 检查 | 结果 |
|---|---|
| 镜像内 `/app/README.md` 是本运行说明(非 demo 说明) | ✅ |
| 镜像 env 无凭据;源码无内部地址与口令字面量 | ✅ |
| 不含 agent 包 / gateway / 前端 | ✅ |
| 运行身份非 root(`uid=10001`) | ✅ |
| 缺 token 时拒绝启动(`Exited (1)` + 明确缺哪个变量) | ✅ |
| 带 token 时 `/healthz` 200、`/mcp` 无 token 401 | ✅ |

---

## 11. 能力边界说明(答不了什么,以及答不了时它会怎么回应)

这一节是**给集成方和业务方看的**:系统能答的范围之外,领导会问到很多别的事。
先知道边界,才不会把"没有这类数据"当成"系统坏了"。

> **先说清一件事:本节描述的"边界应答"是 Agent 层的行为,镜像里没有提示词。**
> 本镜像只含**取数服务**(31 个工具)。上面那些"先说做不到、再给能答的部分、
> 部分是推断时划界"的话术,由**我方 Agent 的提示词**保证(见源码包
> `systems/system.py`)。若贵方用**自己的 Agent** 接这个服务,这层话术需要贵方自己实现;
> 服务侧能保证的是:`caliber` 字段带口径、"答不了"是正常返回(`ok=true`)、
> 越权请求在工具层没有可执行入口。
> 若不希望交付包里带这份提示词,删掉 `systems/` 目录即可 —— 它不进镜像。

### 11.1 五类**系统性不可答**(库里没有这些数据)

| 类别 | 典型问法 | 库里为什么没有 |
|---|---|---|
| **资源与经费** | "投了多少钱""预算执行率""哪个项目超支""缺人缺钱卡在哪" | 无预算额、批复金额、实际支出、合同金额、工时投入字段 |
| **协同与外部** | "OA 里审批走到哪了""合同签了吗""跟财务系统对得上吗""兄弟单位怎么做" | 只有本周报系统的记录,没有跨系统/外部数据 |
| **考核与评价** | "某人表现怎么样""谁最不称职""延期责任在谁" | 无绩效、编制、岗位字段;且属主观评价,不由取数侧裁决 |
| **趋势与预测** | "年底能完成多少""下个月会延期吗""哪个先出问题" | 无标准计划日、剩余工作量、资源投入、历史速度、风险概率 |
| **无历史快照** | "跟去年同期比怎么样" | 全库只有一个快照日,没有去年同期快照 |

### 11.2 答不了时的**标准回应**

系统会做三件事,而不是硬凑一个答案:

1. **先说做不到**,并点名缺什么(例:"库内没有预算额、批复金额、实际支出字段")；
2. **再给能答的部分**(例:"能答的是任务状态分布与缺报情况")；
3. **部分是推断时明确划界**(例:"『烂尾风险』是按这些信号作出的判断,库内没有风险等级
   与概率字段,判断口径由贵方认定")。

`error.code` 上会看到三种:**`not_migrated`**(该参数组合未实现,仅剩 2 档)、
**`formal_source_unreachable`**(数据库连不上)、**`table_not_granted`**(可选表未授权)。
"答不了"本身**不是错误** —— 它是一个正常返回,`ok=true`,正文里说明边界。

### 11.3 **写操作一律拒绝**

系统**只有读取能力**,以下请求会被明确拒绝并说明原因(不会假装执行):
改任务状态、换负责人、删除进展、发通知、导出文件、发邮件、批量打标签、
改数字、编数字、绕过权限取全量、把名单外发。
若后续确实需要写能力,那是**另一个系统**,不是打开一个开关的事。

### 11.4 集成方要处理的两件事

1. **员工视角需接入身份**。当前系统**没有调用方身份**,因此:
   * 员工问"我负责的任务现在什么状态",只能给全局口径,给不了"我的";
   * 审批意见等敏感字段按**共享 token 的级别**决定明码或打码(`[按权限不展示]`),
     做不到"按人授权"。
   要让"领导看全公司、员工只看自己范围"真正成立,需要:身份头下推(由站点后端在校验
   登录态后注入)+ 行级可见域 + 逐出口过滤。**这是需要在服务侧改代码的下一步,不是配置。**
2. **边界话术要原样透传**。系统在正文里写的"库内没有 X 字段""这是推断不是台账"是
   **刻意的**,不要让前端或上游 Agent 把它删掉或改写成结论 —— 删掉之后,领导会把
   推断当成台账数据。

### 11.5 一条数据质量事实(建议反馈给数据侧)

`task_progress.progress_date` 有相当比例为空(实测 197 行中 180 行为空,其中已发布 44 行)。
影响:按时间分组时这些行进不了任何时间档,工具会回 `unbucketed_rows` 并说明差额。
**建议由数据侧补齐**,而不是在应用侧解释这个差额。
