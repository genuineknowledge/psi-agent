# gateway 内存单调增长根因分析

> 状态:分析完成,**未实施任何修复**。内存问题待团队讨论后处理。
> 实测环境:境内 A 机 `47.100.84.197`,ToB 生产,2026-09-10 10:30–11:30。
> 本文所有数字均为当日实测;推断与未验证项已逐条标注。

## 结论先行

gateway 单进程 RSS 在 15 小时内从 1.75G 涨到 3.05G,`memory.current` 达 4.12G / 4.72G(87%),
且 **swap 额度存在却完全未被使用**(`memory.swap.current = 0`)。

根因**不是某处写错一行**,而是四个各自合理的设计决策叠加:

| # | 层 | 决策本身的合理性 | 叠加后的后果 |
|---|---|---|---|
| ① | trigger 刷新是 per-session 的,而 trigger 定义是全局的 | trigger 要能被会话触发 | 同一份全局配置被 57 个 session 各扫一遍 |
| ② | 刷新 trigger 注册表时捎带全量工具注册 | 复用现成加载路径 | 四个 trigger 全 `skipped`、产出为零,代价是 131 文件全量走一遍 |
| ③ | `_module_cache` 永不淘汰 | 编辑工具文件后旧模块留在 `sys.modules` 里,行为一致 | 每个历史版本的工具文件都常驻堆 |
| ④ | 飞书一个 `open_id` 一条长命 session | 飞书没有"开新会话"语义 | 上述产物无任何自然回收点 |

**没有任何一层负责回收**,而累积速率被 ① 放大了 57 倍。

## 一、实测事实

### 1.1 内存构成

```
memory.current      = 4124160000   (4.12G)
memory.max          = 4718592000   (4.50G)   → 87%
memory.swap.max     = 4718592000   (4.50G)   ← 额度存在
memory.swap.current = 0                      ← 完全未使用
memory.high         = max                    ← 无软限流,直接硬撞
memory.stat: anon 3291254784 (3.29G) / file 799363072 (0.80G) / slab 42527800
```

RSS 集中在**单一进程**,不是子进程堆积:

```
RSS=2956.26MB  pid=1949663  age=15:22:11  psi-agent gateway --listen http://127.0.0.1:8080
RSS= 185.52MB  pid=1950152  age=15:21:39  psi-agent channel feishu --session-socket ...
RSS=  15.86MB  pid=1950194                multiprocessing.resource_tracker
RSS=  15.81MB  pid=1949817                multiprocessing.resource_tracker
RSS=   3.11MB  pid=1949518                bash /workspace/launch-gateway.sh
```

**swap 救不了这个问题:** 3.29G 全在 `anon`(进程私有匿名页)且被 Python 堆持续引用,
换不出去。给更多 swap 额度不会缓解 —— 额度本来就有,是用不上。

### 1.2 增长曲线

| 时刻 | RSS | 占比 |
|---|---|---|
| 09-09 20:04(搬家完成) | 1.75G | 40% |
| 09-10 10:30 | 2.649G | 60% |
| 09-10 11:19 | 3.051G | 69% |
| 09-10 11:2x(`memory.current`) | 4.12G | 87% |

单调增长,无回落。已**超过 B 机 2.9G 的历史峰值**。

### 1.3 工具注册频次

```
每小时工具注册次数(24h,UTC 小时):
T16=42  T17=158  T18=160  T19=159  T20=158  T21=159  T22=160  T23=159
T00=158  T01=161  T02=152  T03=69
```

通宵不停,约 158 次/小时。单个 session 的注册时刻:

```
T17:56 → T18:11 → T18:27 → T18:42 → T18:57 → T19:12 → T19:27 → T19:42
```

**间隔恒为 15 分钟。** 24h 内 57 个不同 session、1695 次工具注册、30379 行日志。
按 session 统计 top10 均为 42–48 次,即均匀摊在所有 session 上,非个别会话异常。

### 1.4 四个 trigger 全部空转

`Trigger refresh complete` 的实测输出,**每次四个全是 `skipped`**:

```
{'rookie-doc-sync': 'skipped', 'handbook-onboarding-welcome': 'skipped',
 'todo-iteration-group-auto-reply': 'skipped', 'assignment-delivery-refresh': 'skipped'}
```

| trigger | 触发事件 | 为什么 skipped |
|---|---|---|
| `rookie-doc-sync` | `drive.file.edit_v1` | **该事件至今推达 0 次** —— 飞书权限死结,TRIGGER.md 里已写明实测对照 |
| `handbook-onboarding-welcome` | `contact.user.created_v3` | 无新员工入职 |
| `todo-iteration-group-auto-reply` | `im.message.receive_v1` | 该群无人发言 |
| `assignment-delivery-refresh` | `haitun.assignment.delivery_check` | 无未结束投递记录 |

15 分钟一次**不是这四个 trigger 各自的业务周期**,而是 `trigger_registry` 的刷新周期。
四个全判 `skipped`、什么业务都没做,却捎带 131 个工具文件走一遍注册。**产出为零。**

## 二、代码层面的根因

### 2.1 `skipped` 的判定逻辑(`trigger_registry.py:192`)

```python
async def _do_refresh(self) -> dict[str, str]:
    new_files = await self._load_from_dir(self._work_dir, self._files)
    ...
    elif not new_entry.fresh:
        result[name] = "skipped"
```

`skipped` 表示 trigger 定义文件内容未变。**四个全 skipped 意味着这次刷新完全没有必要发生。**
`_do_refresh` 自身开销不大,问题在于它所在的刷新循环同时驱动了工具注册(② 层耦合)。

### 2.2 `_module_cache` 的复用是生效的 —— 一处需要更正的判断

`tool_registry.py:648` 的模块名确实带 `session_id`:

```python
module_name = f"psi_tool_{py_file.stem}_{session_id}_{file_hash}"
```

但缓存键**不含** `session_id`(`tool_registry.py:636`、`:140`):

```python
_module_cache: dict[tuple[str, str], types.ModuleType] = {}
cached = _module_cache.get((layer_id, file_hash))
```

**因此"模块名带 session_id 挡住了 file_hash 复用"这个说法对当前代码不成立。** 那描述的是
本缓存加入之前的状态,现已修复 —— 命中缓存时直接复用已 exec 的模块对象,不再 compile、
不再触发模块级副作用。代码注释明确记录了这一点(`:125-130`)。

分析时若沿用旧结论会指向错误的修复方向,故在此显式更正。

### 2.3 真正的累积点:缓存永不淘汰(`tool_registry.py:137-139`)

代码注释自己写明:

```
# Entries are keyed by content hash and never evicted, so editing a tool file
# leaves the old module behind.  That matches what ``sys.modules`` already does
# with these per-hash module names; a bounded cache would be a separate change.
```

**永不淘汰 + 按内容哈希键**意味着:工具文件每改一个字节就是一个新 key,旧模块对象
永久留在 `_module_cache` 和 `sys.modules` 里。131 个文件 × 历史版本数,全部常驻。

这是**有意的权衡**(与 `sys.modules` 行为保持一致),注释也预告了"bounded cache 是另一个改动"。
在 ToC 桌面端场景下进程短命,代价不显现;在 ToB 长命 gateway 里就成了单调增长。

### 2.4 `_layer_id` 是过渡实现(`tool_registry.py:144-149`)

```python
def _layer_id(tools_dir: Path) -> str:
    """Content layering is not in place yet, so the resolved tools-dir path stands
    in for the layer.  Once layers land this becomes the layer id, and files
    shared by a layer stop being re-compiled per workspace."""
```

当前用 tools 目录路径**代替**层 id。内容分层落地后,同层共享的文件会停止按 workspace
重复编译 —— 也就是说 ② 层的部分开销已在既有路线图上,但尚未到位。

## 三、为什么搬家没解决,反而更难看

B 机 `mem_limit=3g`,每天最多 4 次 memcg OOM。**那个 OOM 是当前唯一在起作用的回收机制** ——
等于每 6 小时强制清空一次堆。

A 机抬到 `4500m` 后:泄漏速率一点没变,只是撞墙更慢、单次影响更大。

这一点值得单独记住:**把限额从 3g 调到 4500m 与"拉长刷新周期""禁用空转 trigger"是同一类动作**
—— 都只推迟撞墙时刻,不改变单调增长的性质。

撞墙后果(B 机已实测发生过):gateway OOM 重启 → oauth-proxy 因 `NetworkMode=container:<gateway>`
共享 netns 而孤儿化 → 公网 502。**且 A 机当前没有 `restart-stack.sh`**,已验证过的恢复手段不可用。

## 四、方案

### 4.1 结构性修法(推荐,内核侧)

**A. 让刷新的作用域与被刷新对象的作用域对齐。**
trigger 定义是 `/workspace/triggers/` 下的全局配置,应当**全局刷新一次**,而不是每
session 各刷一遍。这直接消掉 ① 的 57 倍放大,是收益最大的一处。

**B. 解开 ② 的职责耦合。**
刷新 trigger 注册表不应触发工具全量注册。四个 trigger 全 `skipped` 时应当是接近零成本的操作。

**C. 给 `_module_cache` 加上界(注释已预告)。**
按内容哈希永不淘汰在长命进程里不成立。需要 LRU 或按 layer 淘汰。注意注释里那条约束:
命中缓存复用的模块,其函数的 `__module__` 是**首次加载**时的名字,所有权过滤要按那个名字匹配,
不能按当前 session 重建的名字匹配(`tool_registry.py:639-642`)。改动时这条不能破。

**D. 推进内容分层,让 `_layer_id` 名副其实。**
`:144` 已标明当前是过渡实现,分层落地后同层文件停止按 workspace 重编。

### 4.2 只能推迟撞墙、不改变性质的动作(不建议单独采用)

| 动作 | 效果 | 为什么不够 |
|---|---|---|
| 拉长 trigger 刷新周期 | 泄漏速率按倍数下降 | 仍单调增长,只是变慢 |
| 禁用 `rookie-doc-sync` | 减一个纯空转项 | 它依赖的事件永不推达,该禁,但对 3G 量级无意义 |
| 继续抬 `mem_limit` | 撞墙更晚 | 与 3g→4500m 同类 |
| 加 swap 额度 | **无效** | 额度已存在,`swap.current=0`,活跃 anon 页换不出去 |

其中「禁用 `rookie-doc-sync`」独立成立(它依赖的 `drive.file.edit_v1` 因飞书权限死结永不推达,
TRIGGER.md 自己写明实测 0 条),但应作为清理项而非内存修复项。

### 4.3 与内存问题无关但被本次分析暴露的运维缺口

A 机缺 `restart-stack.sh`。若 gateway 撞限额 OOM,oauth-proxy 会孤儿化造成公网 502,
而文档中记载的恢复动作在 A 机不存在。**这一项与内存修复独立,建议先行补齐**(见 Kanban `387b4`)。

## 五、未验证项

- **刷新周期 15 分钟的配置位置未找到。** 实测间隔恒为 15 分钟(1.3),但 `trigger_registry.py`
  中未搜到 `refresh_interval` / `900` / `15 * 60` 等常量,驱动它的循环在别处,未定位。
  因此 4.2 中"拉长刷新周期"是否可通过配置完成、还是必须改代码,**未验证**。
- **`_module_cache` 实际占用未直接测量。** 3.29G 的 `anon` 归因到编译产物常驻是**推断**,
  依据是 RSS 集中于单进程、增长与注册频次同步、缓存永不淘汰有代码依据。
  未做堆快照(`tracemalloc` / `objgraph`)确认构成比例。
- **57 个 session 各占一份编译产物**这一表述需谨慎:`_module_cache` 按 `(layer_id, file_hash)`
  去重,若所有 session 共用同一 tools 目录则应当共享。**为何仍单调增长,需要堆快照才能确定**
  是缓存条目数增长(多 layer / 多版本)还是别处持有引用。这是下一步最该测的一项。
- gateway 为何需要 2.9G 这个绝对量(B 机历史峰值)与 `request_assembly` 超预算 ERROR
  (24h 内 179 条)是否同源,未查。

## 六、下一步建议

1. 先做**堆快照**,确定 3.29G 的实际构成。五节第三条是当前分析链上最薄的一环 ——
   在它确认之前,4.1 的 A/B/C 三项优先级无法排定。
2. 定位 15 分钟刷新周期的驱动位置,判断能否配置化。
3. 补 A 机 `restart-stack.sh`(独立于本问题)。
