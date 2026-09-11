# ToB 内容分层 + 工具架构演进（会话 2）交付

## 结论

四项工作全部落地，合成一条分支 `feat/tob-layering-s3`，PR #901。原 #899 / #900 已并入并关闭。

| 项 | 状态 | 关键数字 |
|---|---|---|
| A1 分层隔离进内核 | 已落地 | 4 处支架，5 个 `xfail(strict=True)` 按设计销账 |
| ext4 加载顺序上 CI | 已落地 | feishu **135/137 错位**、4571 逆序对（NTFS 只 16/134） |
| 多内容根 | 已落地 | `content_root` 在 `src/` 从 **0 → 13 处**，`layer_id` 不再从 tools 路径推 |
| 阶段 E（删 M2 + 分层暴露 + 档位开关） | 已落地 | 三档 `PSI_TOOL_EXPOSURE`，默认档压到全集 1/10 以下 |
| 考勤不收敛（并行卡） | 已落地 | 归因到「返回对决策欠定」，非确定性坏字段 |

判据总量：本会话新增约 **160 条**，全部做过变异复核。

**生产收益一条都没验。** 下面每一节末尾都写了各自的缺口。

## 一、A1 分层隔离进内核

### 问题

A1（分层私有模块隔离）此前只活在测试文件里。内核 `tool_registry.py` 的
`_tools_dir_on_sys_path` / `_stash_private_modules` 建立在「同一时刻只有一个 tools 目录在 scope 内」
这个前提上，分层把前提推翻。

另有一处独立缺陷，与分层无关、顺序加载就会串：`_stash_private_modules` 的判定
`if not name.startswith("_") or "." in name` **跳过一切带点模块名**，所以
`agents/feishu/tools/_feishu/` 的 15 个模块从不入 stash，作用域退出后仍留在 `sys.modules`，
第二个自带 `_feishu` 的目录会绑到第一个目录的子模块上。

### 改法

新增 `src/psi_agent/session/tool_layers.py`。四处支架，每一处都是**判据先红才发现的**，不是设计出来的：

1. **裸名别名只在提问工具文件 exec 期间绑。** CPython 解析带点子模块的父包用的是裸名，
   只改名会 `KeyError`；常驻绑定则毁掉隔离。
2. **`psi_layer_<token>` 自身必须作为包存在于 `sys.modules`。** 触发条件是
   *父包是否被当作包解析*，与嵌套深度无关。
3. **`_module_cache` 命中会跳过 exec，命中路径必须补绑裸名。** 否则「同层第二个带点导入者失败而
   第一个成功」，且只在缓存热了以后才出现。
4. **跨层兜底按 `Layer.priority` 降序，不跟打开顺序。** `priority` 是显式字段，
   不从列表顺序推、也不从 `layer_id` 推。**这条只有三层才看得见**，两层永远测不出区别。

语义已由实测定死：**私有模块解析是「提问层优先」，不是优先级优先。**
`_` 前缀帮手是一层的实现、不是它可寻址的表面；反过来会让高优先级层的 `_shared`
劫持低优先级层自己的实现。

带点名缺陷的修法：判定改为「文件是否落在该私有包目录下」。修好后
`test_layer_isolation_probe.py` 里 5 个 `xfail(strict=True)` 按设计销账（XPASS 会让 strict 转红），
标记已摘除。

### 没验到

- **四层及以上未测。** 第 4 处支架是三层才照出来的；四层是否再冒出第 5 处，用同样的理由不能排除。
- 生产上「59 个工具加载失败归零」未验。
- 「每会话 114 文件 / 104 万字节的重复编译省掉多少」未验。

## 二、ext4 加载顺序判据上 CI

### 问题

`tool_registry.py` 里 `glob("*.py")` 的排序此前**只在 Windows/NTFS 上量过**。
加载顺序是隐藏输入（59 个工具文件曾因此全部加载失败），而这条判据从没在生产用的文件系统上复量。
这一项是会话 1 自认「最该补而没补」的。

### 改法

新增 CI job `tool-load-order`（ubuntu runner）+ `scripts/check_tool_glob_order.py`。

**单独 job 而不塞进 lint**：lint 是 publish 的门（`needs: lint`），把测试挂进去会让发版连带依赖测试。

### 验证

CI 在真 ubuntu/ext4 runner 上实测：

| 目录 | ext4 错位 | 逆序对 | 对比 NTFS |
|---|---|---|---|
| `agents/feishu/tools` | **135 / 137** | 4571 | 134 里只错 16 个（21 逆序对） |
| `agents/desktop/tools` | **64 / 65** | 944 | 65 里错 13 个 |

ext4 按 htree 哈希返回目录项，与字母序几乎无关，所以**几乎每个文件都错位**。
三种目录形状（原样检出 / 逆序新建 / 46 个文件重建）给出**完全相同**的数字，
说明这不是偶然布局而是文件系统的稳定性质。**那行 `sorted` 在 Linux 上远比在 Windows 上吃劲。**

### 没验到

- 数字来自 CI job 的日志，不是在本机 ext4 上量的。
- 文档里**不写死这三个数**，只写「非零即说明 glob 顺序是活输入」这个判据——文件数会随新增工具漂移
  （交接记录说 134，考勤卡新增文件后已是 137）。

## 三、多内容根

### 问题

`content_root` 在 `src/` 里出现 **0 次**，`layer_id` 只是个从 tools 目录路径推出来的占位符。
层身份挂在路径上，两个内容根指向同名目录就无法区分。

### 改法

新增 `src/psi_agent/session/content_roots.py`（154 行）。**内容根成为层身份**，
`_layer_id` 改为接受 `roots` 参数，不再只从 tools 路径推。

红线守住了：`ScheduleRegistry` 仍用 `workspace_path`。
卡自己交代了理由——**内容根答不了「这条日程属于谁」，顺手统一成 `agent_root` 会静默丢掉所有用户日程。**

判据 25 条 + 6 个变异旋钮。变异复核过程中红检查揪出一处缺口：**缓存键当时无人攻击**，补了判据。

### 没验到

- 生产上多内容根的实际收益未验。
- 生产内容层此前实测 **42 个文件漂移**（29 新增 + 13 就地改动），这批漂移与新层身份如何对齐，未处理。

## 四、阶段 E：删 M2 + 分层暴露 + 档位开关

### 问题

`tool_defs.py:66` 的 `TMPFIX_M2_CORE_TOOLS` 是临时白名单（tmpfix-20260902），
把模型能**看见**的工具收窄到 M2 高频集。**只删不补请求体积会涨回三倍**
（旧门的生产实测：285566 → 83725 字符，省 70.7%，3 小时流量 / 496 次调用 / 44 个 distinct 工具）。

### 改法

三件一起做，缺一件就是回退：

1. **删掉硬编码门。** `TMPFIX_M2_CORE_TOOLS` 与 `tmpfix_m2_gate` 归零，
   `test_tmpfix_m2_gate.py` 删除。
2. **分层暴露接替。** 新增 `src/psi_agent/session/tool_exposure.py`（189 行），
   暴露清单迁进内容包 `agents/feishu/tools/EXPOSED.txt`（95 行、进 git），
   按层声明而不是靠一张写死的名单。
3. **三档开关。** 环境变量 `PSI_TOOL_EXPOSURE`，三档 `layered` / `declared` / `off`，
   未设默认 `layered`，**每次 Session 启动读一次**——不重新部署就能切档，
   线上体积或行为异常时归因与回退都是切开关，不排停机窗。

保住了原有的关键性质：**收窄的是 discovery 不是 capability。**
被挡在 `tools` 数组外的工具仍能通过 dispatch 调到，`get()` 保持全集可达。

**换锚而不是静默让判据失效**：`_tool_status.py` 那份中文别名表原先「覆盖 M2 高频集，由判据锁死」，
M2 名单删了之后判据重锚到内容包清单上，并在测试里写明换锚理由。

清单缺失的语义是刻意的且写进 docstring：**无清单 = 本层没有意见 → 全量暴露；
空清单 = 本层什么都不暴露**，两者区分开。读不了则告警后按无清单处理，理由是
「失败关闭会因为一个权限错误剥掉整个 Session 的工具」。

判据 48 条 + 8 个变异旋钮。体积判据用**相对判据**（默认档压到全集 1/10 以下、
`off` 档逐字节还原全集），而不是写死字符数。

### 没验到（这一节缺口最大）

- **70.7% 属于旧门，不是本实现的成绩。** 新分层暴露在生产上的体积**没量过**。
- **默认档的收益依赖 `EXPOSED.txt` 在生产存在。** 生产是挂载源码、`agents/feishu/tools/`
  那份内容实测有 42 个文件漂移。**清单没送到就静默退回全量暴露、体积涨回三倍，
  而线索只有一行 DEBUG 日志**（线上跑 INFO）。上线前必须确认清单已投放。
- `refuse_agent_write` 分层后挡住哪些工具，仍未核对。

## 五、考勤查询不收敛（并行卡）

### 问题

`feishu_attendance_query` 在生产同一入参重复 **800 次、其中 754 次逐字节相同**，
工具没失败、数据不空。

### 归因

84 个回合里 78 个只调 1 次就收敛，6 个炸成 `128,128,128,128,108,50`。
**回合 46 与 47 的 user prompt 与工具返回逐字节相同，一个 1 次收敛、一个 128 次撞顶。**

所以成因**不是某个确定性的坏字段**，是返回**对该决策欠定**：
`check_in_result: "Normal"` 配 `check_in_time: ""`（800 行 Normal 空时间 vs 12 行有时间），
没有任何字段能区分「不需要打卡」与「没取到时间」。

### 改法

给返回补 `NoNeedCheck` / `SystemCheck` 与缺失时间哨兵，把判定所需的维度补进返回；
SOP 写明查一次就够。**没有改 `max_tool_rounds`**——那是掩盖，且 60 是刻意的。

### 没验到

- **最终判据在生产**：「不再出现 754 次重查」本地无法验证，需上线后复量。
- **这个形状不是考勤独有**：同一批历史里 `feishu_api` 最高 413 次、`feishu_message_list` 187 次。
  本次只处理了最严重的那个。

## 六、合并与验证

16 个 commit 合成一条分支。合并中出现一处真冲突：`src/psi_agent/session/AGENTS.md`，
两张卡都重写了加载顺序契约，**两边内容都保留**，不是取其一。
另把 `origin/main` 的 6 个 commit（#891/#892/#894/#895/#896/#898）并入，无冲突。

判据在**合并后**的树上重量，不照搬各卡自述。基线取自 `origin/main` 的干净控制树
（`git worktree add --detach`，不用 `git stash`），两边跑同一条命令：

| 项 | 合并树 | 控制组 |
|---|---|---|
| `tests/psi_agent/session` + `channel/feishu` | 13 failed / **1120** passed / 2 skipped / **0** xfailed | 13 failed / 824 passed / 2 skipped / 5 xfailed |
| FAILED 名字 | **逐条相同**（`test_channel_adapter.py` 8 + `test_server.py` 5，全是 `asyncio events.py:487 NotImplementedError`，Windows 基线） | 同上 |
| `ruff check` / `format --check` | 退出码 **0 / 0** | — |
| `ty check --python` | 退出码 1，4 个 `unresolved-attribute` | 4 个，**位置逐条相同** |

CI：`lint` / `feishu-web` / `tool-load-order` 全部 success。

## 七、红线状态

- `ScheduleRegistry` 仍用 `workspace_path`，未动。
- `get()` 仍是全集可达，未动。
- 生产未做任何改动。
- `gateway/`、`desktop/`、ToC 侧代码、`deploy/` 未碰。

## 八、过程中值得留下的两条

**`merge --ff-only` 报「Already up to date.」却清掉暂存区新增文件。**
分支 ref 已被 `git update-ref` 提前推进后，merge 变成空操作，但**空操作照样重置索引**，
`A` 状态的暂存新增会从工作树和索引一起消失，而 `git status` 事后仍显示干净。
安全顺序是：先 `git reset <文件>` 降级成未跟踪（`??`），再 merge，合完 `git add` 回去。
未跟踪文件 merge 不碰，暂存新增会。

**Kanban worktree 刚创建时的 HEAD 不是它的工作基点。**
我一度量到 `8fe88` 的 worktree 停在缺少前置依赖的 commit 上、`content_roots.py` 不在里面，
判为「基于过期 base 出活」。复量后发现那是 worktree 刚建、**尚未切到任务分支**的瞬时状态，
切过去以后 base 是对的。**要核的是任务分支上的 HEAD，不是刚建目录时的 HEAD。**
