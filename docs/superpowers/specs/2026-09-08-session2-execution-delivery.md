# 会话 2 · 内容分层 + 工具架构演进 · 执行交付

2026-09-08 · zsd+Claude · 分支 `feat/tob-content-layering`（基于 `origin/main` 14 commits 之后）

## 结论

五张卡全部收活并合入，阶段 A 收债完成、阶段 B 隔离实验完成。五件事按重要性排：

| 卡 | 内容 | 状态 | 关键实测 |
|---|---|---|---|
| `4724a` | F1 同名工具覆盖方向对齐 | **已合** `953918a9` | 独立变异复核: 2 条用例红，断言正指向被改行 |
| `4a014` | F4 元工具一致性判据 | **已合** `06ecbc6a` | 独立变异复核: 精确报出文件名 + 第 67 行 |
| `4bd21` | 阶段 B 跨层隔离探针 | **已合** `4dfb9ed5` | **P1 也红**，推翻方案"P1 应绿"的预期 |
| `22710` | `_feishu` 循环导入 | **已合** `c14765a9` | 变异后报错与生产**逐字相同** |
| `3905b` | F2 跨会话模块复用 | **已合** `24cab20d` | 第二次 load 编译 12 文件/63288 字符 → **0** |

1. **阶段 B 的实验结果比方案预计的坏。** 方案假设"裸名私有模块本来就是隔离的，分层只需处理带点号的包"。实测不成立：裸名同样串味（P1 红）。`_tools_dir_on_sys_path` 的 stash/restore 在多层同时打开时**前提整体失效**。原兜底方案 A1（每层独立 meta path finder）升为**首选**。详见 §二。

2. **性能缺陷②的归因是错的**，真原因与修法都不同。流传说法"`module_name` 里的 `session_id` 废掉了 `file_hash` 的复用"—— 实测 `module_name` 只是注册键，复用判定根本不看它。真原因是 `load()` 每次传 `old_files=None`，必然全量重编。详见 §三。

3. **修掉两处假绿判据**。`test_get_last_file_wins` 名字说"最后胜出"、docstring 说"返回第一个匹配"、断言 `assert await func() in ("a", "b")` 恒真，永远不可能变红。

4. **生产 59 个工具文件加载失败的环路已修**（`mentor_ledger`）。另 12 个模块同病未修，已 `xfail(strict=True)` 记账。

5. **82 failed 是合并上游后的新基线，不是回归** —— 控制实验证据见 §五。**请不要拿它和几周前的 57-62 相减。**

三条决定（已定，据此拆卡）：

1. **F1 修法**：`get()` 与装表期对齐（最小改动），不动 `_files` 数据结构语义。
2. **元工具**：不提共享层，改加一致性判据。据此**阶段 E 的硬前置解除**。
3. **F2 跨会话复用**：纳入本次范围（按你的指示）。

---

## 一、分支与基线

### 1.1 合并 `origin/main`

开工前同步远程，`origin/main` 领先 14 个 commit，且**动过我方案依赖的全部四个文件**：

```
agents/feishu/tools/_feishu_impl.py           | 109 +++++++-
src/psi_agent/session/tool_defs.py            |  17 ++
src/psi_agent/session/tool_registry.py        |   3 +
tests/psi_agent/session/test_tool_registry.py |  25 ++
```

所以**在合并后的状态上把四条核心结论逐条重验了一遍**，没有沿用旧结论：F1（方向分叉）、F2（归因错）、F3（点号逃逸 stash）、F4（元工具逐字节相同）四条全部依然成立。

### 1.2 拆卡按文件切分

本仓库栽过"并行卡跨文件改名，各自全绿、git 不报冲突、合完炸在收集期"的跟头，所以五张卡**按文件切分**，唯一的例外是 `4724a` 与 `3905b` 都动 `tool_registry.py`（相邻函数）—— 对这一对我做了专门的跨卡复核，见 §四。

---

## 二、阶段 B 隔离实验（最重要）

### 2.1 实验设计

探针 `tests/psi_agent/session/test_layer_isolation_probe.py`，三个设计要点：

- **用 `ExitStack` 让每层的 `_tools_dir_on_sys_path` 同时打开**，再逐层 exec。既有的 `test_two_workspaces_bind_their_own_private_helper` 只测顺序加载，所以它绿着而问题仍在 —— 顺序加载测不出分层的真实形态。
- **两个顺序都跑**（`official-first` / `personal-first`），排除结论是 sys.path 顺序假象。
- **判定靠读回 `MARKER` 值，绝不检查 `sys.modules` 内部** —— 断言实现细节的判据换个等价实现就假红/假绿。

### 2.2 实测结果推翻方案预期

| 用例 | 方案预期 | 实测 |
|---|---|---|
| P1 裸名 helper 按层解析 | 绿 | **红（两个顺序都红）** |
| P2 带点号 helper 按层解析 | 红 | 红 |
| P3 派生：下层看得见上层 | 绿 | 绿 |
| P4 残留清理 | 红 | 裸名绿 / 带点号红 |

合计 3 passed + 5 xfailed(strict)。

**P1 红的含义**：多层同时打开时 stash/restore 的前提**整体失效**，不只是漏了带点号那一类。方案里"候选 A 只需处理带点号"的说法据此作废。

**P3/P4 的绿不能当好消息**：当前隔离度是零，所以"下层看得见上层"当然通得过。它绿是因为根本没隔离，不是因为派生机制成立。等隔离真做上去，P3 会重新受考验。

**结论：兜底方案 A1（每层独立 meta path finder）升为首选。** 理由是它不依赖"同一时刻只有一个目录作用域打开"这个已被证伪的前提。

---

## 三、各卡改法

### 3.1 4724a：`get()` 与装表期方向不一致

`tools` 属性用 `result.update(entry.tools)` —— 后写覆盖，**后加载胜出**。`get()` 原本 `for entry in self._files.values()` 配提前 return —— **先加载胜出**。同名工具于是元数据来自一份、函数体来自另一份。改成 `reversed(...)`。

**没有把"看不见"和"调不到"搞混**：改的只是同名碰撞时谁胜出，只属于某个早加载文件的独有名字照样能找到（红线 2）。

### 3.2 3905b：性能缺陷②的真实原因

复用判定在 `:568` 比对 `old_files[str_path].file_hash`，而 `old_files` 是 `ToolRegistry` **实例内**的 `_files`。`load()` 每次新建实例并传 `old_files=None`（`:452`），所以复用**只在同一实例的 `refresh()` 路径上发生**。实测：`session_id` 完全相同的两次 `load` 照样全量重编 —— 这条直接否掉"session_id 是元凶"。

改法：进程级 `_module_cache`，键 `(layer_id, file_hash)`。

**键里必须有 `layer_id`**：两个 workspace 里同名同内容的文件 hash 相同，只用 hash 会让第二个目录拿到第一个目录的模块，连带拿到对方的 `_priv_helper` 绑定。

两处配套细节：复用分支下 `module_name` 取自缓存模块的 `__name__`（否则 `__module__` 归属过滤会把工具筛空）；异常清理加 `compiled_here` 守卫（复用来的模块属于仍在使用它的上一次 load，不能摘掉）。

实测（猴补 `builtins.compile`，只统计落在该 tools 目录下的调用）：

```
load 1 (session_id=sess-A): 12 文件 / 63288 字符 / 8 tools
load 2 (session_id=sess-B):  0 文件 /     0 字符 / 8 tools
load 3 (session_id=sess-A):  0 文件 /     0 字符 / 8 tools
```

### 3.3 22710：mentor_ledger 环路

`_feishu_impl` 从 `mentor_ledger` 取名字，`mentor_ledger` 又 `import _feishu_impl as _core`。靠"`_feishu_impl` 那句 import 恰好写在文件底部"侥幸化解，glob 字母序一变就炸。

新增 `_feishu/ledger_schema.py` 只放不依赖 `_feishu_impl` 的惰性内容，两侧都从它导入，依赖单向、任何顺序都不成环。实测**只下沉常量不够**：`_feishu_impl` 还从 `mentor_ledger` 取 `_build_list_tables_request`，顺序 B 会改报这一个，故一并下沉。

### 3.4 4a014：元工具一致性成为 CI 门

五个元工具两份逐字节相同，历史上只被 `30e0f28a` 一次性改过 —— 所以"双包拆分让上游改动只跟到一侧"的教训是从 `prompt_sections` 误配到这里的。不提共享层（**阶段 E 的硬前置据此解除**），改加 10 条一致性判据钉住，做了换行归一化。

---

## 四、变异复核（我独立重做，不采信卡的自述）

| 变异 | 预期变红 | 实测 |
|---|---|---|
| `reversed()` 抽掉（4724a 回退） | 判据 1、2 | ✅ 2 failed，报错正指向胜出方 |
| `ledger_schema` 导入改回 `mentor_ledger` | 环路判据 | ✅ 报错与生产**逐字相同**：`cannot import name ... from partially initialized module` |
| `mentor_ledger` 里把常量复制一份 | 同一对象判据 | ✅ `assert 'False' == 'True'` |
| 缓存键去掉 `layer_id` | 隔离判据 | ✅ 2 failed；核心复用判据**保持绿** |
| 缓存整体禁用 | 复用判据 | ✅ 1 failed；两条隔离判据**保持绿** |

后两条互补，证明这三条判据**互不遮蔽** —— 这正是仓库栽过的"一个分支撞四次兜底分支提前吃掉结论"要防的。

**跨卡复核**：`4724a` 与 `3905b` 改同一文件的相邻函数，git 自动合并无冲突 —— 跨卡损坏最容易藏在这里。合并后我重做了 4724a 的变异：判据依然变红，说明 3905b 的改写没把它吃掉。

**判据落在它声称的那一层**：环路判据**每条都在全新解释器里跑**（`subprocess`）。同进程里跑，前一条用例的 `sys.modules` 会供出本该缺失的模块，而这恰恰是这个 bug 当初隐身的原因。

---

## 五、回归归因（关键）

三次全量，先说结论：**82 failed 是合并上游后的新基线，不是我们改出来的。**

| 跑什么 | 树状态 | 结果 |
|---|---|---|
| 全量 | 合卡后 | 88 failed / 5549 passed |
| 全量 | 合卡后（重跑） | 82 failed / 5555 passed |
| **控制实验** | **`3b704ac6`（合上游、零卡产出）** | **82 failed / 5555 passed** |

**控制实验与合卡后那次的失败集合逐条相同（`diff` 为空）**。所以 82 全部来自上游，五张卡没引入任何新失败。

88 与 82 相差的 6 条全在 `test_channel_adapter.py` / `test_server.py` —— 硬编码管道名被残留进程占着的已知 flaky，两次跑之间浮动。

`agents/` 子树单独跑：分支 20 failed，控制实验对应子集 21 failed。少的那条 `test_memory_read_tools_route_one_explicit_visibility` 在分支上单跑也通过。**我没有隔离出它变绿的原因**，不声称是环路修复的功劳。

**踩过的坑记一下**：第一次拿分支全量跟控制实验比，数字是 61 vs 82，看着像少了 21 条。实际是 `pyproject.toml` 的 `testpaths = ["tests"]` 让默认调用只收 `tests/`，而控制实验那次范围更宽 —— 差的 21 条全是 `agents/feishu/tests/...`。**收集范围不同的两个数字不能相减。**

合并后目标子树：**113 passed / 17 xfailed**。

### Lint（看退出码，不看输出末尾）

| 命令 | 退出码 |
|---|---|
| `ruff check src agents tests` | **0** |
| `ruff format --check src agents tests deploy` | **0** |
| `ty check src` | **0** |

`ruff check .` 全库退出码 1，22 条 findings **全部在未入库的本地文件**（`scripts/latency-probe/` 19 条、`deploy/haitun/_patch_*.py` 3 条，`git status` 均为 `??`）。本地噪音，非本分支引入。

---

## 六、之后怎么办（阶段 C/E 的输入）

1. **阶段 C 落分层时按 A1 做**，不要按原方案的候选 A。落地后探针里 5 个 `xfail(strict=True)` 会自动变红提醒摘掉标记 —— 这是设计好的记账机制。
2. **另 12 个 `_feishu/*.py` 有同一个环路**，本轮未修（各被 `_feishu_impl` re-export 3-91 个名字，`message` 独占 91，拆解远超本轮范围）。已逐个 `xfail(strict=True)` 记账，修好一个就变红提醒摘出清单。
3. **`_module_cache` 永不淘汰**，改工具文件会留下旧模块。与 `sys.modules` 现有行为一致，加界限是独立的改动。
4. **阶段 E 删 M2 前**先确认元工具已在共享层暴露，否则 ToB 侧模型找不到工具 —— 那是能力消失，不是能力降级。M2 不能只删不补，否则请求体积涨回三倍。
5. **红线 1 未动**：`ScheduleRegistry` 仍用 `workspace_path`（四条里唯一正确的那条）。分层落地时不许改。

---

## 七、没验到的部分（如实交代）

- **生产判据完全未验**：59 个加载失败归零、M 129→188、内容漂移 42 个文件。本轮不碰生产（与另一会话的边界：搬家线纯服务器侧、0 行仓库代码，9-19 统一部署一次）。
- **Windows 单平台**：所有数字都在 Windows 上量的。82 failed 里相当一部分是 Windows asyncio 子进程的已知基线，Linux/CI 上的真实数字未知。
- **`test_memory_read_tools_route_one_explicit_visibility` 变绿的原因未隔离**，只记录现象。
- **阶段 B 只测了 2 层**，3 层及以上未测。**【2026-09-10 已补】** 三层已测，并因此查出 A1 的第 4 处支架（搜索顺序必须跟层优先级而非打开顺序）—— 这一处两层结构上**测不出**。四层及以上仍未测。见 `2026-09-10-session2-a1-validation-delivery.md`。
- **性能收益只在示例 workspace 上量**（12 文件/63288 字符 → 0）。生产每会话 114 文件/104 万字节的实际收益未测。
- **`layer_id` 目前是路径占位**，真正的分层参数未引入，所以"跨 workspace 共享同一层不重编"这个收益还拿不到。
- **卡 22710 报的 md5** 对应 `main` 那份（45335 字节），worktree HEAD 是 50132 字节的较新版本，环路两份都在，行号 1137 而非 1030。
