# 工具暴露分层：删掉 M2 硬编码门，换成按层声明 + 三档开关

## 结论先行

- **做完了什么**：删掉 `TMPFIX_M2_CORE_TOOLS`（tmpfix-20260902，64 个名字的内核硬编码白名单）
  与 `tmpfix_m2_gate()`，换成 `src/psi_agent/session/tool_exposure.py`：**每层自带一份
  `EXPOSED.txt`** 声明进 `tools` 数组的名字，加一个三档环境变量开关 `PSI_TOOL_EXPOSURE`
  （`layered` 默认 / `declared` / `off`），**不重新部署就能翻档归因与回滚**。
- **收窄的是发现不是能力**：派发走 `ToolRegistry.get()` 不走数组，被收窄掉的工具**仍然可调**。
  这条性质有专门判据，且对**每个档位**各测一遍 `get()` 对注册表保持全函数。
- **判据**：新增 48 条、删掉 6 条（旧 M2 门的）。session 子树 **13 failed / 944 passed /
  2 skipped**，13 个全是 Windows asyncio 的 `NotImplementedError`，与控制 worktree
  **FAILED 名字排序后逐个相同**（passed 902 → 944）。
- **生产体积未验证**。70.7% 那个数字**属于旧的 M2 硬编码门**，不是本实现的成绩，本卡没连生产
  改任何东西、也没做部署。本实现只有构造判据的数字：固定工具集下默认档为全量的 **3.81%**。
- **孤儿判据已重锚不是失效**：`_tool_status.py` 的别名表原来锚在 `TMPFIX_M2_CORE_TOOLS` 上，
  现改锚到 `agents/feishu/tools/EXPOSED.txt`，并补了一条"锚文件真的在"的判据防空过。

## 设计目标

`tools` 数组该装什么，要由**内容分层结构**回答，而不是内核里一张名单；同时任何一次体积或行为
异常都要能**靠翻开关**定位与回滚，不必等停机窗。

## 问题

### 一、`tools` 数组是每回合的固定成本

它排在请求最前面、参与上游前缀缓存键，且不随对话增长而摊薄。全量装载时飞书包 227 个工具
（137 个文件，实测），而一次会话真正用到的是个位数。旧的 M2 门实测把请求从 285566 压到 83725
字符（**省 70.7%**，3 小时生产流量 / 496 次调用 / 44 个 distinct 工具）——收益是真的，所以
**只删不补不行**：请求体积会涨回三倍。

### 二、内核名单必然漂移

旧名单住在 `session/tool_defs.py`，离它描述的工具隔着一个目录，于是：

| 漂移 | 后果 |
|--|--|
| 2026-09-07 / 09-10 两批工具上线数天后名单才更新 | 期间新工具对模型**不可见** |
| 64 个名字里 **2 个背后根本没有工具**（`feishu_message_list`、`feishu_permission_list_members`，#612 删掉后名单仍列了几个月） | 教给模型的名字调不出来 |

更根本的是**问错了问题**：分层部署要回答"**这些工具属于谁**"，只有层自己知道；内核名单回答的是
"哪些名字热"，那是一次流量采样的快照。

### 三、归因手段是停机窗

要判断体积/行为异常是不是收窄造成的，只能改代码重新部署一轮。

## 实现方案

### 清单随工具一起发布（`EXPOSED.txt`）

每个层的 tools 目录下一份 `EXPOSED.txt`，`#` 注释与空行忽略，一行一个名字。它与它描述的工具
**同目录同版本**，加工具时顺手加一行，这类漂移才有人管。加载器跳过它（不是 `.py`）。

三种状态刻意区分：

| 目录状态 | `read_manifest` | 语义 |
|--|--|--|
| 没有 `EXPOSED.txt` | `None` | **没有意见**（不是"声明了零个"） |
| 有，但没有任何名字 | `frozenset()` | **声明零个** |
| 读不出来（IO/编码错） | `None` + warning | 退回"没有意见"，不因一个坏文件让层隐身 |

`None` 与 `frozenset()` 必须分开，否则 `layered` 档没法既照顾"新层还没写清单"又照顾"这层明确
不想暴露任何东西"。

### 内核只保证发现入口

`DISCOVERY_TOOLS = {tool_search, tool_describe}` 恒在数组里（若注册表有）。忘写清单的层因此
**不可能**把自己关进一个没有发现入口的死角——这是清单写错时的伤害上限。

### 三档开关 `PSI_TOOL_EXPOSURE`

| 档位 | 语义 | 用途 |
|--|--|--|
| `layered`（**默认**，不设即此档） | 声明了清单的层只出清单里的名字；**没有**清单的层出全部 | 新层不写清单也能用，且保住收窄 |
| `declared` | 只出清单声明的名字；无清单的层一个都不出 | 最省，但新层会静默隐身 |
| `off` | 不收窄，等于全量数组 | **归因与回滚** |

默认档保住收窄（这是卡的硬要求）。开关存在的理由主要是**归因**而不是方便。

选环境变量而不是 CLI flag，同 `PSI_CONTENT_ROOTS` 的理由：三条 spawn 路径都得各自穿参，而这是
**部署属性**不是单次调用属性。

**读取时机**：档位在 `SessionAgent.__init__` 读一次，清单每次 load 读一次。都不是每回合读——
数组反正会被 `ToolDefsCache` 冻结，会话中途重读只能得出一个"冻结数组并不反映的档位"，那种不
一致比不支持热改更难查。

### 两个不收窄的兜底

注册表为空（异步加载窗口）、或收窄后一个不剩（清单写错）时，**原样返回全量**。宁可贵一点，也
不要一个工具都没有的会话。

### 层身份从哪来

`FileEntry` 新增 `layer_id` 字段，加载时写入、随文件一起复用，`layer_of_tool` 按与 `tools`
**同向**的 last-wins 展平（同名工具归给赢家）。存字段而不是每次从路径反推，是因为路径反推会在
内容根共享挂载时给同一批字节铸出不同 id（见 `2026-09-10-content-root-layer-identity.md`）。

**没有碰** A1 的 `_tools_dir_on_sys_path` / `_stash_private_modules` / `tool_registry.py:106`
（那是 A1 卡的地盘）。

### 与 `tool_defs` 的分工

`tool_defs` 只管渲染与冻结，收窄在它之前发生。刻意分开：冻结是为了前缀缓存稳定，收窄是为了
体积，一个变了另一个不该跟着变。

## 验证

### 判据落在哪一层

一条判据必须碰它声称的那一层（前车之鉴：docstring 说测 AI 层却调的是 Session 层函数）。

| 文件 | 层 | 条数 | 只有它能证明的事 |
|--|--|--:|--|
| `test_tool_exposure.py` | 纯函数（手搭 mapping） | 23 | 档位语义、清单解析、不改输入 |
| `test_tool_exposure_registry.py` | 真 `ToolRegistry` + 真目录 | 11 | 层归属、清单读取、**被藏起来的工具真的跑得起来** |
| `test_agent_tool_exposure.py` | `SessionAgent` + 真 HTTP 请求体 | 5 | `agent.py` 的接线（其余文件全绿也照不出） |
| `test_tool_exposure_request_size.py` | 体积 | 5 | 体积回归会变红 |
| `test_feishu_pack_manifest.py` | 内容包 | 4 | 声明的名字背后有工具（幽灵名字） |
| `test_tool_status.py`（改） | 飞书渠道 | 13 | 别名表覆盖已暴露工具（重锚） |

**体积判据用构造不用生产流量**：6 个热 + 150 个冷工具、带真实 schema 重量、固定输入，
`separators=(",", ":")` 钉死序列化。量到默认档 3840 / 全量 100740 字符 = **3.81%**。另一条
判据看的是**字符与条数的背离**（一个胖 schema 让条数涨 16.7%、字符涨 250.3%，15 倍），而不是
一个绝对阈值——绝对阈值会在 fixture 改大小时变红，那不是回归。

**发现≠能力**：`test_get_stays_total_over_the_registry_under_every_gear` 遍历 `ExposureTier`
所有成员，断言任何已注册名字 `get()` 都不返回 `None`；派发判据**真的 await 返回的 callable**
（解析出名字不是要证的东西）。

**每个档位各有判据、且只让自己变红**：`test_each_gear_gives_a_different_answer` 断言
`declared < layered < off` 严格递增，所以把两个档位实现成同一件事会变红。

### 变异复核（手工，**未用 `git checkout`**）

`git checkout` 会连实现一起擦掉：变异如期变红、测试随后全绿，只有 `git diff --stat` 为空才
露马脚。所以每条都手工改回。

| # | 破坏的那行 | 变红 | 报错指向被改的东西？ |
|--:|--|--:|--|
| 1 | `select_exposed` 直接返回全量 | 17 | 是 |
| 2 | 去掉 `DISCOVERY_TOOLS` 保证 | 4 | 是 |
| 3 | 默认档改成 `OFF` | 4 | 是 |
| 4 | `layer_of_tool` 归错层 | 9 | 是（纯函数文件正确地**没**变红） |
| 5 | 清单解析不忽略 `#` 注释 | 4 | 是（含幽灵守卫与重锚的别名判据） |
| 6 | 让 `get()` 也听清单 | 4 | 是（发现≠能力那条） |
| 7 | 拆掉 `agent.py` 的接线 | **3，且只在 agent 层文件** | 是 |
| 8 | 把幽灵名字放回清单 | 1 | 是（报错点名那个工具） |

复核后核对 `git diff --stat` 仍是 13 文件 / 1310 插入、无残留 `MUTATION` 标记。

变异 4 的分布正是"判据落在它声称那一层"的证据：层归属是注册表层的事，纯函数文件手搭 mapping
所以结构上照不出，它保持绿是**对的**。

### 孤儿判据的处置：重锚，不是失效

`_tool_status.py` 的 `TOOL_ALIASES` 原来"覆盖 M2 高频集、由判据锁死"，锚点被我删了。**没有让
它静默失效**：改锚到 `EXPOSED.txt`（并 union `DISCOVERY_TOOLS`），因为它守的风险一点没变——
**已暴露的工具缺别名，生产卡片上就显示通用兜底文案**。另补
`test_the_anchor_manifest_is_actually_there`：锚文件丢了会让覆盖检查**空过变绿**，这条专治那个
失败模式。不要求全覆盖是刻意的：未暴露的工具仍可经 `tool_search` 调到，但它们不是这条判据的
责任范围。

### 数字（本机实测，Windows）

| 项 | 结果 |
|--|--|
| session 子树（本分支） | 13 failed / **944** passed / 2 skipped（331s） |
| 控制 worktree（`git worktree add /tmp/ctrl HEAD --detach`） | 13 failed / 902 passed / 2 skipped（377s） |
| FAILED 名字排序后 diff | **完全相同**（8 个 `test_channel_adapter` + 5 个 `test_server`，全是 asyncio `NotImplementedError`） |
| `ruff check` / `ruff format --check` | 退出码 **0 / 0**（看退出码不看输出末尾） |
| `ty check --python /f/code/psi-agent/.venv` | 退出码 1，**4 个** `unresolved-attribute`，与基线**位置逐个相同**（都在 `agents/*/tools/run_flow.py`，非本卡文件） |

控制实验用 worktree 不用 `git stash`（worktree 里 stash 会静默错位，pop 不报错但实现从工作树
消失）。

### 三向同步

| 载体 | 改了什么 |
|--|--|
| `src/psi_agent/session/AGENTS.md` | 新增「工具暴露分层」节：发现≠能力、清单三态、三档表、读取时机、兜底；数字标明归属 |
| `agents/feishu/AGENTS.md` | 新增 `EXPOSED.txt` 节：**加工具要改两处**，以及为什么清单在包里不在内核 |
| 代码 | `tool_exposure.py` 模块 docstring 承载"为什么"，两处 AGENTS.md 不重复它 |

grep 过所有 15 份 `AGENTS.md` 的 `tmpfix` / `M2` / `工具暴露` / `tool_defs` / `64 个`：没有
写死旧行为或旧数字的残留。写 AGENTS.md 时顺手实测了工具数，发现旧 docstring 里的 **210 已经
过时（实为 227）**，新文档按实测写；`tool_defs.py:13` 那个 210 是**引用一条历史生产日志**
（`tools_exposed=53 of 210`），照原样留着。

## 未验证 / 边界

- **本实现的生产请求体积没有测过**。70.7% / 285566 → 83725 属于**旧的 M2 硬编码门**，不是本
  实现的成绩，不挪用。本实现的数字只有构造判据里的 3.81%。
- **没连生产改任何东西，本卡不做部署**。生产要生效需要把 `EXPOSED.txt` 投放到生产内容层——
  而生产内容层已实测漂移 42 个文件，投放属于部署卡的范围。
- 生产上 62 个声明名字是否**够用**（会不会出现模型该用的工具没被 `tool_search` 找到、于是绕路）
  没有生产数据。这正是 `off` 档要回答的问题。
- 13 个 Windows asyncio 失败**没有修**，是既有基线（与控制 worktree 名字相同），不在本卡范围。
- 幽灵名字守卫对 `@mcp` 生成的工具走一张豁免表（当前只有 `serper_google_search`），豁免表自身
  被断言（生成的名字必须还在生成），但新增一个生成型工具需要手工加一行。
