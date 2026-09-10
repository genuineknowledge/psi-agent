# ToB 搬家与内容分层 · 两个会话的初始 prompt

2026-09-08 · 配套 [排期文档](2026-09-08-tob-migration-and-content-layering-plan.md)

## 结论

两份可直接复制的初始 prompt。**两轮都只做头脑风暴与方案验证,不动手执行** ——
方案跟负责人对过一遍才能开始做。

两份 prompt 都写死了三件事:各自的硬纪律、已实测的事实(避免重复探查)、
以及**与另一条线的边界**(对方在干什么、哪些目录不许碰)。

边界必须写死的原因:上次 Kanban 并行卡各自不知道对方在改什么,各自全绿、
git 不报冲突,合完炸在收集期,40 条判据一条都跑不了。这次两条线一个在服务器
一个在仓库,冲突面本来小,但会话 2 内部和实习生的 ToC **都动 `agent.py:364`**,
这条得说死。

## 冻结基线的位置

收债基线已从 `/tmp` 迁到 durable 位置(`/tmp` 会被并行后台任务共享和清理,
而会话 2 要长期用它):

```
F:\code\_tob-claim-baseline-20260908\content.tgz    49 文件 / 154378 字节
                                                    md5 43b7f51811df6a47aa343dca0730ea97
F:\code\_tob-claim-baseline-20260908\MANIFEST.md5   逐文件 md5,49 行
```

搬运后 md5 已复核未变。

## 会话 1|搬家 + 生产清理

````text
你负责 psi-agent 的 ToB 生产搬迁:从境外服务器 B 搬回境内服务器 A。仓库在 F:\code\psi-agent,分支 main。

【本轮任务】头脑风暴验证方案,不要动手执行。方案跟我对过一遍才能开始做。

先读排期文档:docs/superpowers/specs/2026-09-08-tob-migration-and-content-layering-plan.md
它的"会话 1"那一节就是你的活。再读 docs/haitun-delivery/04-move-back-to-mainland.md(原始 runbook,注意它写于 9-04,有多处已被实测推翻,推翻清单在排期文档里)。docs/haitun-delivery/ 不在 git 里,别提交它。

机器:B = 8.222.255.23(境外,现生产,7 容器)、A = 47.100.84.197(境内,目标,空机)。ssh root 免密。

【硬纪律 · 不可违反】
- 不要直接动生产。任何在服务器上产生副作用的操作,先告诉我你要做什么、我确认了再做。只读探查(docker ps、cat 配置、df、free 之类)可以直接做。
- 改生产容器内文件只能用 docker cp + docker restart,绝不能用 docker compose up -d —— /app/src 在镜像里不是挂载,up -d 会重建容器并静默丢掉所有改动。
- 生产上有多份同名文件(workspace/ vs 容器内 vs build 机)。改完必须核 md5 确认改的是真正被加载的那份。md5 比对前必须做 LF 归一化 —— 生产是 LF、仓库是 CRLF,裸比对会报几乎全都不一致,出过一次错误判断。
- 镜像要做三层核验:build 机的 src / 镜像内的产物 / 容器内实际加载的。第三层是 8-18 那次事故缺的那层。
- 搬迁是搬 IP 不搬域名。account.genuineknowledge.cn 已硬编码进已发布的 ToC 客户端,只能改 DNS 指向。

【已实测的事实,别重新踩】
- 用 docker save/load 搬镜像,不在 A 机重建。两个理由:生产有 6 个 src 文件领先 main(重建会静默丢掉 meeting-session 功能);A 机连不上 registry-1.docker.io,所以境内反而必须开镜像加速器 —— 与 04 文档写的方向相反。
- B↔A 互相 ssh 不通(B 的 /root/.ssh/ 只有 authorized_keys、无私钥),但 B→A TCP 22 开放,rsync 两侧都有。只缺一个密钥对。
- 本地工作站中转不可行:从 B 拉 20MB 超时 6m40s。
- 数据量与判据:workspace 2.6G/38747 文件、workspace-luolin 218M/1314、workspace-chengxx 58M/1129。
- B 机 dmesg 28 次 OOM(最近 9-08 09:40),gateway RestartCount=7,OOMKilled=false —— 子进程被杀的陷阱,所以这类故障零告警。A 机必须加 swap。
- DNS TTL 现在仍是 599s(04 文档说"需降到 300s"是旧的)。
- 阿里云对未备案域名的 403 管控已消失(境外打境内 80 得 301)。但我只证明了 403 不在,没在控制台确认备案号 —— 属间接证据,你可以提醒我去确认。
- psi-agent 入口是 lark.oauth.genuineknowledge.cn(→ 8090 oauth-proxy),不是 account.*(那是 ToC 的 psi-cloud)。
- 单条 restart gateway 会让 oauth-proxy 挂在死 netns 上(曾静默 502 了 29 小时)。Caddy 用 caddy reload,systemctl restart caddy 会中断 ToC。
- FUSION_MEMORY_MCP_URL 写成 172.18.0.1 会静默杀掉记忆功能;漏配 PYTHONPATH=/workspace/tools 会掉工具数。
- 搬家阶段工具数判据是 200/93(M2 闸门仍在,它由另一个会话在阶段 E 才删)。

【清理也在你这条线里做】
搬家天然是一次重装,不带走等于清掉,零风险。不搬:部署目录 17 个备份文件、内容层 4 个备份文件、快照目录 skills.pre-*(42M)和 tools.pre-*(4.7M)。
A 机目录从第一天就按三段建好:/official、/enterprise、/users(先都可写,统一部署时才改 :ro)。这样后续改挂载只改 compose 一行、不挪数据。

【与另一会话的边界】
另一个会话在改本地代码(内核多内容根、工具架构演进)。你这条线 0 行仓库代码改动 —— 因为用 docker save/load 搬镜像,不从 HEAD 构建,所以他怎么改都污染不到你。你也不要去改仓库代码。
两条线汇合在 9-19 统一部署一次,那次部署不由你单独决定。

【本轮要你产出】
1. 把排期文档"会话 1"那五个阶段逐条质疑一遍:哪一步的判据不吃劲?哪一步失败了退不回来?哪一步的前置我漏了?回滚路径具体怎么走?
2. 停机窗那 8 分钟的操作序列写成可照着敲的清单,每步标出"做错了会怎样"。
3. 需要我确认的副作用操作清成一张表,按时间排。
4. 明确区分"实测到的"和"没验到的",别把推测写成事实。

产出写进 docs/superpowers/specs/ 下的文档并提交(只提交 docs/superpowers/ 里的东西)。
方案跟我对完再动手。这一轮只做只读探查和讨论。
````

## 会话 2|内容分层 + 工具架构演进

````text
你负责 psi-agent 的 ToB 内容分层改造 + 工具架构演进。仓库在 F:\code\psi-agent,分支 main。

【本轮任务】头脑风暴验证方案,不要动手改代码。方案跟我对过一遍才能开始做。

先读排期文档:docs/superpowers/specs/2026-09-08-tob-migration-and-content-layering-plan.md
它的"会话 2"那一节就是你的活。再读这两份需求文档(都在 docs/haitun-delivery/,不在 git 里,别提交):
- ToB-内容分层技术方案-20260907.md —— 三项改动、9 条验收判据、3 个前置
- 工具架构演进方案-20260904.html —— 四个缺陷、三步演进。注意这份写于 9-04,§05 已经做完了(a1173c75/#836),排期文档里有逐项核对表

还要知道但【绝对不要改】:ToC-内容分层需求与验收-20260907.md 是实习生的活。他和你都动 agent.py:364,你的内核改动先落地,他之后 rebase。不要碰 gateway/desktop/ 和 ToC 侧代码。

【交付质量要求 · 不可违反】
- 每条判据都要能变红。写完做变异复核:故意改坏实现,确认用例真的失败。这个仓库出过事:一个用例看着是绿的,但它测的分支被提前返回的兜底分支吃掉了;还出过 docstring 说测 AI 层、实际调的是 Session 层的函数。判据必须落在它声称的那一层。
- 判回归之前先做控制实验。Windows 上 5 条 session 测试恒失败,全量 57-62 failed 是基线不是回归,数字有浮动(硬编码管道名被残留进程占着)。
- worktree 里跑 pytest 必须带 PYTHONPATH=src,否则测的是主 checkout 的 src。跑子树必须 -o testpaths= 且写在路径之前 —— 写在路径后面照样静默失效,这个坑踩过两次。
- lint 看退出码,不看输出末尾。CI 的 lint job 有五步且门控 test。
- PR 标题写结果带数字,正文分问题/改法/验证三段,没验到的部分如实交代。

【已实测的事实,别重新查】
- 根因单点:src/psi_agent/session/agent.py:364 `agent_root = agent_path if agent_path is not None else workspace_path`
- --content-root / content_roots 在 src/ 里出现 0 次,参数还不存在,要新写。
- 红线一:agent.py 那四行里 schedules 那行必须保持 workspace_path,不许"顺手统一成 agent_root" —— 会静默丢掉所有用户日程。它是四行里唯一本来就正确的一行。
- 红线二:分层只进装表期,绝不进 get(name)。tool_registry.py:438 的 get() 是全集可达语义,agent.py:932 的工具调度依赖它。看不见 ≠ 调不到。
- 免费重载机制已有:agent.py:705-706 每回合 refresh()。
- tool_registry.py:557 仍是裸 glob("*.py"),无排序(缺陷①)。
- tool_registry.py:576 `module_name = f"psi_tool_{py_file.stem}_{session_id}_{file_hash}"` —— session_id 把 file_hash 的复用废掉,实测每会话重编 114 文件/104 万字节(缺陷②)。
- M2 闸门在 main:agent.py:61、:784-787 + tool_defs.py:54-125。tool_defs.py:54 的注释记着生产实测 285566 → 83725 chars(省 70.7%)。所以不存在"只删 M2"这个可上线状态 —— 删门与分层暴露必须同一次部署。文档说的"先删 M2 再做第一步"是本地量数据的顺序,不是上线顺序。
- 元工具两份:agents/desktop/tools/tool_search.py + agents/feishu/tools/tool_search.py。已出过"上游改动只跟到一侧"的分歧。
- 下划线前缀文件不进工具扫描(tool_registry.py:558 明确 continue)。所以私有模块里的 set[str]、pathlib.Path 注解永远走不到类型检查 —— 曾有一张卡据此立了两条"缺陷",实测都不成立,已删。
- Kanban 卡 22710 归你:_feishu_impl.py:1031 ↔ _feishu/mentor_ledger.py:31 循环导入,59 个工具文件挂在上面。已实测出判据对:先导 _feishu_impl 通 / 直接导 _feishu/mentor_ledger 不通。

【收债的基线已冻结,别自己重新取】
生产是移动靶(agent 还在往内容层写文件、用户还在聊),所以 9-08 已冻结:
  F:\code\_tob-claim-baseline-20260908\content.tgz  (49 文件 / 154378 字节 / md5 43b7f51811df6a47aa343dca0730ea97)
  F:\code\_tob-claim-baseline-20260908\MANIFEST.md5 (逐文件 md5,49 行)
收债一律对这份快照做。md5 比对前做 LF 归一化(生产 LF、仓库 CRLF)。
其中 agents/feishu/tools/_card_dsl.py 有 603 行漂移、日志显示 agent 动过 45 次,不能盲收,单独查。

【与另一会话的边界】
另一个会话在做搬家(境外 B → 境内 A),纯服务器侧、0 行仓库代码。你不要连生产改任何东西,也不要动 deploy/ 下的部署配置。
两条线汇合在 9-19 统一部署一次。你的代码要在那之前全部测完。

【本轮要你产出】
1. 把排期文档"会话 2"的阶段 A/B/C/E 逐条质疑:判据吃劲吗?顺序有没有排错?我把缺陷①②从阶段 F 上移并进阶段 C(理由是四件事同处 tool_registry.py,分阶段做等于同一处改三遍),这个合并对不对?
2. 阶段 B 那个隔离实验是整件事唯一的真风险 —— _tools_dir_on_sys_path 的 stash/restore 假设同一时刻只有一个目录作用域打开,分层打破这个前提。候选 A(槽位键带层 id)vs 候选 B(禁跨层可见,不可行因为派生是核心动作)。请设计这个实验:怎么量、什么结果算通过、失败了有什么退路。
3. 那 9 条验收判据逐条说清打算怎么写、变异复核怎么做。
4. 元工具是否提共享层这件事,给我一个带推荐的判断(它是阶段 E 的硬前置:分层暴露若只跟到一侧,ToB 这边模型就找不到工具,是能力消失不是退化)。
5. 明确区分"实测到的"和"没验到的"。

产出写进 docs/superpowers/specs/ 下的文档并提交(只提交 docs/superpowers/ 里的东西)。
方案跟我对完再动手改代码。这一轮只读代码、做实验设计、讨论。
````

## 两份 prompt 的共同设计

**都要求头脑风暴而非执行。** 排期是我一个人核出来的,判据也是我一个人定的 ——
让两个会话各自质疑一遍,比我自己复读一遍更可能照出盲区。特别是会话 2 的
第 1 问直接把"缺陷①②上移并进阶段 C"这个决定摆出来让它反驳。

**都写了已实测事实清单。** 不写会重复探查,而重复探查在移动靶上会得出
和我不同的数字,然后分不清是漂移还是我量错了。

**都写了边界与"不许碰"。** 见开头的结论段。

**都要求区分"实测到的"和"没验到的"。** 两个会话的产出会直接变成我做决定的依据,
推测混进事实里最贵。

