# ToB 搬家与内容分层改造 · 方案与排期

2026-09-08 · 执行人 zsd+Claude · 状态:待负责人确认停机窗

## 结论

三件事捆在一起讨论过,实测后应当**拆成两个会话并行,最后统一部署一次**:

| 线 | 内容 | 代码量 | 何时 |
|---|---|---|---|
| **会话 1** | 搬家(境外 B → 境内 A)+ 生产清理 | 0 行 | 9-08 ~ 9-11 |
| **会话 2** | 内容分层 + 工具架构演进(含 Kanban `22710`) | 内核 + 工具 | 9-08 ~ 9-18 |

阶段 D(飞书工具进度反馈)**已删除** —— 核代码发现 `a1173c75`(#836)早已上线,见下。

搬家结束后正常跑一段时间,这段时间把所有要部署的代码准备好,**最后统一部署一次**。

两条线能真并行,因为搬家用 `docker save`/`docker load` 搬镜像、**不从仓库 HEAD 构建**,
所以本地怎么改都污染不到搬迁结果。

顺序死结只有一个:**收债必须早于改挂载**。官方层变 `:ro` 之后,没入库的内容再也拿不回来。

## 工具架构演进并入会话 2

`docs/haitun-delivery/工具架构演进方案-20260904.html` 的三步(§04)全是本地代码工作,
与内容分层同属会话 2(§05 已完成,见下文修正表)。前置条件已满足:PR #815 已合入(`888b1b1f`),
M2 闸门本体在 main(`src/psi_agent/session/agent.py:61`、`:784-787`,
`src/psi_agent/session/tool_defs.py:54-125`)。

### 删 M2 与分层暴露必须同一次部署

`tool_defs.py:54` 的注释记着实测数字:

> Kept because production measured 285566 → 83725 chars,省 70.7% with this gate on.

所以**不存在"只删 M2"这个可上线状态** —— 删掉而分层暴露没跟上,每回合请求体积涨回三倍多。

文档 §04 说"先删 M2,再做第一步",那是**本地量数据的顺序**:不先删,新判据量到的是 M2
的效果而不是新机制的效果(这个仓库出过"绿而不吃劲"的用例)。上线是一次性的,
两件一起走 —— 统一部署天然满足这个约束。

### 归因靠开关,不靠拆窗口

四项改动一次上线,若延迟变差需要分得清是谁干的。仓库里 `PSI_*` 环境变量开关已是既有模式
(生产 compose 正在用 `PSI_DEBUG_MODULES`),所以给工具暴露机制加一个档位开关:
上线后**不重新部署**就能切档对比,归因不必依赖拆停机窗。

这个开关是本次唯一为可观测性新增的东西,阶段 F 一并做。

## Kanban 卡的处置

| 卡 | 处置 | 依据 |
|---|---|---|
| `2e460` | **删**(产出已 cherry-pick 到 main `905524e4`) | 活已干完,254 行 OOM 诊断文档 |
| `ca8c4` | **删** | 两条缺陷都已不成立,见下 |
| `22710` | **留,并入会话 2** | 与 glob 排序共用同一判据 |

### `ca8c4` 为何不成立

卡称两个工具因类型注解被注册器拒绝。实测:**它们都不是工具**。

- `resolve_feishu_display_names` 真身在 `agents/feishu/tools/_assignment_display.py:31`。
  文件名带下划线,`src/psi_agent/session/tool_registry.py:558` 明确
  `if py_file.name.startswith("_"): continue` —— 从不进扫描,`set[str]` 走不到类型检查。
- `load_channel_event_defs` 在 `agents/feishu/tools/channel_event_check.py:26` 已改为
  `as _load_channel_event_defs`,修复在 `30e0f28a`(8-31)。代码里就留着理由:

  ```python
  # Aliased under a leading underscore: the loader treats every public async
  # name in this module as a tool, and this helper's ``Path`` parameter is not a
  # JSON-Schema type, so exposing it would fail signature conversion.
  ```

卡里"已核实该检查在新镜像里原样保留"这句话本身对,但它核的是**内核的检查还在**,
不是**这两个函数还在被扫**。检查还在 ≠ 还撞它。判据搭错了层。

残留一处值得留意:`agents/feishu/tools/positive_negative_list.py:20` 是裸导入
`from _assignment_display import resolve_feishu_display_names`(不带下划线别名),
9-05 `34c73c65` 引入。它依赖 `sys.path` 里有 tools 目录 —— 属于 `22710` 的同族机制,
由 `22710` 覆盖,不单独立卡。

### `22710` 为何必须并入

循环导入在 HEAD 原样存在:

```
agents/feishu/tools/_feishu_impl.py:1031          _LEDGER_SCHEMA_FIELDS,
agents/feishu/tools/_feishu/mentor_ledger.py:31   import _feishu_impl as _core
```

59 个工具文件挂在上面。卡里已实测出判据对:先导 `_feishu_impl` 通 / 直接导
`mentor_ledger` 不通。而会话 2 要把 `tool_registry.py:558` 的无序 glob 改成 sorted ——
改的正是决定谁先谁后的那件事。分开做会互相打脸。

## 基线冻结(已完成)

生产是移动靶:agent 仍在往内容层写文件,用户仍在聊。48 个待收债文件是 9-08 的快照,
拖下去会漂移,搬家后基准还会从 B 机变成 A 机。所以先冻结:

- 快照:49 个文件 / 154,378 字节,包 md5 `43b7f51811df6a47aa343dca0730ea97`
- 逐文件清单:`MANIFEST.md5`(49 行)

之后收债一律对冻结版做,与搬家彻底解耦。

## 会话 1|搬家 + 生产清理

服务器侧,0 行仓库代码。B = 8.222.255.23(境外,现生产),A = 47.100.84.197(境内,目标)。

搬迁背景已核实:阿里云对未备案域名的 403 管控**已消失**(境外打境内 80 得 301),
搬回境内的唯一障碍解除。搬迁是**搬 IP 不搬域名** ——
`account.genuineknowledge.cn` 已硬编码进已发布的 ToC 客户端,只能改 DNS 指向。

### 阶段 0 · 9-08 · 前置(约 1 小时)

一句话:给 A 机装上搬家需要的通路和内存缓冲。

- B 上 `ssh-keygen` 生成密钥对,公钥追加到 A 的 `authorized_keys`。
  现状:B `/root/.ssh/` 只有 `authorized_keys`、无私钥;A 的 sshd
  `permitrootlogin yes` / `pubkeyauthentication yes`,B→A TCP 22 开放
  (`SSH-2.0-OpenSSH_10.2p1 Ubuntu-2ubuntu3.6`);rsync 两侧都有(B 3.2.7 / A 3.4.1)。
  **只缺一个密钥对。**
- A 机加 4G swap。依据:`2e460` 的诊断坐实 OOM 是撞 3G `mem_limit`,
  独立复核为 dmesg **28 次** OOM(最近 9-08 09:40)、gateway RestartCount=7、
  `OOMKilled=false`(子进程被杀的陷阱,所以这类故障零告警)。不加 swap 搬过去必然复现。

判据:B 上 `ssh root@47.100.84.197 true` 返回 0;A 上 `free -h` 显示 swap ≥ 4G。

### 阶段 1 · 9-08 · 搬镜像与数据(约 3 小时,多数是等)

一句话:把三个镜像和三份 workspace 原样搬到 A 机,顺手把垃圾留在原地。

用 `docker save`/`docker load` 而非在 A 机重建。理由两条:生产有 **6 个 src 文件领先 main**
(重建会静默丢掉 meeting-session 功能);A 机连不上 `registry-1.docker.io`
(所以境内反而**必须**开镜像加速器 —— 与方案文档写的方向相反)。

清理就在这一步做 —— 搬家天然是一次重装,不带走等于清掉,零风险:

- 不搬:部署目录 17 个备份文件、内容层 4 个备份文件、快照目录
  `skills.pre-*`(42M)、`tools.pre-*`(4.7M)
- A 机目录**从第一天就按三段建好**:`/official`、`/enterprise`、`/users`
  (先都可写,阶段 G 再改 `:ro`)。这样后续改挂载只改 compose 一行,不用挪数据。

判据(文件数卡死,一个不差):

| 目录 | 大小 | 文件数 |
|---|---|---|
| `workspace` | 2.6G | **38747** |
| `workspace-luolin` | 218M | **1314** |
| `workspace-chengxx` | 58M | **1129** |

加上:A 机 `docker images` 出现三个目标 tag 且 digest 与 B 机一致;
那 49 个收债文件在 A 机的 md5 与冻结清单逐条一致(**比对前做 LF 归一化** ——
生产 LF、仓库 CRLF,裸比对会报几乎全不一致,已出过一次错误判断)。

### 阶段 2 · 9-09 · A 机起栈自测(约 2 小时)

一句话:在 A 机把栈拉起来但**不接飞书通道**,自己验一遍再决定切不切。

飞书一个 App 只能维持一条出向 WS,所以承载通道的新栈必须在旧栈停掉之后才能起。
这一阶段先起不带通道的,专验容器内部。

判据(三条都来自已知会踩的坑):

- `curl 127.0.0.1:8090` 通
- 工具数 **200/93**(不是 202/95 —— 搬家阶段 M2 闸门仍在;它在阶段 E 才随新机制一起删)。
  漏配 `PYTHONPATH=/workspace/tools` 会掉到 200/93 以下
- memory MCP 连得上 —— `FUSION_MEMORY_MCP_URL` 写成 `172.18.0.1` 会**静默**杀掉记忆

同日:DNS TTL 从 **599s 降到 60s**(方案文档说"需降到 300s",实测仍是 599s)。

### 阶段 3 · 9-10 20:00 · 停机切换(预期 8 分钟)

一句话:停旧栈、A 机起带通道的栈、改 DNS 指向。

顺序不可换:停 B 的栈 → A 起带通道的栈 → 改 DNS。参考实测停机 6m28s。

两个不能犯的操作:

- 改容器内文件只能 `docker cp` + `docker restart`,**绝不能 `docker compose up -d`** ——
  `/app/src` 在镜像里不是挂载,`up -d` 会重建容器并静默丢掉所有改动
- 单条 `restart gateway` 会让 `oauth-proxy` 挂在死 netns 上(曾静默 502 了 29 小时);
  Caddy 用 `caddy reload`,`systemctl restart caddy` 会中断 ToC

判据:飞书发一句话有回复;ToC 客户端能登录
(入口是 `lark.oauth.genuineknowledge.cn` → 8090 oauth-proxy,**不是** `account.*`)。

### 阶段 4 · 9-11 · 观察日

一句话:只看不动,确认"境内已起来且可用"这个观察点成立。

判据:24 小时内 dmesg 零 OOM;无 502;飞书响应正常。
B 机**保持不动**作为回退 —— 回退只需把 DNS 改回去。

## 会话 2|内容分层 + 工具架构演进

仓库侧,与会话 1 同时进行。阶段 A~C 是内容分层与加载期确定性,E 是分层暴露。

### 核代码后对 `工具架构演进方案` 的修正

文档写于 9-04,四个缺陷 + §05 逐条核过代码:

| 项 | 文档说 | 代码现状 |
|---|---|---|
| §05 飞书进度反馈 | 缺口在 `client.py:551`,建议先做 | **已完成**,`a1173c75`(#836):`channel/feishu/_tool_status.py` 146 行 / 46 别名 / 25 判据 / 变异 13/13。文档的三个约束都已实现,且多处理了并发只报个数、`merge_streaming_text` 去重吃正文开头 |
| ① glob 无排序 | 部分缓解 | **仍无序**,`tool_registry.py:557` 裸 `glob("*.py")` |
| ② 缓存键混淆 | 未解 | **仍在**,`:576` `module_name = f"psi_tool_{py_file.stem}_{session_id}_{file_hash}"` |
| ③ 平铺暴露 | 临时门挡着 | 未做,只有 `tool_defs.py` 的 M2 硬编码门 |
| ④ 元工具两份 | 待决 | 确认两份:`agents/{desktop,feishu}/tools/tool_search.py` |

**①② 上移到阶段 C**:它们与工具扫描下沉、`22710` 全落在 `tool_registry.py`
同一片代码,分阶段做等于把同一处改三遍、每遍重跑同一套判据。文档本身也说
①② 与分层暴露"无强依赖,可并行推进"。

### 阶段 A · 9-08 ~ 9-09 · 收债

一句话:把冻结的 49 个文件逐个判定入库还是丢弃。

`agents/feishu/tools/_card_dsl.py` 那 603 行漂移单独查(日志显示 agent 动过 45 次,
不能盲收)。

判据:全量 pytest 落在 **57-62 failed** 基线内 —— 这是基线不是回归
(Windows 上 5 条 session 测试恒失败,数字浮动因硬编码管道名被残留进程占着)。
跑法必须 `PYTHONPATH=src`、`-o testpaths=` **写在路径之前**,否则静默失效。
lint **看退出码,不看输出末尾**。

### 阶段 B · 9-09 ~ 9-10 · 隔离实验(唯一真风险)

一句话:验 `_tools_dir_on_sys_path` 那个全局槽位在跨层时会不会串味。

`src/psi_agent/session/tool_registry.py` 的 stash/restore 假设同一时刻只有一个目录作用域打开;
分层打破这个前提。候选 A(槽位键带层 id)vs 候选 B(禁跨层可见 —— 不可行,派生是核心动作)。

这一步的结果**决定阶段 C 的走法**,是真正的序列化点。

### 阶段 C · 9-11 ~ 9-15 · 内核多内容根 + `tool_registry.py` 一次改到位

一句话:让内核能同时挂多个内容根,并把工具扫描搬出内核,同时把 `tool_registry.py`
上四件事一次改完。

四件事共处一个文件,分阶段做等于把同一处改三遍、每遍重跑同一套判据:

1. **工具扫描下沉**(内容分层方案的第二项改动)
2. **glob 排序**(缺陷①):`tool_registry.py:557` 仍是裸 `glob("*.py")`
3. **缓存键拆分复用与隔离**(缺陷②):`:576` 现为
   `module_name = f"psi_tool_{py_file.stem}_{session_id}_{file_hash}"`,
   `session_id` 把 `file_hash` 的复用废掉,实测每会话重编 114 文件 / 104 万字节
4. **`22710` 循环导入**:`_feishu_impl.py:1031` ↔ `_feishu/mentor_ledger.py:31`,
   59 个工具文件挂在上面

多内容根的根因单点在另一个文件:

```python
# src/psi_agent/session/agent.py:364
agent_root = agent_path if agent_path is not None else workspace_path
```

`--content-root` / `content_roots` 在 `src/` 里**出现 0 次** —— 参数还不存在,要新写。

三条红线:

- **`schedules` 那行必须保持 `workspace_path`**,不许"顺手统一成 agent_root" ——
  那会静默丢掉所有用户日程。它是四行里唯一本来就正确的一行。
- **分层只进装表期,绝不进 `get(name)`**。`tool_registry.py:438` 的 `get()`
  是全集可达语义,`agent.py:932` 的工具调度依赖它。**看不见 ≠ 调不到。**
- **glob 排序与 `22710` 必须同批**:改排序就是改加载顺序,而 `22710` 的病灶正是
  加载顺序。分开做会互相打脸。

免费的重载机制已有:`agent.py:705-706` 每回合 `refresh()`。

判据:

- `22710` 那对 A/B 导入顺序实验在改 glob 前后各跑一次。
  变异复核:**故意把 sorted 去掉,确认用例真的红**
- 同一 workspace 起两个会话,第二个的编译文件数显著低于 114。
  变异复核:**把 `session_id` 加回键里,确认用例真的红**
- 全量 pytest 落在 57-62 failed 基线内

### 阶段 E · 9-16 ~ 9-18 · 删 M2 + 正式分层暴露 + 档位开关

一句话:把 45/210 的硬编码临时门换成"常驻核心 + 按域加载"的正式机制,并留一个
生产可切的档位开关。

顺序:**先删 M2,再上新机制**(否则判据量到的是 M2 的效果)。两者同一次部署上线 ——
`tool_defs.py:54` 记着 285566 → 83725 chars,只删不补等于把 70.7% 还回去。

硬约束:**变更只在回合边界发生,一个回合内 tools 绝不变**。
`ToolDefsCache.freeze()` 已经是这个约束的执行点。

**前置:元工具必须先收口(缺陷④)。** 分层暴露靠 `tool_search` 让模型找到没暴露的
工具;而 `agents/{desktop,feishu}/tools/tool_search.py` 两份实现已出过"上游改动
只跟到一侧"的分歧。若分层暴露只跟到一侧,ToB 这边模型就找不到工具 —— 能力直接消失,
不是退化。所以「元工具是否提共享层」这个决定**卡在阶段 E 开工前**。

同时加暴露档位环境变量(见上文"归因靠开关"),让上线后不重部署即可对比。

判据:请求字符数与 M2 时期(83725)同量级或更低;`get(name)` 仍能调到全部 210 个
(**看不见 ≠ 调不到**,这条必须有独立用例,且变异复核要能让它红)。

## 汇合:阶段 G · 9-19 · 统一部署一次

一句话:内容分层的挂载改造 + 工具架构四项改动,一次性上线。

前置**全部满足才动**:会话 1 已落地并观察通过、会话 2 阶段 A~F 全部测完。
且**收债必须已完成** —— 官方层变 `:ro` 后没入库的内容再也拿不回来。

挂载改成 `/official`(ro) + `/enterprise`(ro) + `/users`(rw)。A 机目录在阶段 1
已按三段建好,所以这里只改 compose 一行,不挪数据。

镜像要做**三层核验**:build 机的 src / 镜像内的产物 / 容器内实际加载的。
第三层是 8-18 那次事故缺的那层。

上线后用阶段 F 的档位开关做归因对比,不需要额外停机。

## 清理不再复发的机制

生产乱象的实测归因(用 mtime 纳秒位区分:整秒 = 部署投放,带纳秒 = 就地写入):

- 人的乱象**大于** agent 的乱象:部署目录 17 个备份 + 内容层 4 个 + 2 个快照目录
  vs agent 就地写入 14 个文件
- `agents/feishu/tools/_runtime_paths.py:101` 的 `refuse_agent_write` **是个空操作**,
  两个原因叠加:① 只有 3 个文件调它(自己、`write_excel.py`、`write_word.py`),
  真正的写入通道 `bash.py`/`python_run.py`/`edit.py`/`write.py`/`powershell.py`
  **都不调**;② 生产把两个根都设成 `/workspace`,`agent_root != ws_root` 恒为假

结论:**`:ro` 挂载才是真正的执行点**,bash 绕不过去;应用层守卫只提供可读的报错,不是防线。
Plan B 的挂载分离本身就是解药。

另外要纠正一个说法:agent 往官方层写,不是"同事错误地让 agent 去改东西" ——
是因为**当时没有个人层可写**。给了个人层,这个动机就消失了。

## 需要负责人确认

1. **阶段 0 的两条副作用操作**(都在 A 机空机,不碰 B 的生产):B→A 建密钥对、A 机加 4G swap
2. **停机窗 9-10 20:00** 是否可以
3. **元工具是否提为共享层**(`工具架构演进方案` §06 唯一点名要你定的事)。
   文档建议:只把协议契约(工具描述格式、调用信封)提共享,不共享工具集合与提示词。
   它是**阶段 E 的前置而非落点选择** —— 分层暴露若只跟到一侧,ToB 这边模型
   就找不到工具。请在 **9-15 前**给我。
4. **按域划分的粒度**(几个域、怎么分)。文档说"随第一步设计一并定" ——
   我在阶段 E 动手前把方案发你过目,不需要你现在决定。

## 待办(非阻塞)

- 与实习生对齐 ToC 时序:双方都动 `agent.py:364`,内核改动 9-15 落地后他 rebase
- 在阿里云控制台确认备案号 —— 目前只证明了 403 管控不在,属间接证据
- 与实习生对齐 ToC 时序另需你帮着通气(见上)

