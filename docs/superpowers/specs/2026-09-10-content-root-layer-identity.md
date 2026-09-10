# 内容根：让 `layer_id` 从路径占位变成真正的层参数

## 结论先行

- **做完了什么**：引入 `ContentRoot`（`src/psi_agent/session/content_roots.py`），
  `layer_id` 不再从 tools 目录路径推，改成**声明出来的内容根名**。同一份内容被两个
  workspace 挂载时**只编一次** —— 这是本卡的收益本身，有判据看守。
- **判据**：新增 25 条（`tests/psi_agent/session/test_content_roots.py`），含 6 个变异旋钮的
  红检查。A1 那批 + 新增一起跑：session 子树 **13 failed / 902 passed / 2 skipped**，
  13 个全是 Windows 上 asyncio 的 `NotImplementedError`，与改动前基线**逐个名字相同**。
- **生产收益未验证**。"每会话 114 文件 / 104 万字节"那个数字**没有在生产上复量**，本次也
  没连生产。本地判据证明的是"第二次装载 `compile` 次数为 0"，不是生产内存/耗时的降幅。
- **三条红线都守住了**：`ScheduleRegistry` 仍用 `workspace_path`（有判据 R2）；
  `get()` 保持全集可达（R1）；A1 的"提问层优先胜过优先级优先"未改（R3）。

## 设计目标

一个层的**内容**来自哪个根目录，要与**哪个 workspace 在用它**解耦。官方层的内容根应当可以
被多个 workspace 共享而只编一次。

## 问题：路径既是身份又是位置

改动前 `tool_registry._layer_id(tools_dir)` 拿 tools 目录的 resolve 后绝对路径当层身份。
这把两件不是一回事的东西合并了：

| | 是什么 | 数量 |
|--|--|--|
| **内容** | 一份出厂的官方工具，磁盘上只有一份 | 1 |
| **挂载** | 哪些 workspace 现在把它挂进了作用域 | N |

只有"每个 workspace 各自复制一份全部内容"时两者才重合。内容分层按设计让它们不再重合：
官方根以 `:ro` 挂进多个 workspace，个人根并列在旁边。此时路径派生的 id 会为**同一批字节的
每个挂载点各铸一个不同的 id**，进程级 `_module_cache` 的 `(layer_id, file_hash)` 于是全部
未命中，每个 workspace 把共享层重编一遍。

所以"跨 workspace 共享同一层不重编"这个收益，在 `layer_id` 还是路径占位时**结构上拿不到** ——
不是没优化，是键的语义不允许。

## 实现方案

### `ContentRoot`：声明式的内容身份

```python
@dataclass(frozen=True)
class ContentRoot:
    name: str       # 身份 —— 它就是 layer_id
    path: Path      # 这个挂载点的位置，刻意不参与身份
    priority: int   # 排名，原样传给 Layer.priority
```

`layer_id` 由 **name** 铸成，与任何路径无关。两个 workspace 把官方根挂在不同路径
（`/official/tools` 与 `C:/pack/official/tools`，或同一宿主目录的两个 bind mount）时声明
同一个名字，因此共享缓存条目。

**命名就是全部的隔离契约。** 缓存键仍是 `(layer_id, file_hash)`，所以两个根声明同名，等于在
断言"这两处的字节可以互换"。反过来，`name` 不同则永不共享模块 —— 这正是路径派生 id 当初提供
的隔离度，一分不少。名字因此**必须由挂载方给**：挂载方才知道哪些挂载点是同一份出厂内容。
本模块不猜（从路径推正是被移除的行为），无名条目直接丢弃而不是给个默认名 —— 一个静默共享的
默认名恰好就是这个键要防的那个故障。

### 配置从哪来：`PSI_CONTENT_ROOTS` 环境变量

格式 `name=path`，`os.pathsep` 分隔（不用 `:`，否则 Windows 盘符会被截断成 `C`）。
位置定优先级，升序，步长 10（留出插层不重编号的余地），读起来与挂载清单同向：
`official:enterprise:users`。

**为什么是环境变量而不是新增 CLI 参数**：Session 有三条创建路径 —— `SessionManager`
（转发固定参数表）、Gateway、以及直接 `psi-agent session`。新增一个 flag 要在三处都穿线并
保持同步；而挂载布局是**部署**的属性，同一容器内每个 Session 都一样，这正是本仓库既有的
环境变量用法（`PSI_APPDATA`、`PSI_PRIVATE_OPEN_IDS`）。未设置 → 完全没有分层，单根行为逐字节不变。

坏条目**丢弃并告警**而不抛异常：部署级环境变量里的一个笔误不该让每个 Session 都起不来，
剩下的根照样加载。

### 兜底：没声明任何内容根时

退回 tools 目录的 resolve 后绝对路径，即单根世界 —— 一个目录就是一层，路径是唯一可用的身份。
**兜底成路径而不是兜底成一个共享常量**：后者会让每个未声明目录都变成同一层，两个目录里同名
同内容的文件于是互相拿到对方的模块和 `_priv_helper` 绑定。

### 键只在一处成形

抽出 `tool_registry._cache_key(layer_id, file_hash)`。键**就是**隔离边界，把它写成一个可审的
函数而不是散在三处的元组字面量 —— 散着就会漂移。这个抽取还有一个直接后果，见下节。

## 验证

### 判据落在哪一层

- **收益本身（C1）**：一个声明的根、两个 workspace，第二次装载 `compile` 次数必须为 **0**。
  用 patch `builtins.compile` **观测编译次数**，不断言 `_module_cache` 或 `sys.modules` 的内部
  状态 —— 数"干了多少活"才是收益本身；断言缓存字典的话，缓存被填好却没人读也照样通过。
- **收益的看守（I1）**：两个**不同名**的根放**字节完全相同**的 `probe.py`、不同的
  `_root_helper.py`。键正确则编两次、各自读到自己的 helper；只按 hash 建键则编一次、第二个根
  读到第一个根的 marker。C1 与 I1 是一对：只按 hash 建键的实现**通过 C1 而挂在 I1**，
  这正是本卡说"最容易静默做错"的那处。

### 变异复核（手工，未用 `git checkout`）

| 改了哪一行 | 哪条判据红了 | 报错是否指向被改的那件事 |
|--|--|--|
| `_cache_key` 里 `return (layer_id, file_hash)` → `(file_hash, file_hash)` | `test_i1_two_named_roots_with_identical_bytes_compile_twice` | 是：`identical bytes in two layers compiled 1 time(s)` |
| `ContentRoot.layer_id` 返回 `str(self.path.resolve())` 而非 `self.name` | C2 / C3 / C5 / P6 四条 | 是：同名不同路径被判成两层，编了第二次 |
| `agent.py` 的 `workspace_path / "schedules"` → `agent_root / "schedules"` | `test_r2_schedule_registry_still_takes_workspace_path` | 是：直接指向被改的那行 |

三次都用 `cp` 备份/还原，**没有用 `git checkout`**。每次还原后核 `git diff --stat` **非空**
且 `MUTANT` 标记数为 0，确认实现没被擦掉（本仓库有过"三条变异如期转红、测试全绿，只有
`git diff --stat` 为空才露马脚"的先例）。

### 红检查自己发现的一个缺陷

`test_mutations_redden_exactly_their_own_criteria` 把"哪个变异该让哪些判据红"写成**精确集合**
断言。它当场揭出一件事：`load_content_roots` 把每个根**声明的 id 一路带下去**，所以
`_layer_id` 根本不在分层路径上 —— 我原先安排的所有变异都碰不到缓存键，**I1 当时没有任何变异
在攻击它**。因此才抽出 `_cache_key` 并新增 `cache_key_drops_layer` 这个旋钮。
记在这里而不是抹掉：判据必须落在它声称的那一层，这条是被机器抓出来的，不是我读出来的。

同理，`_layer_id` 的两个变异只红 C3/C4/C5 而不红 C1/C2 —— 因为它服务的是**单目录**路径
（`load()`），分层路径自带 id。期望集合按实测写成这样，而不是按直觉写成"都该红"。

### 数字（本机实测，Windows）

| 项 | 改动前基线 | 改动后 |
|--|--|--|
| session 子树 | 13 failed / 877 passed / 2 skipped | **13 failed / 902 passed / 2 skipped** |
| FAILED 名字 | 8×`test_channel_adapter` + 5×`test_server` | **逐个相同** |
| `ruff check` | 0 | 0 |
| `ruff format --check` | 0 | 0 |
| `ty check --python …/.venv src/ tests/` | 1（4 个 unresolved-attribute，本地噪音） | **0** |

13 个失败全是 `asyncio events.py:487 NotImplementedError`，Windows 上的既有基线，与本卡无关。
跑法固定 `PYTHONPATH=src` 且 `-o testpaths=` 写在路径参数**之前**。

## 未验证 / 边界

- **生产收益未量**：114 文件 / 104 万字节的降幅**没在生产复核**，本次未连生产做任何改动
  （连只读测量也没做）。本地证明的是编译次数，不是生产内存或耗时。
- **`PSI_CONTENT_ROOTS` 尚未在任何部署里设置**，所以生产当前走的仍是兜底的单根路径 ——
  行为与改动前逐字节相同。真正拿到复用要等阶段 G 改挂载时把这个变量填上。
- 未碰 `gateway/`、`desktop/`、ToC 侧、`deploy/`、`tool_defs.py`（阶段 E 在改）。
- `agent.py` 里把 agent 包作为最高层根接入时，用的名字是它的 resolve 后路径 —— 每个用户的
  agent 包本来就各自独立，共享它是错的，所以这一层保持 per-mount 身份是刻意的。
