# 飞书生产三缺陷:NO_REPLY 泄漏、省略削掉待发送输出、句柄进用户文本 · 本批设计

> 根因均由生产日志实测坐实(2026-09-04),不是推演。生产机历史目录
> `/srv/haitun/psi-agent/workspace/.psi/appdata/histories`,只读取证。

## 结论

**三个缺陷同源于一件事:内部约定的记号漏到了它不该到的一侧。** `NO_REPLY` 本该被渠道吞掉却发给了用户;省略句柄本该只给模型看却进了用户文本;而省略本该只碰历史,却削掉了 assistant 刚生成、还没送达用户的输出——包括其中的 `[SEND:]` 标记,这是用户反复问「文档呢」的直接原因。

**最伤的一条是省略削掉待发送输出,它会自我延续。** 模型下一轮回看自己说过什么,看到的是句柄,于是以为自己没说成话,道歉并补发(生产原话:「抱歉,上一条消息里发送标记被截断了,文档没真正送达。现在补发:」),补发的内容在再下一轮又被削。用户永远拿不到那个文档。

**修法边界:「还没送达用户的内容不算历史」。** 用回合水位线精确表达这句话——不是按时间开窗口,不是给某类内容开后门。跨回合的交付物记忆是另一半问题,由句柄自述 + 取回工具处理,明确**不靠扩大省略豁免**:豁免区随「一件事」的长度无上限增长,聊够久就等于关掉省略,而省略是预算的唯一硬保证。

**一处已被日志推翻的假设,记此备考:** 最初怀疑是 thinking 泄漏到 content(上游 deepseek 被注入 `thinking=disabled`,模型没有 reasoning 通道,独白落进 content)。日志否掉了它——生产 line 6370 那 2128 字符是模型在正式回复里一路自我推翻写出来的(「让我重新想一下…」「不对,看对话流程」「最稳妥:」),是它认为自己该输出的 content,不是被错投的 reasoning。**不要去改 provider 那处 thinking 注入,那是另一件事。**

---

## W —— 是什么

### 三个缺陷的实测形态

**缺陷 A · NO_REPLY 抑制只覆盖卡片回调,普通对话完全没开**

`suppress_silent_reply=True` 只在 `channel/feishu/_card_action.py:511` 传。普通聊天走 `_handle_and_stream`,用默认值 `False`(`client.py:513`),于是 `client.py:541` 那个「攒字符边流边猜」的抑制分支压根不进,`NO_REPLY` 原样 `stream.append` 给用户。

实测(用户 1,`feishu-ou_b1a3bdba…jsonl`,43 MB / 6417 行):content 含 `NO_REPLY` 的 assistant 行 **231 条**,全文件出现 **1229 次**,其中 line 116、148 整条就是 8 字符裸 `NO_REPLY`。

**缺陷 B · 省略削掉 assistant 刚生成、还没送达用户的输出**

机制链条(已核实):

```
第 1 轮  assistant 说一段话(含 [SEND:/path])→ 写完就落库(agent.py:837)
第 2 轮  中间夹了多条 tool 结果行
第 3 轮  组装请求时它已不在 _elidible_candidates 保护的 paired[:-2] 之内
         → 成为合格候选 → 被换成句柄
         → 上游看到「我上一条什么都没说」
         → 模型道歉并补发 → 补发的在下一轮又被削 → 循环
```

实测(用户 2,`feishu-ou_2528b878…jsonl`,18 MB):4 条 assistant 行含省略句柄,全部在内容末尾——

| 行 | 句柄 |
|---|---|
| 5856 | `[已省略 1072 字符, 句柄 assistant#466000]` |
| 5872 | `[已省略 67 字符, 句柄 assistant#467000]` |
| 5874 | `[已省略 69 字符, 句柄 assistant#467100]` |

line 5874 的上下文最能说明危害:模型说「抱歉,上一条消息里发送标记被截断了,文档没真正送达。现在补发:」,然后补发的内容自己又被省略成句柄。

> **一处必须纠正的理解:省略不改写落盘历史。** `_project_for_ai`(`history_display.py:297`)为每行建新 dict,`_elide_until` 只写 `projected` 的 content 键,源行的 content 是字符串、不可变。历史文件里那三条句柄**是模型自己抄格式抄出来的**——它在上一轮请求里看见自己刚写的那条被换成了句柄,于是在下一轮输出里复述了它。真正被削的是**送进上游的那一份**。判据要照着这个机制写,不要断言落盘内容被改。

**缺陷 C · 省略句柄进了用户可见文本**

模板 `ELISION_HANDLE_TEMPLATE`(`request_assembly.py:141`)设计用途是只给模型看的内部占位符。`history_display.py:364` 的 `strip_transfer_markers` 清了 `[SEND:]`/`[RECV:]`,但没剥省略句柄。

附带危害已实测:模型自己也被句柄绕进去了——line 6370 那段独白里写着「用户消息为空(已省略)?」「返回了 [已省略](即选项文本)」,它把句柄当真实内容解读,这是它陷入 clarify 自我怀疑循环的助推因素之一。

### 验收标准

按卡逐条列,每条都必须能变红,且**必须做变异复核**(故意改坏实现、确认用例真失败;等价变异或打不中的变异要如实记下来,不许假装覆盖)。

**卡 A(NO_REPLY 覆盖面)**
1. 普通对话吞掉 `NO_REPLY`。
2. 卡片回调仍正常——`client.py:552` 那条 `kind == "tool_result"` 时钟信号仍生效。**这条不得打乱**,否则卡片按钮场景会开始冒出多余回复。
3. 两侧判据必须同时存在。

**卡 B(回合水位线)**
1. `[SEND:]` 标记不再被省略吞掉。**这是用户丢文档的直接原因,本卡最重要的一条。**
2. finally 清理:回合抛异常 / 被取消(anyio cancel)/ 撞满 `_max_tool_rounds` 三种退出,水位线均已复位。
3. 真实机制覆盖:多轮工具循环中,第 1 轮的 assistant 行在第 3 轮组装时仍未被省略。
4. 定时/触发回合同样适用(`agent.py:679` 那个 `schedule.` 分支只跳 hook、不跳循环)。
5. 现有行为不变:压不住预算时 `within_budget=False` 如实上报;滞回语义(`_elided_row_ids`、`SHRINK_TARGET_FRACTION`)不被打乱。

**卡 C(剥句柄)**
1. 用户可见文本不含句柄——判据必须落在 Gateway 投影这一层,不许借 Session 层内部函数假绿。
2. 反向判据:送往模型的请求里句柄**仍然保留**(那是它的正当用途,剥掉会让省略变成静默删除)。
3. 覆盖句柄在末尾、中间、一行多个三种位置。

**卡 D1(句柄自述已送达文件)**
1. 自述文本**不含可被渠道扫描器识别的发送标记**——直接拿 channel 侧 `SendMarkerScanner` / `iter_send_paths` 去扫,断言扫不出路径。本卡最重要的一条,防的是「修一个 bug 引入一个更糟的 bug(重复发文件)」。
2. 不含发送标记的行,句柄保持**字节不变**(防预算回退)。
3. 覆盖一行含多个 `[SEND:]`。
4. 滞回不被打乱:同一行在连续回合产出**逐字节相同**的句柄(否则上游 prefix 缓存实测 99.7% 命中率全失配)。已有判据 `test_second_shrink_leaves_earlier_handles_byte_identical` 仍绿,并补一条覆盖带自述的句柄。
5. `_HANDLE_PREFIX` 幂等检查不被绕过:带自述的句柄不会被二次省略。

**卡 D2(history_recall 取回工具)**
1. 路径逃逸:handle 带 `..` 或绝对路径时拒绝,不读到会话外文件。
2. 返回长度上限生效。
3. `role#ordinal` 句柄跨进程对不上时报清楚,不静默返回空或瞎猜一行。
4. `tool_call_id` 形态的句柄能正常捞回。

**集成后(主会话负责)**
各卡自己绿不等于合起来绿——本仓库出过「各自全绿、git 不报冲突、合完炸在收集期,40 条判据一条都跑不了」。集成分支上必须重跑全量,并与失败基线对比。

---

## H —— 怎么做

### 缺陷 B:回合水位线

在 `agent.py:706` 那行 `self._conversation.add(stored_user_message)` 之后,取 `len(self._conversation.messages)` 作为水位线交给 `RequestAssembler`。`_elidible_candidates` 只在水位线之前取候选:索引 ≥ 水位线的行是本回合产出,整轮豁免;回合结束后它们自然变回普通历史,该省就省。

**回合的边界是代码结构给的,不是语义判断。** `agent.py` 那个生成器每收到一条用户消息被调用一次,走完就返回;多轮工具循环全在这一次调用里面。飞书那个永不新开的 session 只是说 `conversation.messages` 一直在长,但每条用户消息仍各自触发一次调用。所以「一个回合」是确定的,不需要猜。

**这个数已经在算了。** `agent.py:716` 的 `"user_line": len(self._conversation.messages)` 就是同一条线(为记历史出处而存在)。不是新引入一个概念,是把已有的线告诉省略层。

**最容易漏、也最该被判据钉死的一处:** `RequestAssembler` 按 session 持有(`agent.py:291`),活得比回合长;水位线是回合级的。必须在 `finally` 里清——异常、取消、撞满 `_max_tool_rounds` 全都要清。漏了这一笔,一个死掉的回合会把水位线永久钉在那儿,下个回合的省略范围被锁死,症状是预算慢慢失控而没人知道为什么。**这条比正常路径更重要。**

### 缺陷 A:抑制覆盖普通对话

让 `_handle_and_stream` 也传 `suppress_silent_reply=True`。抑制逻辑本体已在 `client.py:520-554`,token 定义在 `_card_markers.py` 的 `SILENT_REPLY`,不新增机制。只碰 `channel/feishu/`,与 B 并行不撞车。

### 缺陷 C:剥句柄

`strip_transfer_markers`(`history_display.py:364`)补剥句柄。正则与 `_HANDLE_PREFIX` / `ELISION_HANDLE_TEMPLATE` 保持单一来源,不在 `history_display` 里另写字面量——本仓库出过「两处正则写法不同导致静默漂移」(见 `_card_markers.py` 与 `_send_markers.py` 的注释)。

只剥**用户可见投影**这一侧。请求侧保留,那是句柄的设计用途。

### 第二半:跨回合交付物记忆

**D1 · 句柄自述已送达文件。** 改 `_handle_for`(`:519`)与模板(`:141`):被省略的行含发送标记时,在句柄里注明文件名。解码走 `_send_markers.iter_send_paths()`,不另写正则。

两条硬约束:

- **不能写成字面标记。** 渠道侧是扫模型输出流触发发送的(`channel/_markers.py` 的 `SendMarkerScanner`),而这个模型已演示过会照抄句柄(line 5874)。抄出一个真标记就会重复发文件。要渲染成不可被扫描器识别的形式。
- **句柄的字节预算是硬约束。** 模板 docstring(`:151-159`)记着:句柄每条被省略的行付一次,第一版带 ~220 字符解释,结果在本来就过大的历史上变成几十 KB 的不可省略地板,针对紧预算的运行怎么省都下不来。自述必须极短,且只在该行真含发送标记时才加。

**D2 · history_recall(handle) 取回工具。** 省略本来就是可恢复的设计(`request_assembly.py:148`:the handle names where the original still lives),缺的是模型手上那把捞的工具。历史定位复用 `_appdata.resolve_history_read_path`(`:77`,双读:优先 AppData、回退 legacy),会话 id 走 `_session_helpers.current_session_id()`。只读,防路径逃逸,返回有长度上限。

**只放 `agents/feishu/tools/`,不同时放 desktop。** 两包共用 89 个同名工具文件,本仓库出过「双包发散、上游改动只跟到一侧」,多一份副本就多一处要手工同步。本轮修的是生产飞书机器人,desktop 没这个病例。

### 否决的方案,与否决理由

**按时间开豁免窗口。** 两个理由,都是硬的:

1. **前提不成立。** 历史行上没有时间戳——`_DISPLAY_ONLY_KEYS`(`history_display.py:53`)只有 `kind`/`chat_type`/`turn_context`,`with_kind` 也不写时间。要按时间就得先给每行加字段,存量 43 MB 历史全无此字段,还要处理缺失回退。
2. **就算加上也答不对。** 时间与「送达了没有」不是一回事,且在两个方向同时会错:3 秒答完的回合任何窗口都覆盖得住,但它本来也不出事;跑了 8 分钟 49 轮的失控回合(本仓库真出过),窗口开 5 分钟前 3 分钟的产出照样被削——正是要防的那一类。窗口要多大取决于最慢回合有多慢,那是个没有上界的数。反过来开够大,刚结束的快回合也被白白豁免,纯浪费预算。

水位线不看时长:回合跑 3 秒还是 8 分钟,它都精确覆盖「这一次调用产出的行」,一条不多一条不少。

**`assistant` 整个角色退出省略**(从 `_ELIDIBLE_ROLES` 删掉)。改一行最省事,但 43 MB 历史里 assistant 占的量不小,等于自断一部分压缩能力,且没有正当理由——问题不在这个角色,在「还没送达」。

**按「用户眼里的一件事」豁免**(需求到落地算一个回合)。这个单位确实存在且省略在它里面照样会咬人,但:边界只有语义,判定要么靠模型自己说(line 6370 证明它判不准),要么靠启发式(错判则该压时不压、预算失控);且豁免区随「一件事」的长度**无上限增长**,最后等于关掉省略。而省略是 level 1、预算的唯一硬保证——关掉它就回到 2026-09-02 那个卡死状态(请求组不出来、发不出去、重启也不好)。所以这一层的答案是让被省略的内容**可捞回**(D1/D2),不是不省略。

---

## A —— 执行过程

五张 Kanban 卡,各在自己 worktree 落代码 + 写判据 + 变异复核 + 提交。全部开 auto-review commit。

| 卡 | ID | 内容 | 触碰范围 | commit | 合并点 |
|---|---|---|---|---|---|
| A | `a65e3` | NO_REPLY 抑制覆盖普通对话 | `channel/feishu/` | `9b49da5c` | `617799a1` |
| B | `bbf53` | 回合水位线 | `session/agent.py`、`request_assembly.py` | `0a2f9a54` | `f1c2c294` |
| C | `61d98` | 剥省略句柄 | `session/history_display.py` | `62cd8cab` | `910dfa85` |
| D1 | `468c7` | 句柄自述已送达文件 | `session/request_assembly.py` | `b1e09051` | `8e83baf8` |
| D2 | `fc46e` | history_recall 取回工具 | `agents/feishu/tools/` | `e4d6a210` | `342cdd79` |

五张卡新增判据合计 43 条(A 6、B 12、C 5、D1 8、D2 12)。

编排:**A 独立并行**;**B → C → D1 → D2 串行**。后四张都碰 `session/` 下同一批文件或句柄格式,本仓库踩过「并行卡各自全绿、git 不报冲突、合完炸在收集期」,不重演。依赖用 `task link` 表达(`6ae72806`/`3b931897`/`93134356`),前一张进 done 时下一张自动起。

主会话只做编排、集成、验证、交付,不自己写修复代码。

集成前核对(Kanban 的已知坑):

- worktree 的 HEAD 会**领先** task 分支 ref,只 merge 分支名会静默丢 commit——核 HEAD 不是只看分支名。
- base-ref 改了不挪已有 worktree,跑着的任务会静默基于旧 base 出活——`rev-parse` 核对。已确认 A/B 两个 worktree 的 HEAD 均为 `f9a0374e`,与集成分支同点。
- worktree 可能是没注册的空目录,那里跑 git 命令会静默落到主检出、status 还显示 clean。

### 集成实测

五张卡的 worktree 都是 **detached HEAD,没有任何 `task/` 分支 ref**。上面那条「HEAD 领先分支
ref」的坑在这里是更狠的形态:**根本没有分支名可以 merge**,而 `task done` 会删掉 worktree,
删完这两个 commit 就成了不可达对象。所以集成第一步是先把它们钉成 ref:

```
git update-ref refs/heads/card/a65e3-noreply         9b49da5c
git update-ref refs/heads/card/bbf53-watermark       0a2f9a54
git update-ref refs/heads/card/61d98-strip-handles   62cd8cab
git update-ref refs/heads/card/468c7-sent-files-note b1e09051
```

另一条实测到的坑:合并前主检出的工作区里**多出了卡 A 那两个文件的改动**——卡 A 的 agent 用
绝对路径写穿到了主检出。逐文件 `git hash-object` 与该卡 commit 里的 blob 比对后确认**逐字节
相同**,不是另一份发散的副本,直接 checkout 掉。这类污染不会让任何测试变红,只会在集成时
冒出来像是「谁改了主检出」。

还有两条实测到的坑,都会让人误判成「活丢了」或「谁改了主检出」:

- 合并前主检出的工作区里**多出了卡 A 那两个文件的改动**——卡 A 的 agent 用绝对路径写穿到了
  主检出。逐文件 `git hash-object` 与该卡 commit 里的 blob 比对后确认**逐字节相同**,不是另一份
  发散的副本,直接 checkout 掉。这类污染不会让任何测试变红。
- 卡 B 与 D1 都会**把自己的活 `git stash` 掉去量干净基线**(卡里要求做控制实验)。那段时间
  `git status` 显示 clean、`git diff` 为空,看着像 377 行全没了。worktree 与主仓库**共用同一个
  stash 栈**,`git stash list` 能看到 `WIP on (no branch): <该卡 base>`。别去动它。
- 还遇到一次相反的假象:`git status --short` 返回空、而磁盘上文件明明是改过的(`hash-object`
  与 index 不符)。是 agent 当时正持有 index(worktree 的 gitdir 里有 `AUTO_MERGE`)。隔一轮再读
  即恢复——**一次空读不能当结论**。

合并结果(全部 `--no-ff`,无冲突):

| 集成点 | 全量(`tests/`) | 与基线比 | passed 增量 |
|---|---|---|---|
| A+B | 61 failed / 1963 passed / 7 skipped | `comm` 逐条**完全一致**,新增 0、消失 0 | — |
| A+B+C | 61 failed / 1968 passed | 同上,零回归 | +5(C 的 4 条剥离 + 1 条反向) |
| A+B+C+D1 | 61 failed / 1977 passed | 同上,零回归 | +9(D1 的 8 条 + 1) |
| 五卡全部 | 61 failed / 1977 passed | 同上,零回归 | +0(见下) |

判据不只看数字对得上:每次都用 `comm -13` / `comm -23` 与那 61 条基线失败集合**逐条**比对,
新增与消失两侧都是空。三个改动文件单独跑 125 passed。

**最后一行的 +0 是个陷阱,不是 D2 没判据。** `pyproject.toml` 里 `testpaths = ["tests"]`,
默认全量**根本不收 `agents/`**,所以 D2 的 12 条判据一条都没进那个 1977。这正是本仓库
「`-o testpaths=` 与路径顺序」那条坑的另一个面孔:这次不是顺序写错,而是**默认作用域压根不含
被改的那棵树**,数字看着完全正常。补跑:

| 范围 | 集成分支 | 改动前(`f9a0374e`) | 结论 |
|---|---|---|---|
| `agents/feishu`(3241 条) | 23 failed / 3218 passed | 同 23 条(9 个文件定向复跑) | 失败集合 `comm` 逐条一致,零回归 |
| D2 自己的判据 | 12 passed | — | 五张卡的测试文件无一在那 23 条里 |

那 23 条集中在 `test_browser.py`(11)、`test_feishu.py`(3)、`test_fusion_memory_config_url.py`(2)
等 9 个文件,改动前后逐条相同,属既有基线。判定方式是**另开一个 `f9a0374e` 的 worktree** 定向
复跑那 9 个文件,而不是拿「有红」直接当回归。

五步 lint:`ruff check .` = 0、`ruff format --check .` = 0、`ty check .` = 4 条(既有
`os.killpg` 基线)、`gen_haitun_icon_png.py --check` = 0。

lint 第一次跑 `ruff check` / `ruff format` 都是**退出码 1**,失败全在未入库的本地文件
(`scripts/latency-probe/*.py`、`deploy/haitun/_patch_*.py`),`git stash push -u` 隔离后两步均为 0。
这正是「未入库文件的报错是本地噪音」那条坑的现场,不隔离就会误判成集成引入了 lint 回归。

`shellcheck .github/macos/*.sh` 本机没装(exit 127),这一步没跑;本轮未改任何 `.sh`。

编排上还有一处与预期不符:**D1 进 done 时 D2 没有自动起**,依赖链被消费掉了(`dependencies`
变空)而 D2 仍留在 backlog。手工 `task start` 后 worktree base 核对为 `8e83baf8`,含全部四个
合并,继续。链式自动起不能当作一定会发生,每一环都要核。

### D2 的 commit 是主会话代提的

卡 D2 把实现与判据都跑完了(590 行:248 行工具 + 276 行判据 + 提示词接线),然后按卡里的控制
实验要求 `git stash --include-untracked` 掉自己的活去量干净基线,**之后会话静默停住 15 分钟,
既没恢复也没提交**。这就是本仓库那条「反复出完活就停、0 commit 进 review」的失败模式,而
Kanban CLI 无法给在跑的会话发消息,没有别的办法把它叫醒。

主会话的处理:`git stash apply` 恢复出完整 590 行 → 重跑判据(该卡三个测试文件 28 passed)→
**自己做变异复核**(不采信卡自己的说法):

| 变异 | 结果 |
|---|---|
| containment 拒绝改成直接 `return path` | 2 条红(roots 越界 / `..` 先解析) |
| 去掉 `MAX_TOOL_RESULT_CHARS` 封顶 | 1 条红 |
| `role#ordinal` 识别正则失配(等于假装能捞) | 9 条红 |

→ 跑 lint(四个改动文件 ruff check / format 均为 0)→ 代为提交 `e4d6a210`,并在 commit message
里写明此事与「**卡自己声称的全量数字不存在**」。

这里有一个查证过程值得记:恢复前 `git stash show --stat` 只显示 66 行 6 个文件,而工具本体
238 行和判据文件都是 untracked、已从磁盘消失,看着像丢了一半。实际是 **`git stash show --stat`
默认不列 untracked 条目**,加 `--include-untracked` 后显示完整的 590 行 8 个文件。判「活丢了」
之前必须用这个参数复核。

### 与 main 合并:基线整体挪位,旧数字一条都不能复用

PR 开出去之后 main 前进了 11 个 commit,GitHub 报 3 个文件冲突,全在 session 层——因为
`888b1b1f`(压缩移出会话锁,改了 `agent.py` 785 行)与 `f2b5ed73`(Stop 撤回时剥离 history)
动的正是卡 B 包 `try/finally` 的那个回合循环。

**这里的头号陷阱不是解冲突,是拿旧基线判新代码。** main 自己带进来几十条判据,失败集合与
总数都变了;沿用本文上面那个「61 failed」会直接读出「64 - 61 = 涨了 3 条,回归」的错误结论。
正确做法是在 `c76a2962` 上重新起一棵工作树量基线,再逐条比对:

| 范围 | 合并后 | 新 main 基线 | 判定 |
|---|---|---|---|
| `tests/` | 64 failed / 1990 passed | 64 failed / 1958 passed | `comm` 两侧皆空,零回归 |
| `agents/feishu` | 43 failed / 3251 passed | 43 failed / 3237 passed | `comm` 两侧皆空,零回归 |

passed 增量 32 与 14 分别对上各卡新增判据数。合并后五张卡的 159 条判据全绿,
`ty check .` 仍为 4 条 `os.killpg` 基线诊断,ruff 两步 0。

**三处冲突只有一处是真的语义冲突:**

1. **回合循环的 `try`(真冲突)。** 两侧各自加了 `try` 但目的不同:本分支要
   `finally: end_turn()` 清水位线,main 要 `except BaseException:
   _abandon_incomplete_turn(turn_start)` 撤回早提交的 user 行。两者可共存,按
   `except` 在前、`finally` 在后合成同一个 `try`。**关键是两个索引不是同一个量**:
   main 的 `turn_start` 在 `conversation.add(user)` **之前**取,本分支的水位线在
   `commit()` **之后**取。混用会让撤回多删或少删一行,因此各自保留、各自命名。
2. **`tool_defs` 与压缩调用(纯 main 侧)。** TMPFIX-M2 工具闸门、`_maybe_compact` 改成
   `_request_compaction` 只记录,本分支未碰,取 main。
3. **两个文件的 import 块(机械冲突)。** 两侧各加不同 import,都保留;合并会产生重复的
   `project_history_with_sources`,去重并按 `ruff --select I` 排序。

`request_assembly.py` 上 main 的改动(token 上限收归 `psi_agent.protocol`)与省略逻辑正交,
`_turn_watermark` / `begin_turn` / `end_turn` / `exempt` 全部原样存活。

**一个差点得出错误结论的判据操作,记此备考:** 用 `grep -c "test_history_recall"` 在 `-q`
输出里数测试名,得 0,看着像 D2 判据又没被收集(正是本文上面记过的 testpaths 坑的形状)。
实际是 **`-q` 模式只打点不打通过用例的名字**,`grep` 数不到属正常。改用 `--collect-only`
才看清:该文件被收集 13 处,且 3251 + 43 = 3294 与收集总数吻合。判据没中和判据没跑是两件
事,别用一个数不出来的量法去判后者。

云端 CI 在合并提交上全绿(lint / test / antlr / feishu-web / 三平台 pyinstaller / 两个安装包),
GitHub 状态 `MERGEABLE`、`CLEAN`。

---

## T —— 测试与验收

### 判据本身的质量要求

- **每条判据都要能变红。** 写完做变异复核:故意改坏实现,断言用例真的失败。等价变异或打不中的变异**如实记下来**,不要假装覆盖了。
- **判据必须落在它声称的那一层。** 出过这种事:docstring 说测 AI 层、实际调的是 Session 层函数,于是 AI 层重新引入硬编码字面量它照样绿,变异复核也照不出来。
- **一个分支撞多次兜底会提前吃掉结论**,让用例看着绿其实没吃劲。补断言必须做变异复核。

### 跑测试的坑(都会静默通过,不报错)

- **worktree 里跑 pytest 必须带 `PYTHONPATH=src`**,否则测的是主 checkout 的 src,数字全作废还会伪造出回归。
- **`-o testpaths=` 必须写在路径之前:**
  - 对:`pytest -o testpaths= tests/xxx/test_a.py -q`
  - 错:`pytest tests/xxx/test_a.py -o testpaths= -q` ← 静默跑全量,不报错

  唯一露头迹象是进度百分比刻度不对。这个坑已踩过两次。
- **失败基线不为零**,全量在 **57-62** 之间浮动(Windows 上 asyncio 子进程 + 硬编码管道名被残留进程占着)。判定回归前必须做控制实验:`git stash` 掉改动、确认 stash 真生效(`git diff --stat` 那几个路径为空)、再跑一遍对比**失败集合**而非只看数字。
- **CI 的 lint 有五步且 test 门控于它**,作用域是 `.` 不是 `src tests`:

  ```
  ruff check .  /  ruff format --check .  /  ty check .
  shellcheck .github/macos/*.sh  /  gen_haitun_icon_png.py --check
  ```

  `ty check` 的基线是 **4 条 `os.killpg` 诊断**,不是 0。未入库文件的报错是本地噪音,用 `git stash push -u` 隔离掉再量。
- **lint 结果看退出码**,不要读还在缓冲的 tail 输出就下结论。读 tail 报过全绿、CI 随即变红。

### 本轮明确没做、留待后续

- **跨回合交付物记忆只做到「可捞回」,没做到「模型一定会去捞」。** D2 的触发压在模型自己起意,而 line 6370 证明这个模型会把句柄当真实内容读——看不懂句柄的模型不会想到去捞。D1 的自述是被动生效的,不依赖起意,这是它优先于 D2 的原因。这一层的完整答案(交付物清单、或压缩层保住语义)不在本轮。
- **`role#ordinal` 句柄跨进程失效。** 该形态来自 `id(source) % 1_000_000`,是进程内内存地址派生的,重启后对不上。D2 要在报错信息里如实说明,不假装能捞到。
- **未上生产。** 本轮只到 PR。

### 上生产时的约束(本轮不执行,记此备考)

- 改容器文件**只能 `docker cp` + `docker restart`**,绝不能 `docker compose up -d`——`/app/src` 在镜像里不是挂载,`up -d` 会重建容器并静默丢掉所有改动。
- 生产有多份同名文件(`workspace/` vs 容器内 vs build 机),改完必须核 **md5** 确认改的是真正被加载的那份。
- **不要单条上生产。**
