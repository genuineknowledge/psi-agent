# 公司级定时任务种子（`agents/feishu/schedules/`）

本目录是**公司级定时任务的种子**：一份任务一个目录，每个目录里一个 `TASK.md`。
调度器把 agent 包 `agents/feishu/schedules/*/TASK.md` **幂等 seed 进配置的种子 workspace**
（部署时经 `PSI_SEED_SCHEDULES_WORKSPACE` 指定；**空 = 关闭**）。

seed 的两条硬规则写在 `SchedulerManager._seed_missing_schedules` 里，收任务前先读懂：

1. **只补缺失**：目标 workspace 已有同名目录就跳过 —— 用户改过的口径、删掉的任务
   **都不许被 seed 回来**。seed 只做「补」，**不覆盖、也不删除**。
2. **只 seed 那一个 workspace**：飞书每用户一个 workspace，全 seed 会让每个在线用户的
   调度 Session 各跑一遍「提醒全表所有人」，消息按在线人数翻倍。

因此**下线一条任务要两步**：删掉 agent 包里的 `TASK.md` **并且**删掉 workspace 里的同名
目录；只删包里那份，线上旧目录会继续按原 cron 触发。

## 任务清单

### 公司 TODO 管理（`fire: prompt`：到点把正文当提示词交给模型执行）

| TASK.md | Cron | 行为 |
|---|---|---|
| `todo-remind/TASK.md` | `30 14 * * 1,3,5` | 14:30 填报提醒：读看板当期列，**只**私聊提醒「未填且无已通过请假」的人；不做规范检查 |
| `todo-writing-check/TASK.md` | `0 15 * * 1,3,5` | 15:00 统一检测：加载 todo-writing-standard / todo-truthfulness-check / todo-alignment-check，逐项判 格式／时间／粒度／价值／对齐／重要全覆盖／防复制／优先级／验收核对；违规项私聊本人**一次**按类列全；对齐存疑落盘 `align-pending.txt` |
| `todo-ledger-push/TASK.md` | `0 16 * * 1,3,5` | 16:00 动态台账推送：按 mentor 组生成「上期 vs 本期」前后对比卡片表私聊发送；不发 todo 卡、不建飞书任务、不写 base |

这三条的**判定口径不在正文里**：正文只写「读什么、按什么顺序、失败怎么报」，
规则一律以技能为唯一来源 —— 改口径改技能，别改这里。
三条都是 `visibility: silent`（结果不进普通用户对话）。

### 会议（`fire: tool`：到点直调工具、不经过模型）

| TASK.md | 会议 | Cron | 行为 |
|---|---|---|---|
| `weekday-alignment/TASK.md` | 周中对齐会 `57152787045`（周一/三/五 10:00） | `0 12 * * 1,3,5` | `meeting_pipeline_run`：取最新已完成转写 → 分块分析 → 按路由发送 |
| `weekday-alignment-retry-1730/TASK.md` | 周中对齐会（补偿重跑） | `30 17 * * 1,3,5` | 同上；主任务已投递时按 `record_file_id` 自动跳过 |
| `weekday-alignment-1100/TASK.md` | 日会 `42654699903`（周一/三/五 11:00） | `0 13 * * 1,3,5` | 同上 |
| `weekday-alignment-1100-retry-1730/TASK.md` | 日会（补偿重跑） | `30 17 * * 1,3,5` | 同上；主任务已投递时按 `record_file_id` 自动跳过 |

四条会议任务（两场主任务 + 各自的 17:30 补偿重跑）的正文由 `meeting_schedule_files()` 从 `MEETING_JOBS` 投影生成，
**静态文件必须与投影逐字一致**，由 `test_committed_meeting_schedule_files_match_projection`
强制（文件不齐时该判据跳过，齐了即恢复强制）。
改 `MEETING_JOBS` 的 cron／retry／参数，必须同步改这些文件。

按录制 `record_file_id` 幂等：同一场次重复触发不会重复投递。

> **补偿重跑为什么必须有**：腾讯的文字转写是**异步**产出的，主跑时常常还没生成
> （实测 2026-09-11 周中会：12:00 主跑取不到转写，21:59 才生成）。而管道只认**最新
> occurrence** —— 下一场主跑时最新已经是下一场，当天没赶上就**永久丢失**。故**两场会各留
> 一条 17:30 的补偿重跑**（周中会 `weekday-alignment-retry-1730`、日会
> `weekday-alignment-1100-retry-1730`）。
> 幂等由 `record_file_id` 保证：主任务已成功投递时，重跑只跳过、不重复投卡。
>
> 历史：2026-09-11 曾把两条 17:30 补偿重跑一起下线（#914 移除代码、#916 补带到 main）；
> 2026-09-12 复盘迟到转写后恢复，并把口径统一成「每场会一条 17:30」，22:30 那一档不再保留。

### 运维

| TASK.md | Cron | 行为 |
|---|---|---|
| `heartbeat/TASK.md` | `*/30 * * * *` | 心跳自检：每 30 分钟回 `HEARTBEAT_OK`，证明 agent 循环还活着；顺带清陈旧文件 |

## 运行所需的凭据环境变量

工具层按 `meeting_name + meeting_code` 精确选择 Token 变量, 避免串用账号凭据:

- `TENCENT_MEETING_TOKEN` —— 周中对齐会 `57152787045` 对应腾讯会议账号
- `TENCENT_MEETING_TOKEN_42654699903` —— 日会 `42654699903` 对应腾讯会议账号

**Token 是机密, 不得进入代码、TASK.md、工具参数、日志或 Git。** 变量名与取值的唯一
约定见 `deploy/haitun/.env.example`(只含变量名与说明, 不含真实取值)。

## 不同环境的加载方式

- **生产容器**: `launch-gateway.sh` 启动时 `set -a; . /workspace/.env; set +a`,
  凭据放在 `/workspace/.env`(见交接文档 §2.3), 改后需重启栈生效。
- **Windows 本地开发**: 由启动脚本(如 `start-haitun2.ps1`)先解析仓库根
  `.env`(被 `.gitignore` 忽略)注入进程环境, 再启动 gateway/channel; `.env`
  缺失时回退到用户级环境变量。判断是否生效:
  `test -n` 的等价检查(PowerShell: `[bool][Environment]::GetEnvironmentVariable('TENCENT_MEETING_TOKEN')`),
  **不要把值打印到日志**。
- **时区**: 调度按运行环境本地墙钟时间执行, 应为 `Asia/Shanghai`(`TZ`)。

## 手动验证(只读)

```powershell
# 进程环境已注入后, 用仓库 .venv 直跑只读探测, 不应把 token 打印出来
python .run-logs/probe_tencent.py   # 或等价: 对两个 meeting_code 调 get_records_list
```
