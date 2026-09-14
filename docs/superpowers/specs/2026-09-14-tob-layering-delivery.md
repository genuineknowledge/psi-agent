# ToB 内容分层（方案 B）· 最终交付报告

> 面向负责人。结论在最前面，细节在后面。
> 生产逐条操作记录在 [`2026-09-12-tob-layering-production-oplog.md`](2026-09-12-tob-layering-production-oplog.md)，本文不重复抄。

---

## 一、结论先行

**分层已在生产生效并承载真实流量；剩下三步被网络阻断，与本方案无关。**

| 项 | 状态 |
| --- | --- |
| 三层内容模型（official / enterprise / users） | ✅ 已生效。`layers_for('skills')` 返回 4 层，就近覆盖用**两个真正不同的文件**验过 |
| 工具暴露按层声明、删掉 M2 硬编码门 | ✅ 已落地，48 条判据 + 8 个变异旋钮 |
| A1 私有模块隔离进内核 | ✅ 已落地，44 条判据 + 9 个变异旋钮 |
| 飞书 API 护栏 | ✅ **本次修复**。修复前 195 条规则**一条都不生效**，详见第三节 |
| 4 处被分层断掉的硬编码 `skills` 路径 | ✅ **本次修复**（PR #956，CI 全绿）。其中 `meeting_pipeline_run.py` 那条是**当前正断着**的每日会议分析；15 条判据 + 6 个变异。合并期与 #955 撞车，`git` 零冲突却留悬空引用，已连带修好并补 3 条变异。生产效果**还没量**（U13/U15） |
| 第 5 处断链：`card-dsl` 模板只看得见 1 个（PR #960） | ✅ **本次修复**。**生产上正断着**：`/workspace` 那层的模板目录存在但只有 1 个文件，解析就此终止，`/content/official` 下的 `meeting-summary-card` / `review-card` / `todo-card` 全部看不见——牵连会议总结卡、`feishu_card_render`、台账对账、评价卡。7 条判据 + 5 个变异。生产效果**还没量**（U16） |
| 可复用的标准部署流程 | ✅ 已交付脚本 + 章节，**已在生产真机首跑**（`root@47.100.84.197`，基准 `ef3cad55`）：同 205 / 落后 32 / 领先 3 / 缺失 1 / 独有 0，EXIT=1 |
| 完整部署闭环（剩 32 个落后文件 + 1 个缺失 + 两份私有 workspace） | ⏸ **等归属决定**：3 个"领先"文件里 `tencent_meeting.py` 今天 14:19 还被人就地改过，覆盖会踩掉别人的活 |
| 分支与 main 同步、单 PR | ✅ 已合并 `origin/main`（4 处冲突逐个核过取舍），全量回归 **21 failed / 1120 passed，与干净 main worktree 逐条相同**；CI 五步除 `ty check` 外全绿，`ty` 的 4 个诊断在 main 上同数（全是 Windows 无 `os.killpg`） |

**最值得知道的一件事**：这次最严重的问题不是代码写错，而是**漏投了一个文件**。9-12 投放时
`_feishu_spec.py`（新版）投上去了、调用它的 `_feishu_api_impl.py`（旧版）没投，于是 195 条
飞书 API 护栏规则全部失效——该拒的调用一律放行，而表面功能完全正常，唯一线索是一行 INFO 日志。
`origin/main` 里的代码一直是对的。

所以本次交付里权重最高的不是代码，是那个**审计脚本**：它把"漏投"从没有判据变成有判据。

**第二件同源的事**：这次一共踩到三处"看着全绿、其实断了"——漏投一个文件让 195 条护栏静默失效；
分层挪走技能目录让 4 个工具的写死路径断链（其中一处生产上正在报错）；并行改名与别人新加的函数
撞车，`git merge` 零冲突却留下悬空引用。三处的共同点是**故障不在它声称的那一层报警**：护栏只留
一行 INFO、`flow_run` 静默换引擎、悬空引用只有 lint 抓到。补判据的重点因此不是"多写测试"，而是
让判据落在故障真正发生的那一层。

**第四处，形态又不一样**：查 #867 耦合时顺手核出 `card-dsl` 的模板解析也断了（PR #960）。前
三处能靠"目录存不存在"这类判据抓，这处**目录在、只是内容不全**——有人往 `/workspace` 那层放了
一个模板，`isdir` 为真，解析就此终止，同层缺的另外三个模板再也没机会被下一层补上。教训是：
兜底链的终止条件要落在**最终要用的那个东西**（文件）上，不能落在它的容器（目录）上。写判据时
也踩了对应的一刀——模板名先用了仓库里真实存在的 `review-card`，被 `__file__` 兜底那层悄悄接住，
判据假绿；换成仓库里不存在的名字才真的吃劲。

---

## 二、生产当前状态（2026-09-14 实测）

| 项 | 值 |
| --- | --- |
| 容器 | 3 个跑 agent：`gateway` / `private-luolin` / `private-chengxx`（另有 oauth-proxy 共享 gateway netns、不跑 agent） |
| 镜像 | 三容器均 `4720dace` |
| `PSI_CONTENT_ROOTS` | 在位，gateway = `official=/content/official:enterprise=/content/enterprise:users=/content/users` |
| 工具数 | gateway 229 / luolin 198 / chengxx 198 |
| 加载失败数 | 2 / 4 / 4 |
| 稳定性 | 37 小时 uptime，无新 OOM（swap 已在用 2047M/4095M） |
| `vm.swappiness` | 10，且**已持久化**（原先只改了运行时，重启会回到 0） |

### 与远程 main 的差异（`origin/main` = `ef3cad55`，241 个 `.py`）

| workspace | 同 | 落后 | 领先 | 缺失 | 生产独有 |
| --- | --- | --- | --- | --- | --- |
| `workspace/`（gateway） | 205 | 32 | 3 | 1 | 0 |
| `workspace-luolin/` | — | — | — | **73** | 6 |
| `workspace-chengxx/` | — | — | — | **71** | 10 |

三个要点：

1. **漂移是历史欠账，不是这一天的空窗造成的。** 所有漂移文件的 mtime 落在 9-07（33 个）/
   9-11（1 个）/ 9-12（2 个），**9-13、9-14 一个都没有**。空窗期写入的 61 个文件全是用户数据。
2. **两份私有 workspace 是 8-07 的旧快照，不是 gateway 的副本。** 它们只能整份铺平，不能增量
   补——9-12 实测：补一条依赖链会连带换新 `_feishu_impl.py`，而旧 `_feishu/bitable.py` 让
   40+ 文件立刻断链，工具数从 198 掉到 87（已完整回滚）。
3. **"生产独有"不是脏文件**，是 git 里已经删掉的文件（`browser_cdp.py`、`feishu_calendar.py` 等）。

---

## 三、本次修的问题：飞书 API 护栏 195 条规则全部失效

### 现象

`POST /open-apis/im/v1/messages` 的护栏 = `None`。探针输出 `0 from 0 of 1 roots [(none)]`。

### 根因

**不是代码缺陷，是漏投文件。** 生产 `_feishu_api_impl.py` 是 434 行的旧版，仍调单目录接口：

```python
rule = _spec.rules_for(_skills_dir(), verb, path)      # 旧: 只看 agent 那一层
```

而 `_skills_dir()` 指向的 `/workspace/skills` 在方案 C 腾名后**已经不存在**了，于是规则数为 0。

`origin/main` 的 473 行版本早已改对：

```python
rule = _spec.rules_for_layers(_skills_ladder(), verb, path)   # 新: 走整条层梯
```

`_skills_ladder()` 的 docstring 把风险说得很准：*"skills 目录有两个消费者：模型读的技能索引，
和这里执行的飞书 API 护栏。只让前者分层会让每人的覆盖看起来生效，而真实 API 调用仍只受官方
规则约束——静默，且朝最贵的方向错（该拒的没拒）。"*

同目录的 `_feishu_spec.py` 生产上**已经是新版**（683 行，`rules_for_layers` 就在里面）。
一新一旧凑在一起，正是 9-12 投放漏了一个文件。

### 修法与判据

投放 main 的 473 行版本到三份 workspace（md5 `64ad81caf9e8` ×3）。**重启前先在容器内 import
探一次**——这是动作 11 那次回滚教的。

判据吃劲，不是"跑通就算"：

- 探针：`0 from 0 of 1 roots [(none)]` → `195 from 3 of 4 roots [official=195 enterprise=0 agent=0]`
- 真实调用 `call_api_impl('POST','/open-apis/im/v1/messages')` 被**拦下**，返回
  `{'ok': False, 'code': 'use_dedicated_tool', 'tool': 'feishu_message_send'}` 并附业务原因
  （私聊内容会漏进群）。修复前同一调用是放行的。
- 工具数 229/198/198 未退，失败数 2/4/4 未增。

---

## 四、顺带查清的两件事

### "写落点悬空"这个担心不成立

`writable_layer(kind)` 是**实写探针**：建一个临时文件再删。目录不存在时它先建——因为可写层
第一次被写入时它本来就不存在，"建不出来"本身就是不可写的一种。所以它返回 `agent` 就意味着
那一层**确实可写**，不是指向一个不存在的目录。

代价是我这次跑探针**在生产上建出了一个空目录** `/srv/haitun/psi-agent/workspace/skills`
（11:28:36）。无害，已记为台账动作 13，回滚是 `rmdir`。

### swappiness 是个定时炸弹，已拆

运行时是 10，但 `/etc/sysctl.d/99-apsara-sysctl.conf` 显式声明 `vm.swappiness = 0`，
**重启就回到 0，OOM 立刻回来**。已新建 `zz-` 前缀文件（sysctl.d 按文件名字典序加载，`zz-`
在 `99-apsara` 之后）。判据是 `sysctl --system` 输出显示先 apsara 应用 0、后 zz- 应用 10、
运行时终值 10——那个顺序就是重启时的加载顺序。

---

## 五、可复用的标准部署流程

### 为什么要新增一章

`deploy/haitun/README.md` 原先只覆盖两类东西：镜像里的构建资产、目标机上的单个脚本。
而 `workspace*/tools/` 那 241 个业务文件属于**第三类**——bind mount 进容器、不在镜像里、
换镜像发布完全不会更新、靠人手投放、**没有任何构建闸门**。这次出事的正是这一类，而文档里
一个字都没有。

### 交付物

| 文件 | 作用 |
| --- | --- |
| `deploy/haitun/audit-workspace-drift.sh` | 投放前后各跑一次，把差异分成四类。**这就是判据本身**，不是文档里一串靠人记的命令 |
| `deploy/haitun/README.md` 新章「`workspace/tools/` 的投放」 | 硬规则 6 条 + 三份 workspace 的现状表 + 已知没验到的 |

### 四类差异，混成一个数字就再也分不开

| 类别 | 判法 | 处置 |
| --- | --- | --- |
| 同 | md5 相等（LF 归一化后） | — |
| 落后 | 与 git 不同，mtime **整秒** | 投放留下的旧版，可安全覆盖 |
| 领先 | 与 git 不同，mtime **带纳秒** | ⚠️ 有人就地写过，**覆盖即丢代码**，必须人工定归属 |
| 缺失 | git 有生产没有 | 补投 |
| 生产独有 | 生产有 git 没有 | 通常是 git 已删，确认后删 |

有"领先"文件时脚本**退出码 1**，让调用方停下而不是继续覆盖。

**"落后"和"领先"必须分开，一键同步的全部危险就落在这一个区分上。** 存在生产领先于 git 的
真实文件：`_card_dsl.py` 是未合并的 PR #867（fork `Twin-Ghosts`）的代码再往前改出来的，
生产是那个 PR 的**超集**。当成"旧版"覆盖会静默丢掉 607 行功能（原生 table 渲染、
`bind-field` 回写、`action_id` 撞车防护）。

mtime 纳秒位这个判据来自 9-12 的取证：投放（`cp -p`/tar 保留源 mtime，或 CI 产物）落在整秒，
就地编辑落在带纳秒的时刻。uid 不是判据——两种情况都可能是 root。

### 脚本的判据（变异复核，2026-09-14）

在一棵人造树上做的，不是"跑通了"：

- 全同树报 241/241、退出 0
- 分别造出落后 / 领先 / **子目录里的**缺失 / 生产独有各一个 → 四类被各自单独认出，退出码变 1
- 再把一个文件整体转成 CRLF → 仍报"同"，归一化没有产生假阳性

### 写进硬规则的 6 个坑（每条都是这次实测踩到的）

1. **清单要走全树，不能用顶层 glob。** `_feishu/` 私有子目录下有文件，顶层 glob 漏掉 6 个，
   其中 3 个的缺失直接让工具加载失败。
2. **md5 比对前先 LF 归一化。** 生产 LF、仓库检出 CRLF，裸比对报几乎全不一致，已因此误判过一次。
3. **重启前先在容器内 import 探一次。** 拷文件时不报错，断链要到重启加载工具才暴露，而那时
   旧进程已经没了。
4. **`docker compose` 收 service 名不是容器名。** 传容器名报 `no such service`——而如果顺手把
   输出重定向掉，看起来就像重启成功了（我踩过）。
5. **`restart` 不换镜像。** 换 tag 要 `up -d`（改 workspace 内容时绝不能用）；`oauth-proxy` 用
   `network_mode: "service:gateway"`，gateway 重建后必须跟着 restart，否则挂死 netns、公网静默 502。
6. **数失败条数要按容器本次启动去重。** gateway 多会话，每会话重扫一遍工具，`grep -c` 会按会话
   数翻倍（实测 106 = 2 类报错 × 约 40 会话）。

---

## 五之二、顺手销账的 3 个环路，以及一条判据差点指错方向

合并 main 时 `_feishu/` 的导入环路测试报了红。这里值得记一笔，因为**判据差点让我修错东西**。

A1 那条测试拿一份硬编码清单当"裸名基线"，做的是**等值**断言。清单漏了模块，它报出来的是
"A1 下成环、裸名基线不成环 —— **A1 引入了新环路**"。一句很自信的话，方向完全错。

实测裸名基线：那几个模块**同样成环**。A1 什么都没弄坏，是清单没跟上代码。

不过这不是清单的缺陷——它是**故意**让新模块默认红的（`strict=True`），这次是该机制第一次真的
抓到东西。所以正确处置是销账，不是修代码。三个模块逐个量过归因，**它们不是同一种**：

| 模块 | 性质 | 报错指向 |
| --- | --- | --- |
| `todo_sop` / `strike` | 与既有 12 个同类：被 `_feishu_impl` re-export 后又反向 import 它 | 自己 |
| `pm_send` | **自己不成环**，是 `message` 既有环路的下游受害者；`_feishu_impl` 不从它 re-export | `_feishu.message` |

这个区分写进了注释，因为将来解开 `message` 的环路时，`pm_send` 应当跟着一起出清单，而不是
被当成第 15 个独立环路去查。

变异复核：往两份清单塞一个确实不成环的 `ledger_schema`，两处各转一条红（还含
`XPASS(strict)`，说明 xfail 那侧也吃劲）；还原后 107 passed / 15 xfailed。

---

## 六、剩下没做完的（逐项交代）

### ⚠ 更正：我此前记的"被网络硬阻断"是错的，量的是另一台机器

我用了 `~/.ssh/config` 里的 `haitun1` 别名，它带 `ProxyJump jump` → 内网 `192.168.63.174`。
那台机器有 docker 但**0 个容器、没有 `/srv/haitun`**，是实验室内网机，不是 ToB 生产。跳板机
TCP/22 那时确实不通，于是我把"这一跳断了"错读成"生产不可达"，还写进了台账、本文档和已合并
的 PR #953 正文。

**ToB 生产是云服务器，直连 `root@47.100.84.197`，不需要跳板机**，整个期间它一直是健康的。

判据（动手前先跑，否则 `Connection timed out` 和生产真的挂了长得一模一样）：

```bash
ssh root@47.100.84.197 'ls -d /srv/haitun && docker ps -q | wc -l'   # 生产 = 9 个容器
```

### 还没做完的三步

| # | 事 | 现状 / 怎么做 |
| --- | --- | --- |
| U8 | 审计脚本在真机跑一次 | ✅ **已跑**，并因此查出脚本自己的一处判据缺陷（见下）。改完复跑：同 205 / 落后 32 / 领先 3 / 缺失 1 / 独有 0，EXIT=1 |
| U9 | 两份私有 workspace 整份铺平 | 需要停机窗；铺平后逐项比对工具数与失败数 |
| U10 | 覆盖 32 个"落后"文件 + 补 1 个缺失 `_meeting_card.py` | 等 3 个"领先"文件的归属决定后再投放，投放后跑重启前 import 探针 |

#### 真机首跑查出脚本自己判错了一个文件（已修）

首跑输出 31 落后 / 4 领先，与我手工量的 32 / 3 差一个 `meeting_pipeline_run.py`。查下去不是
生产变了，**是脚本的判据本身不成立**：

原判据用 mtime 纳秒位区分"落后"与"领先"（整秒 = 投放，带纳秒 = 就地写入）。但**不带 `-p` 的
`cp` 会把 mtime 设成"此刻"，而此刻天然带纳秒** —— 手工投放与就地编辑在 mtime 上根本无法区分。
`meeting_pipeline_run.py` 带纳秒，于是被判成"领先"、脚本拒绝覆盖；而它的内容与 commit
`880d9831` 逐字节相同，其实是**落后** 5 天。

改成内容判据：`git hash-object <生产文件>` 得到 blob SHA，`git cat-file -e` 查这个 blob 在不在
仓库里。在，说明这份内容是某个 commit 里的版本，覆盖它不丢任何未进 git 的代码；不在，才是真
"领先"。前提是仓库 blob 与生产文件都是 LF（已核 `.gitattributes` 与 blob 内容）。

同时修了第二个坑：脚本原来按 glob 顺序取第一个找到的 clone，而目标机上 `/tmp/rel-482d970c`
只有 729 个 commit、`/tmp/rel-5565f4bd` 有 3133 个 —— 取到残缺那份会把"落后"误判成"领先"。
改为取历史最全的那个。

变异复核（本地人造树，241 文件）：塞入仓库存在过的旧版本 + 纳秒 mtime → 判"落后"✅；塞入从未
进 git 的内容 → 判"领先"✅；**整秒 mtime 但内容不在 git → 仍判"领先"**✅（这条旧判据会误判成
"落后"并静默覆盖）。干净树 241 同 / EXIT 0，整份转 CRLF 后仍 241 同（归一化吃劲）。

### 需要人来决定的

- **要不要给 CI 加一个 pytest job**（建议加，范围卡死）：目前 CI 五步全是 lint 类，**没有任何 job
  跑 pytest**。后果这次撞见了一个具体例子：`test_read_rejects_person_name_filter_without_identity`
  在 `origin/main` 上就是红的，没人知道，因为没有东西会去跑它。

  这件事有代价，所以是你的决定而不是我的：全量回归当前 21 failed（Windows 上 57–62 浮动），
  一挂上去就等于把"修全量"这笔债背上。折中做法是**只跑指定子树**（如
  `tests/agents/feishu/`），范围写死在 workflow 里，把新增的红拦住而不追既有的。

- **3 个"领先"文件的归属**（内容不在 git 任何 commit 里，覆盖即丢代码）：

  | 文件 | 时间 | 是什么 |
  | --- | --- | --- |
  | `tencent_meeting.py` | 9-14 14:19 | **是给分层打的适配补丁**，见下 |
  | `_card_dsl.py` / `_rookie_sop_card.py` | 9-12 17:44 | 源头是未合并的 PR #867 |

  PR #867 那两个要 PR 作者判断是把生产那份收编进仓库还是别的，**不是我该拍的**。

- **`tencent_meeting.py` 这份要尽快收编进仓库，而且它顺带丢了一个超时保护**：

  它加了一个 `_skill_script()`，改从 `PSI_CONTENT_ROOTS` 逐层找技能脚本，legacy 路径垫底。
  这是分层的**直接后果**：分层把技能挪出 `<workspace>/skills` 后，这个工具原来硬编码的
  `parent.parent/"skills"/...` 就断了。`_skill_script` 在 git 里**一处都搜不到**，只活在生产上，
  下次镜像发布或批量投放就会把它冲掉、把工具重新弄坏。

  但同一份改动里，PR #859（9-08）加的 `anyio.fail_after(call_timeout)` 与
  `TENCENT_MEETING_CALL_TIMEOUT` **不见了**（生产那份 `grep -c` 得 0）。那个保护正是为了防止上游
  挂起时把会议 cron 永久阻塞。所以收编时应当是**两者都要**，不是二选一。

  已实测确认这三个硬编码 `<workspace>/skills` 路径的工具在生产上现在都能用（有人在 14:21–14:33
  把它们各自需要的技能软链回了 `workspace/skills`，容器内 4 条链全解析）：

  | 工具 | 实测结果 |
  | --- | --- |
  | `tencent_meeting.py` | `_skill_script()` → `/content/official/skills/tencent-meeting-mcp/scripts/tencent_meeting.py`，`exists=True` |
  | `_positive_negative_list/rules.py` | `load_rule_pack()` 得 16 条，`validate_rule_pack()` 通过，`query_rules("红线")` 2 命中 |
  | `_gen_mcp_skill.py` | `SKILLS` → `/workspace/skills`，`is_dir=True` |

  这 4 条软链同样不在 git 里，属于同一类"只活在生产上的修复"。

#### ⚠ 上面这份清单是错的，而且它掩盖了一处正在发生的故障（2026-09-14 17:0x 纠正）

  全量清点了一遍 `agents/feishu/tools/` 里所有自己拼 `"skills"` 路径的地方，结论与上表有三处不符：

  1. **真断的是 4 处，不是 3 处**，上表漏了两个：`meeting_pipeline_run.py:409`（SOP 技能）与
     `flow_run.py:98`（`fusion-flow-legacy/.env`）。
  2. **`_gen_mcp_skill.py` 不是缺陷**，我把它算进来是错的。它下划线前缀、不进工具扫描，是仓库内
     的 dev CLI，产物 `skills/*-mcp/SKILL.md` 已入 git，`TOOLS.parent / "skills"` 在仓库检出里
     本来就对。上表那行 `is_dir=True` 量到的是生产上那个恰好存在的目录，不说明任何问题。
  3. **每日会议分析当前是断的。** 容器内实测：

     ```
     MISSING /workspace/skills/meeting-sop/weekday-alignment/SKILL.md
     /content/official/skills/meeting-sop/weekday-alignment
     ```

     14:21 补的 4 条软链里没有 `meeting-sop` —— 补的人只补了自己撞见的那几个。上表「三个工具
     都能用」这句话本身没错，错在它给人的印象是这一类问题已经被兜住了。

  这也说明**软链接不是修复**：它没有就近覆盖语义（企业层/用户层改不动被链过去的官方规则），
  且下次加技能还得有人记得补。4 处已在 PR #956 里改成走内容层梯子，各自的断法不同、因此各有
  独立判据：`meeting_pipeline_run` 显式抛错、`flow_run` **静默**退回默认引擎 `claude`、
  `run_flow` 在 import 期 `ImportError`、`rules.py` 抛 `unknown rule pack version`（看着像调用方
  版本号写错）。15 条判据 + 6 个变异全部如期转红；生产上的效果**还没量**（见台账 U13/U14）。

#### 这 4 处里的 `rules.py` 在合并期又撞了一次（2026-09-14 18:3x）

  #956 的 CI 出现"同一个 SHA、两个 lint、一绿一红"。红的是 `pull_request` 事件 —— 它检出的是
  **与 main 的合并结果**，而 `push` 事件检出分支本身。所以本机怎么跑都绿：本机没合 main。

  真因是并行改动撞车。main 上 PR #955 在 `rules.py` 里新增了 `rule_pack_source()`（规则包指纹
  工具）并引用常量 `_CONFIG_DIR`，而 #956 为跨层解析把该名改成了 `_LEGACY_CONFIG_DIR` +
  `_config_dirs()`。两处落在同一文件的不同区域，**`git merge` 干净通过、零冲突**，合并后留下一处
  悬空引用，只有 `ruff` 的 F821 抓到。

  **修法不是把名字换回去。** 指纹的用处是给 agent 提供"我确实读了这份文件"的证据。若
  `rule_pack_source` 与 `load_rule_pack` 各自解析路径，分层后两者可能落到**不同层的同名**
  `<version>.yaml` 上 —— 指纹就为一份没被读过的文件作保，而这种伪证恰好长得像有据。所以抽了公共
  `_rule_pack_path(version)` 两处共用，让偏斜在结构上不可能出现；另补 `layer` 字段报命中层名
  （`file` 保持相对短路径不变，但分层后每层都有同名文件，光凭它定不到读的是哪一份）。3 条变异
  （`layer` 写死 / 指纹绕开层梯子 / 层序取反）全部如期转红。生产复量见台账 U15。

  **这是同一形态的第二次**（第一次是并行卡的跨文件改名）。可复用的判据：在多人并行改的文件里改
  常量或函数名，改完要在**合并后的树**上 grep 老名字，别只在自己分支上 grep。

  顺带查明一条**既有红**：`test_read_rejects_person_name_filter_without_identity` 在
  `origin/main` 干净 worktree 上同样失败，与本次改动无关。它一直没被拦下，因为 **CI 里没有任何
  job 跑 pytest** —— 这也是下面「需要人来决定的」里那条建议的由来。
- **`docker-compose.yml` 至今不在 git 里**：分层配置目前只靠机器上一个 `.bak` 文件保着。
  建议收进 `deploy/haitun/` 作为准本。

### 其他未验证

- **U2 分层代码在真实生产镜像里的完整行为**：全部单元判据都是本地 rig 跑的。
- **V4（真实提问验证覆盖顺序）、V7（真实飞书收发）没跑。**
- enterprise 层内容不完整（`card-dsl/` 只有 `card.xsd`，缺 `SKILL.md`）；users 层是空的。
- 台账动作 13 留下的空目录 `/srv/haitun/psi-agent/workspace/skills` 还在（无害，`rmdir` 即可）。

---

## 七、这次纠正过的自己的两处记录

写在这里是因为它们都曾指向错误的行动方向。

1. **"三处生产手改"是错的。** `meeting_pipeline_run.py` 与 commit `880d9831`（9-07）逐字节
   相同——生产是**落后** 5 天，不是被人手改过。更糟的是我 9-12 还把它拷到了两台私有机器上，
   等于在扩散一个旧版本。
2. **"刻意收窄词表"是错的。** `_card_dsl.py` 生产**领先**于仓库（多 607 行），来自未合并的
   PR #867。我一度还进一步说"生产包含 PR 的全部内容"，这也不准：有 52 行是 PR 独有的，
   但那些是被重写过的行的旧形态，不是 PR 独有的功能。
