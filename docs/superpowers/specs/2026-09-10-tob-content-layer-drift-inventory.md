# ToB 生产内容层与仓库的差异清单

> 状态:**清单已建立,未做任何对齐动作。** 待决定各文件的处理方向。
> 基线:生产 = A 机 `47.100.84.197` 的 `/srv/haitun/psi-agent/workspace/`,2026-09-10 14:35 快照。
> 仓库 = `agents/feishu/`,分支 `feat/tob-content-layering`。
> 比对方法:逐文件 LF 归一化后比 md5(生产是 LF、仓库是 CRLF,裸比会几乎全不一致)。

## 结论先行

| 类别 | 数量 | 性质 | 建议 |
|---|---|---|---|
| **仅生产** | **9** | 真人在生产上直接写的,**git 里完全不存在** | 先入库,纯增量零风险 |
| **内容不同** | **21** | 真漂移,**方向不唯一**(两边各自领先) | 逐个判方向,不可批量对齐 |
| 仅仓库 | 2 | git 有、生产没投放 | 判断是否该投放 |
| 相同 | 203 | — | 无需动作 |

**此前引用过的「38 处漂移」「42 个文件(29 新增 + 13 漂移)」都是旧口径,与本次实测不符,应作废。**
本文的 9 / 21 / 2 是第一次基于逐文件 md5 比对得出的数字。

分目录:

| | 相同 | 内容不同 | 仅生产 | 仅仓库 |
|---|---|---|---|---|
| `tools` | 202 | 19 | 1 | 1 |
| `triggers` | 1 | 2 | 1 | 0 |
| `schedules` | 0 | 0 | **7** | 1 |

`schedules` 一行需单独注意:**生产 7 个,与 git 无一相同** —— 整个定时任务配置层从未入库。

## 一、仅生产的 9 项(唯一副本曾只在生产机)

```
schedules/mentor-check-remind/TASK.md
schedules/remind-checkin-gaobo-0950/TASK.md
schedules/remind-checkout-gaobo-1930/TASK.md
schedules/remind-lunch-gaobo-1155/TASK.md
schedules/todo-ledger-push/TASK.md
schedules/todo-remind/TASK.md
schedules/todo-writing-check/TASK.md
tools/feishu_todo_compare_card_send.py
triggers/todo-iteration-group-auto-reply/TRIGGER.md
```

**两条独立判据交叉验证成立。** 除了上面的 md5 比对,B 机 mtime 纳秒位归因(整秒 = 部署投放 /
带纳秒 = 就地写入)测出 11 个就地写入文件,与本清单高度重合:

```
2026-08-31 19:44  schedules/remind-checkin-gaobo-0950/TASK.md      ← 仅生产
2026-08-31 19:45  schedules/remind-checkout-gaobo-1930/TASK.md     ← 仅生产
2026-09-03 13:02  schedules/remind-lunch-gaobo-1155/TASK.md        ← 仅生产
2026-09-03 21:17  triggers/assignment-delivery-refresh/TRIGGER.md  ← 内容不同
2026-09-03 21:17  triggers/handbook-onboarding-welcome/TRIGGER.md  ← 内容不同
2026-09-04 15:01  triggers/todo-iteration-group-auto-reply/TRIGGER.md ← 仅生产
2026-09-07 14:17  tools/_card_dsl.py                               ← 内容不同
2026-09-09 15:23  tools/feishu_todo_compare_card_send.py           ← 仅生产
2026-09-09 15:25  schedules/todo-ledger-push/TASK.md               ← 仅生产
2026-09-09 15:25  schedules/todo-remind/TASK.md                    ← 仅生产
2026-09-09 15:25  schedules/todo-writing-check/TASK.md             ← 仅生产
```

三个 `TASK.md` 的写入时刻是 **2026-09-09 15:25 —— 搬家当天下午,离停机窗仅 4 小时**。

### 1.1 mtime 归因判据在 A 机已永久失效

A 机上同样的纳秒归因**测出 0 个就地写入**:233 个文件全是整秒,220 个挤在同一分钟
`2026-09-07 11:52`。原因是**搬家的复制动作把 mtime 全部重置成整秒**。

也就是说这条判据被搬家操作本身毁掉了,原始证据只剩在 B 机。**这是清理 B 机之前必须先取证的一项**
—— B 机一删,"哪些文件是真人就地改的"就再也查不出来了。

已固化的快照包含逐文件 mtime 清单(`mtime-manifest.txt`,带纳秒),因为 tar 解开后 mtime 会丢。

## 二、内容不同的 21 项 —— 方向不唯一

`git=` 仓库行数,`prod=` 生产行数,`多/少` 为生产相对仓库的增删行数。

| 文件 | git | prod | prod 多 | prod 少 | 方向线索 |
|---|---|---|---|---|---|
| `tools/meeting_pipeline_run.py` | 671 | 303 | 42 | **410** | **仓库领先**,见 2.1 |
| `tools/_card_dsl.py` | 552 | 1053 | **552** | 51 | **生产领先**,见 2.2 |
| `tools/_feishu_impl.py` | 1278 | 1160 | 3 | 121 | 仓库领先 |
| `tools/_meeting_automation.py` | 573 | 459 | 50 | 164 | 双向 |
| `tools/_positive_negative_list/runtime.py` | 496 | 628 | 153 | 21 | 生产领先 |
| `tools/meeting_transcript_prepare.py` | 282 | 226 | 16 | 72 | 仓库领先 |
| `tools/positive_negative_case_read.py` | 106 | 55 | 1 | 52 | 仓库领先 |
| `tools/_feishu/mentor_ledger.py` | 314 | 363 | 64 | 15 | 生产领先 |
| `tools/tencent_meeting.py` | 88 | 70 | 1 | 19 | 仓库领先 |
| `tools/positive_negative_list_confirm.py` | 291 | 296 | 9 | 4 | 生产略领先 |
| `tools/_feishu/auth.py` | 955 | 947 | 0 | 8 | 仓库领先(纯删) |
| `tools/positive_negative_candidate_analyze.py` | 162 | 161 | 5 | 6 | 双向小改 |
| `tools/positive_negative_list.py` | 226 | 226 | 3 | 3 | 双向小改 |
| `tools/_positive_negative_list/table.py` | 267 | 263 | 2 | 6 | 仓库领先 |
| `tools/meeting_session_notify.py` | 310 | 309 | 3 | 4 | 双向小改 |
| `tools/meeting_session_write.py` | 64 | 67 | 6 | 3 | 生产略领先 |
| `tools/feishu_mentor_ledger_ensure.py` | 56 | 54 | 2 | 4 | 仓库领先 |
| `tools/_positive_negative_list/validation.py` | 58 | 56 | 1 | 3 | 仓库领先 |
| `tools/feishu_mentor_ledger_cycle_table.py` | 115 | 115 | 1 | 1 | 单行差异 |
| `triggers/assignment-delivery-refresh/TRIGGER.md` | 16 | 14 | 0 | 2 | 仓库领先 |
| `triggers/handbook-onboarding-welcome/TRIGGER.md` | 19 | 17 | 1 | 3 | 仓库领先 |

**不能批量对齐:两个方向同时存在。** 下面两例说明为什么必须逐个判。

### 2.1 `meeting_pipeline_run.py` —— 仓库领先,生产落后

git 版本 671 行,生产只有 303 行。git 多出的部分是成体系的新功能(docstring 首段即可看出):

```
Terminal states: completed / notifications_pending / transcript_prepare_failed /
transcript_pending 和 analysis_empty(刻意为之)...
Every terminal state is appended to run_metrics.jsonl; hard failures additionally
alert the job's alert_recipients (once per record / per day), so a silent
scheduler never fails silently.
```

git 最后提交 `2026-09-08 4223dce3 fix(meeting): 空分析不再伪装成功, 记录 ...`。
**生产上跑的是没有这个修复的旧版** —— 即"空分析仍会伪装成功"。这类应当投放到生产。

### 2.2 `_card_dsl.py` —— 生产领先,git 落后

生产 1053 行,git 只有 552 行,生产多出 552 行。且它**在 B 机 mtime 归因里是就地写入**
(`2026-09-07 14:17`)。生产侧多出的内容带完整设计注释:

```python
# 单一真源 = _todo_card_impl._UNDO_ROUNDS:三模块共用同一上限,任一处漂移都会
# 让预注册深度与重建轮次错位(某轮变死键),故不再各写字面量 20,统一取此常量。
_MAX_ROUNDS = _UNDO_ROUNDS
```

git 最后提交是 `2026-08-31`,比生产的就地写入早一周。**这是真人在生产上开发出来、从未回流的工作。**
对齐它等于删掉这些工作。

### 2.3 判方向不能只看行数

上表"方向线索"一列是**按行数增删推断的,未逐个读 diff 确认**。行数只能提示,不能定论:
双向小改的那几个(`positive_negative_list.py` 3 增 3 删等)完全可能是两边各自改了不同地方。
`agents/feishu/tools/` 近 30 天有 26 次提交,仓库侧一直在动,所以"仓库领先"的那些也需要
确认生产是否叠加过就地修改。

## 三、仅仓库的 2 项

```
schedules/heartbeat/TASK.md
tools/_feishu/ledger_schema.py
```

git 有、生产没有。`ledger_schema.py` 值得注意:`_feishu_impl.py` 的 git 提交信息是
`常量下沉消掉 mentor_ledger...`,可能与这个新文件配套 —— **若生产缺它而 `mentor_ledger.py`
又是生产领先版本,两者可能不兼容**(未验证)。

## 四、建议的处理顺序

1. **已完成:固化。** 两机内容层 + mtime 证据已下载到本机
   `psi-agent-backups/content-layer-20260910-1435/`(A 233 文件 / B 232 文件,sha256 已校验)。
   在此之前第一节那 9 项的唯一副本只在生产机上。
2. **入库「仅生产」9 项。** 纯增量,git 里不存在这些文件,新增不覆盖任何东西。
   做完真人产出才有版本控制。
3. **逐个判「内容不同」21 项。** 需要人工读 diff 定方向,尤其 2.1 / 2.2 两个反向的大改。
4. **投放仓库领先的部分到生产。** 走标准发布流程,不要手工 `docker cp`。

### 4.1 入库时的两个注意点

- `triggers/todo-iteration-group-auto-reply/TRIGGER.md` 的 `filter` 硬编码了具体群 id
  `oc_258df3b0ff3f67cf20b68dab60de9c8a`。入库到示例目录前应考虑是否该保留真实群 id。
- 三个 `remind-*-gaobo-*` schedule 带真实人名。同上。

## 五、未验证项

- 21 项的方向判断**基于行数增删推断,未逐个读完 diff**。仅 2.1 / 2.2 两例读了实际内容。
- `ledger_schema.py` 缺失是否会导致生产的 `mentor_ledger.py` 不兼容,未验证。
- 生产 A 机 233 文件 vs B 机 232 文件,差 1 项未定位。可能是搬家后新产生,也可能是
  打包时 `__pycache__` 排除差异。
- 本清单比对的是 A 机(现生产)。**B 机内容层是否与 A 完全一致未逐文件核** —— 搬家当天核的是
  数据库 33 表逐行相等,内容层只核了文件数。
