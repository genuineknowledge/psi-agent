# A1 分层私有模块隔离落进内核 — 交付记录

**结论先行**：A1（每层私有模块隔离）已从两个测试文件搬进内核，落在新模块 `src/psi_agent/session/tool_layers.py`（150 语句，97% 覆盖）与 `ToolRegistry.load_layers()`。同时修掉一个**与分层无关、HEAD 上就存在**的内核缺陷：`_stash_private_modules()` 整类跳过带点模块名，使 `agents/feishu/tools/_feishu/` 的 15 个模块在作用域退出后仍留在 `sys.modules`，顺序加载两个目录就会串。

数字：

| 项 | 基线 | 落地后 |
|---|---|---|
| `tests/psi_agent/session` 子树 | 13 failed / 822 passed / 2 skipped / **5 xfailed** | 13 failed / **873 passed** / 2 skipped / **0 xfailed** |
| FAILED 名单（排序后逐条比对） | 13 条 | **同一 13 条，diff 为空** |
| A1 原型 219 条判据 | 绿（量在 `origin/main@86150708`） | **绿**（本基线重量：27 + 93 = 120 用例） |
| 新增内核层判据 | — | **44 条**（K1–K12 + 变异复核） |
| `ruff check` / `ruff format --check` / `ty check` | 0 / 0 / 1（4 unresolved-attribute） | **0 / 0 / 1（同样 4 条，逐条相同）** |

基线的 13 failed 全是 `asyncio events.py:487 NotImplementedError`（`test_channel_adapter.py` 8 + `test_server.py` 5），Windows 上恒失败，与本次改动无关。

---

## 1. 问题

A1 此前只活在两个测试文件里（`test_layer_isolation_a1_probe.py` 1019 行、`test_layer_isolation_a1_real.py` 779 行）。它们各自实现了一套**测试自持的双层装载外壳**——原型。内核这边的两个函数都写着「同一时刻只有一个 tools 目录在场」这个前提：

- `tool_registry.py:49` `_tools_dir_on_sys_path`
- `tool_registry.py:98` `_stash_private_modules`

分层推翻了这个前提。官方层 / 企业层 / 个人层同时在场，每层各自带 `_` 前缀私有帮手。

顺带坐实一个**独立于分层**的内核缺陷（`tool_registry.py:106`）：

```python
if not name.startswith("_") or "." in name:   # ← "." in name 把所有带点名整类跳过
    continue
```

`"." in name` 让 `_feishu.auth` 这类带点私有子模块**从不入 stash**。于是一个 tools 目录的作用域退出后，它的 15 个 `_feishu.*` 子模块仍留在 `sys.modules`；第二个自带 `_feishu` 的目录 import 时会绑到第一个目录的子模块上。**顺序加载就会串，不需要分层**。

## 2. 改法

### 2.1 修 stash 的带点名判定

带点名同样入 stash，判定改为「文件是否落在**该私有包目录**下」而非「是否落在 tools 目录下」——因为 `_feishu/auth.py` 的父目录是 `_feishu/`，不是 tools 目录。新增 `_belongs_to_dir(name, origin, tools_dir)` 承担这个判定，按点分段拼出期望目录，`__init__.py` 另算一层。

### 2.2 新内核模块 `tool_layers.py`

多层同时在场时，`layers_open(layers)` 往 `sys.meta_path` 装一个钩子，私有模块按 `psi_layer_<token>._xxx` 注册。四处支架，每一处都是实测出来的（详见 `2026-09-10-session2-a1-validation-delivery.md` §1）：

1. **裸名别名必须瞬态绑定**。CPython 解析带点子模块的父包用的是**裸名**，只改名会 `KeyError`。只在提问工具文件 exec 期间绑，exec 完立刻释放。常驻绑定毁掉隔离——`leak_plain_aliases` 就是这条的变异。
2. **`psi_layer_<id>` 自己必须作为包存在于 `sys.modules`**。触发条件是**父包是否被当作包解析**，不是嵌套深度（早先的归因是错的，已纠正）。四种形状都会触发。
3. **缓存命中路径也要补绑裸名**。`_module_cache` 命中会跳过 exec；不补绑就变成「同层第二个带点导入者失败而第一个成功」，且只在缓存热了以后才出现。
4. **搜索顺序跟层优先级，不跟打开顺序**。打开顺序在生产里就是 glob 顺序，没有任何一层控制得了它。**这一条只有三层才看得见**，两层永远测不出区别。

`Layer` 是 frozen dataclass，优先级是**显式字段**：

```python
@dataclass(frozen=True)
class Layer:
    layer_id: str
    tools_dir: Path
    priority: int
```

不从列表顺序推（那就是打开顺序，正是要消除的东西），也不从 `layer_id` 推（生产里 `layer_id` 是 tools 目录路径，见 `_layer_id()`）。留任何一个当兜底只是把 bug 搬个地方。

`layer_id` 是路径这件事带来一个原型没遇到的问题：路径塞不进带点模块名（分隔符和点会被读成继续嵌套）。`_scope_token()` = 清洗后的路径末段 + `sha256(layer_id)[:12]`，K10 守住「末段同名的两个目录仍得到两个作用域名」。

### 2.3 语义：提问层优先压过优先级

已由实测定下、不再重开：某层的工具 `import _shared` 时先看**它自己那层**，没有才按层优先级**降序**跨层兜底。`priority_over_asking_layer` 这个旋钮会让 P1/P2/P3/P6 与 R3/R7 同时转红。

理由：`_` 前缀私有帮手是一层的**实现**，不是它可被寻址的**表面**。表面是公开工具名，那里才用层优先级 last-wins。层优先级**只**用于跨层兜底的排序。

### 2.4 `ToolRegistry.load_layers()`

按 `priority` 升序逐层加载，让 last-wins 与 `get()` 的反向搜索、与阶梯三者一致；`work_dir` 取最高层的目录，`refresh()` 于是只重载用户可编辑的那层。`get()` 的**全集可达**语义未动。

### 2.5 5 个 xfail 标记按设计销账

修完 2.1 与 2.2 后，`test_layer_isolation_probe.py` 的 5 个 `xfail(strict=True)` 变 XPASS、被 strict 判红——这是这套记账机制设计好的行为。已删标记转普通用例（P1×2 / P2×2 / `test_layered_load_clears_dotted_modules`），并把「为什么现在会过」写进各自 docstring。删完 xfailed = 0。

同时把该文件的外壳改成**调内核**而非自持隔离：`_exec_layers_together` 现在构造 `Layer` 并 `layers_open()`，每个文件 exec 包在 `executing_tool_file()` 里。

## 3. 验证

### 3.1 判据

`tests/psi_agent/session/test_tool_layers.py`，44 条。文件 docstring 明写：每一层都经 `tool_layers.layers_open` 打开、每个文件都经 `tool_layers.executing_tool_file` 执行——与 `load_layers()` 用的是同一批缝，所以这里没有任何一处重新实现解析。**这是判据落在它声称的那一层的依据**。

| 判据 | 内容 |
|---|---|
| K1 | 裸名私有模块每层各一份 |
| K2 | 带点私有包，3 种导入形式 × 两种打开顺序 |
| K3 | 跨层派生：标记里嵌着被派生帮手自己的嵌套 import |
| K4 | 无残留：`sys.path` / `meta_path` / 裸名 / 作用域名 / 内核 stash 五处 |
| K5 | 一层内只有一个模块对象，按 identity **且**「改一个绑定另一个也变」两种判法 |
| K6 | 父包无预热导入者也能解析，`_K6_SHAPES` × 两种打开顺序 |
| K7 | 内核 stash 保持为空 |
| K8 | 兜底跟优先级：三层 × **全部 6 种打开顺序**，同时核标记与 `VIA_NAME` 作用域名 |
| K9 | 提问层压过更高优先级层：三层 × 6 种顺序 |
| K10 | 末段同名路径的作用域 token 不同 |
| K11 | `load_layers()` 端到端，工具真经 `get()` 被调用 |
| K12 | 同名公开工具解析到最高层 |

K8 同时核 `VIA_NAME`，因为只核标记可能因 `sys.path` 顺序恰好一致而假绿——作用域名是唯一能证明「确实走了钩子」的证据。

### 3.2 变异复核

9 个旋钮，全部从测试外部 monkeypatch 内核（不往生产代码塞测试专用开关；只加了一个生产缝 `_cached_module` 给复用变异用）。`EXPECTED_BREAKAGE` 把「旋钮 → 应当转红的判据」写成可执行断言：

```python
EXPECTED_BREAKAGE = {
    "drop_layer_prefix":          {"K1", "K2", "K6", "K9"},
    "ignore_asking_layer":        {"K1", "K2", "K3", "K6", "K9"},
    "no_cross_layer_fallback":    {"K3", "K8"},
    "no_module_reuse":            {"K5"},
    "leak_plain_aliases":         {"K1", "K2", "K4", "K6", "K9"},
    "no_scope_package":           {"K6"},
    "ignore_layer_priority":      {"K8"},
    "reverse_layer_priority":     {"K8"},
    "priority_over_asking_layer": {"K1", "K2", "K3", "K6", "K9"},
}
```

我预测的 9 组里 **3 组是错的**（3 failed / 41 passed），实测才对：`drop_layer_prefix` 不红 K5；`ignore_asking_layer` 与 `priority_over_asking_layer` 都还红 K3 与 K6（我漏了）。K3 那条另用独立脚本核过：变异下 K3 的 `SEEN` 变成 `FROM-OFFICIAL:PERSONAL-CORE`，即被派生帮手的嵌套 import 不再跟着拥有它的那层。每处差异都写进了 map 上方的注释。`drop_layer_prefix` 确实红不了 K5——K5 问的是「一层的两个导入者是否共用一个对象」，塌成一个共享键是**更多**共享而非更少；抓这个塌陷的是 K9。

另有 `test_every_criterion_is_covered_by_some_mutation`：**作用域包那条支架本来没有见证者**，是它照出来的。

### 3.3 变异复核照出的真问题

**带点名修复本身没有判据守着**——本次最重要的发现。把 `or "." in name` 装回去，`test_layer_isolation_probe.py` 8 条全绿：那 5 个 xfail 是**钩子**翻的，不是 stash 修复翻的（钩子在 `sys.modules` 被查询之前就解析掉私有名）。

补法：往 `test_tool_registry.py` 加两条**顺序加载路径**（不涉及钩子）的判据：

- `test_two_workspaces_bind_their_own_dotted_private_helper`
- `test_dotted_private_submodules_do_not_survive_the_scope`

复核：基线 2 passed；装回缺陷 2 failed，报错分别是 `"second workspace bound the first workspace's dotted private helper"` 与 `"dotted private modules left in sys.modules: ['_priv_pkg.sub']"`——**都精确指向被改的那一行**。恢复用 Python 脚本做，全程未用 `git checkout`（会擦掉自己的实现），`git diff --stat` 确认实现存活。

### 3.4 回归控制

子树 273 个 A1 相关用例（44 + 8 + 101 + 27 + 93）全绿，311.89s。全子树 13 failed / 873 passed / 2 skipped / 0 xfailed，424.31s；FAILED 名单排序后与基线 diff 为空。测试全程带 `PYTHONPATH=src`，子树跑用 `-o testpaths=` 且写在路径**之前**。`ty check` 带 `--python /f/code/psi-agent/.venv`。lint 看**退出码**不看输出末尾。

## 4. 没验到的部分

- **四层及以上没测**。判据最多三层——因为支架 4 的可见性下限是三层。四层不会引入新机制，但没量过就是没量过。
- **生产未验证**。按卡的红线没有连生产改任何东西。生产 `layer_id` 是路径这条已由 K10 覆盖，但真实的 `agents/feishu/tools/_feishu/` 15 个模块在生产环境下的行为没量。
- **性能没在生产量**。`_module_cache` 命中路径补绑裸名多了一次字典写；这个开销没量过。分层是否真的省了「每会话把整个 workspace 重编一份」也没在生产量。
- **只在 Windows / NTFS 上量**。ext4 没重量。
- **同名公开工具在生产是 0 个**，所以 K12 验的 last-wins 现在不裁决任何真实冲突。
- **并发装载只走了 `ContextVar` 的设计**，没有多线程/多协程同时 `load_layers()` 的压力测试。
- **`ScheduleRegistry` 仍用 `workspace_path`**，按卡的红线本次没动它。分层与它的交互没验。
