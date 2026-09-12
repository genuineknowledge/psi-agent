# feishu_attendance_query 不收敛：归因与修法

## 结论先行

`feishu_attendance_query` 在生产上被**同一入参重复调用 800 次**（其中 754 次逐字节相同），
每次返回都是 `ok: true` 且内容一致。工具没有失败，数据不空 —— 这不是工具 bug。

**根因不是返回里某个确定性的坏字段，而是返回对被问的判断「欠定」。**
决定性证据：**同一个 session 里，回合 46 与回合 47 的 user prompt 逐字节相同、工具返回也逐字节相同，
一个 1 次就收敛，一个 128 次撞上 `[Max tool rounds reached]`。** 同因不同果，
说明模型停在决策的刀尖上，采样偏一点就翻向重试。

修法是把模型缺的那几个维度**写进返回**，并在提示词里明确「重查不会变」。
**没有改 `max_tool_rounds`**（60 是刻意的）。

## 实测数据

来源：境内 A 机 `47.100.84.197`，`/srv/haitun/psi-agent/workspace/.psi/appdata/histories/`（414 份历史，只读）。
主样本 `scheduler-cedce38a1e5fcfab.jsonl`，1778 行。

按回合切开后的调用次数（84 个回合）：

```
1,1,1,1,0,0,1,1,1,1,0,1,1,1,1,1,1,1,1,1,1,1,1,1,1,0,1,0,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,
128,128,128,128,108,20,20,20,50,
1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1
```

78/84 个回合 1 次收敛，6 个炸开。**卡里说的 22 次是低估。**

这个形状不是考勤独有，同一批历史里按「单 session 内同一入参最大重复次数」排：

| 工具 | 最大重复 |
|---|---|
| `feishu_attendance_query` | **754** |
| `feishu_api` | 413 |
| `feishu_message_list` | 187 |
| `schedule_manage` | 28 |

考勤是最严重的那个，但这是个通用形状 —— 本卡只修考勤，其余留作后续。

## 模型在等什么它没等到

任务（定时跟进）要它判定「8/17-8/19 的缺卡是否已清除，或显示无需打卡」，然后三选一：
发消息 + 删定时任务 / 静默 `NO_REPLY` / 私聊提醒。返回给它的是：

```json
{"ok": true, "count": 3, "results": [
  {"day": 20260817, "check_in_result": "Lack",   "check_in_time": "", ...},
  {"day": 20260818, "check_in_result": "Lack",   "check_in_time": "", ...},
  {"day": 20260819, "check_in_result": "Normal", "check_in_time": "", ...}]}
```

三处欠定：

1. **`Normal` 配空时间戳，读起来像自相矛盾。** 全量历史统计：
   `Normal` 有 **800 行空时间戳 / 只有 12 行带时间戳**，`Lack` 1603 行全空。
   空时间戳是**常态**，但空字符串本身不说明「响应里就没有」还是「没取到」。
2. **区间级判断不在返回里。** 每个消费者问的都是区间问题（「这三天干净了吗」），
   返回只有逐行数据。区间内两天 Lack 一天 Normal，是「清除了」还是「没清除」要靠模型自己归纳，
   而归纳结果不是事实、每次重新推。
3. **没有任何「重查也不会变」的信号。** 这读的是已落库的历史记录，重查必然同结果，
   但返回没说。对模型而言重试是零成本动作。

三者叠加：判据所需的维度不在返回里 + 重试无成本 = 停不下来的条件齐备。

## 改法

### 工具返回（`agents/feishu/tools/_feishu/attendance.py`）

- 新增 `_day_verdict()`，把逐行数据卷成区间判断：
  `unsettled_days` / `settled_days` / `days_returned` / `per_day_results`。
  **判「干净不干净」直接读 `unsettled_days`，空数组=干净。**
- 「已结清」用**白名单** `_SETTLED_RESULTS = {Normal, NoNeedCheck, SystemCheck}`，
  不是 `Lack` 黑名单 —— 没见过的状态码落到「需要关注」一侧，反过来会把真问题静默标干净。
- 空时间戳不再是 `""`，改为 `no punch timestamp in the response for this day`，
  把「响应里就没有」这件事说出来。
- 新增 `query_is_complete` / `retry_will_return_identical_data` / `next_step`，
  `next_step` 点名工具名并明确禁止重复调用，空结果时额外说明「这是答案不是失败」。

### 提示词

- `skills/feishu-attendance/SKILL.md`：新增「打卡结果查一次就够 —— 不要重查」一节，
  带 754 这个数字，并用表格列出三种最容易被误读成「没查到」的返回。
- `skills/feishu-attendance-payroll/SKILL.md`：分片查询处补同样口径（分片场景下重复最难被看出来）。
- 工具 docstring：把「一次调用回答整个区间」放在最前面。

## 判据

`agents/feishu/tests/test_feishu_attendance_convergence.py`，13 条。

**核心判据落在「同一入参连续调用 N 次」这个可观测事实上**，而不是模型行为
（模型行为是采样结果，不可复现，判据建在上面必然是 flaky 的）：

- `_identical_call_runs()` 把生产取证还原成函数：按 `arguments` **原始字符串**计数，
  所以是字节同一性而非语义相似。
- `test_the_repeat_detector_catches_the_production_runaway` —— 先拿 128 次的真实形状**标定探测器**，
  要求精确报出 128。没有这步，下面的判据可能只是装饰。
- `test_a_converged_turn_is_not_flagged` —— 1 次不算重复，否则「全都报警」也能过上一条。
- 其余覆盖：一次调用只落一个请求、`next_step` 措辞、区间判定、干净区间、未知状态码、
  `Normal` 空时间戳、零行返回、两个 SKILL.md 的措辞。

### 变异复核

四条变异，均确认转红且报错指向被改的那件事。**用 Edit 回滚，不用 `git checkout`**，
每次都核对 `git diff --stat` 非空。

| # | 改了哪一行 | 哪条红了 | 报错指向 |
|---|---|---|---|
| 1 | `_fmt_check_time(cin) or _NO_TIMESTAMP` → 去掉兜底 | `test_normal_without_a_timestamp_is_explained…` | 空字符串正是引来重试的形状 |
| 2 | 白名单 → `r == "Lack"` 黑名单 | `test_an_unknown_result_code_counts_as_unsettled` | 未知码被静默标干净 |
| 3 | 探测器 `+= 1` → `= 1`（致盲） | `test_the_repeat_detector_catches_the_production_runaway` | `assert 1 == 128` |
| 4 | `retry_will_return_identical_data` → `False` | `test_one_call_answers_the_range…` | 确定性声明丢失 |

## 验证

- 新判据 13 passed。
- 控制实验：`git worktree add /tmp/ctrl-attend HEAD --detach`，跑同一批，
  **FAILED 名字排序后 diff 完全相同** —— 3 条 `test_auth_*` 失败是基线，不是本卡引入。
  本卡侧 440 passed vs 控制组 427 passed（+13 即新判据）。
- lint 按**退出码**看：`ruff check` 0 / `ruff format --check` 0 /
  `ty check --python /f/code/psi-agent/.venv` 1，**4 个诊断全是 `os.killpg`**，与基线逐条相同。
  （中途我自己引入过第 5 个诊断 —— 给函数挂属性 —— 已改成 `_FixedInvoke` 类消除。）

## 没验到的部分

**这个 bug 的最终判据是「生产上不再出现重复调用」，本地无法验证。**
根因是采样在欠定条件下翻向重试，要证明修好必须把模型放进回路里按温度跑，
本地判据只能证明**返回里现在有了它当时缺的那几个维度**、以及**重复形状一旦复现会被判据抓到**。

**需上线后复量**：重跑本文档「实测数据」一节的按回合统计脚本，看 6 个炸开的回合是否归零。
在那之前，不能说这个问题已解决。

同理，「为什么模型原来不接受、现在会接受」这个解释里，**「原来不接受」是实测坐实的**
（同因不同果 + 三处欠定 + 全量时间戳统计），**「现在会接受」是推断** ——
补齐的是模型当时缺的维度，但没有生产数据证明补齐就足够。
