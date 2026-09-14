# 会议定时任务种子（`agents/feishu/schedules/`）

本目录是两场固定会议定时任务的**种子 TASK.md**。Gateway 以 feishu 模式启动时,
meeting 自动化会把它们落到会议专用 workspace(`.meeting-session/schedules/`)并由
`meeting-session` 会话激活。

> **本目录是生成物, 不要手改。** 内容全部由代码白名单
> (`agents/feishu/tools/_meeting_automation.py` 的 `MEETING_JOBS`) 投影而来:
>
> ```bash
> python scripts/gen_meeting_schedules.py          # 生成 / 覆盖
> python scripts/gen_meeting_schedules.py --check  # 校验 (CI 用)
> ```
>
> 改了 `MEETING_JOBS` 的 cron / retry / 参数之后**必须重跑生成器**。不重跑不会报错,
> 只会让线上继续按旧的 TASK.md 触发 —— 调度器实际读的是落包这一份, 不是代码那一份。
> `tests/test_meeting_automation.py` 的两条判据(`..._match_projection` /
> `test_no_orphan_meeting_schedule_files`)把两边钉死。

> seed 只补 workspace 里**缺失的同名任务**, 已存在的一律不覆盖、也不删除 (`_scheduler_manager._seed_missing_schedules`)。因此下线一条任务要**三步**: 1) 删掉 `MEETING_JOBS` 里的声明; 2) 删掉本目录下的落包(否则下次部署被 seed 回去); 3) 删掉线上 workspace 里的同名目录(seed 不负责回收, 只删前两步线上会继续按原 cron 触发)。

## 任务清单

| TASK.md | 会议 | Cron | 行为 |
|---|---|---|---|
| `weekday-alignment/TASK.md` | 周中对齐会 `57152787045`(周一/三/五 10:00) | `0 12 * * 1,3,5` | `meeting_pipeline_run`: 取最新已完成转写 → 分块分析 → 按路由发送 |
| `weekday-alignment-retry-1730/TASK.md` | 周中对齐会(主跑未拿到转写时补跑) | `30 17 * * 1,3,5` | 同上, 按 `record_file_id` 幂等跳过已投递场次 |
| `weekday-alignment-1100/TASK.md` | 日会 `42654699903`(周一/三/五 11:00) | `0 13 * * 1,3,5` | 同上 |
| `weekday-alignment-1100-retry-1730/TASK.md` | 日会(主跑未拿到转写时补跑) | `30 17 * * 1,3,5` | 同上, 按 `record_file_id` 幂等跳过已投递场次 |

> **17:30 的两条补偿重跑是保留的。** 腾讯的文字转写异步产出, 主跑(12:00 / 13:00)时常还
> 没生成(实测 09-11: 12:00 主跑没有转写, 21:59 才生成), 而管道只认最新 occurrence ——
> 当天没赶上就永远是「没有转写」, 没有兜底的那场会直接丢掉一次。兜底按 `record_file_id`
> 去重, 主跑已成功投递时自动跳过, 不会重复发卡, 代价只是一次空跑。
> (2026-09-11 的 #916 曾把这两条置空下线, 次日 #931 基于 main 重做时把 `retry_crons`
> 又带了回来, 而本文档当时仍写着"已下线"。以本表为准: 保留。)

运行语义: `visibility: silent`(结果不进普通用户对话)、`fire: tool`(到点直调工具、
不经过模型自主决策)、按录制 `record_file_id` 幂等。

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