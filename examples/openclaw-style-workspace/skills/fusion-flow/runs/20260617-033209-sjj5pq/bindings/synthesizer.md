# PostgreSQL MVCC 综合技术报告：写入、读取与 Vacuum 的协同设计

## 一、总览

PostgreSQL 的 MVCC 不是一个孤立的并发控制模块，而是一套贯穿存储、可见性判定与后台维护的整体设计。理解它的关键，是抓住一条贯穿始终的设计哲学：

> **任何写入都不覆盖旧数据，而是在堆（heap）中追加新版本，并通过元组头部的事务字段标记每个版本的生命周期。**

这条原则把三个看似独立的机制焊接成一个闭环：

- **写入机制**负责「生产版本」——为每个 INSERT/UPDATE/DELETE 在 `HeapTupleHeader` 中刻下 `xmin`（谁创建）、`xmax`（谁删除/更新），UPDATE 通过 `t_ctid` 把新旧版本串成更新链。写入只标记，不清理。
- **读取机制**负责「筛选版本」——每个查询持有一个**快照（snapshot）**，用 `HeapTupleSatisfiesMVCC` 把 `xmin`/`xmax` 与快照对比，从同一行的多个物理版本中挑出「对我可见」的那一个。读不加锁，写不阻塞读。
- **Vacuum 机制**负责「回收版本」——当某个旧版本对所有活跃快照都不再可见（成为 dead tuple），vacuum 回收其空间，同时承担**冻结（freeze）**职责以防止 XID 回卷。

三者共享同一套底层坐标，这是它们能协同的根本原因：

| 共享结构 | 写入 | 读取 | Vacuum |
|---|---|---|---|
| `HeapTupleHeader`（xmin/xmax/t_ctid/infomask） | 写入并设置 | 读取并判定 | 读取并清理/冻结 |
| **CLOG（pg_xact）** | 提交时写状态 | hint bit 缺失时回查 | 推进 relfrozenxid 后截断 |
| **快照 / OldestXmin** | — | 构建快照 | 用 OldestXmin 判定 dead |
| **Visibility Map（VM）** | 写入时清位 | 支撑 index-only scan | 维护 all-visible/all-frozen |
| **Free Space Map（FSM）** | 找页写入 | — | 回收后更新 |

这套设计的本质是一次**权衡**：把历史版本直接放在数据文件里，换来回滚几乎零成本、读判定逻辑简单、不会出现 Oracle 式的 `ORA-01555 snapshot too old`；代价则是表/索引膨胀，以及必须依赖 vacuum 持续维护。相比之下，InnoDB/Oracle 把旧版本放进 undo/回滚段，主表保持紧凑，但长事务会撑爆 undo、回滚成本高。**PostgreSQL 用空间换简单与回滚速度，并把这笔账记在 vacuum 头上。**

下面三节分别提炼三大机制的核心，第五节再把它们串成完整的生命周期。

---

## 二、写入机制

写入是 MVCC 的「版本生产者」。三种写操作的共性是：都只在元组头部做标记，不做物理覆盖。

### 元组的生命周期字段

每个堆元组由 `HeapTupleHeaderData` 描述，写入直接操纵的核心字段：

- `t_xmin`：创建该版本的事务 XID。INSERT 与 UPDATE 产生的新版本都会设置。
- `t_xmax`：删除/更新该版本的事务 XID，`0` 表示版本存活。
- `t_cid`：命令 ID，区分同一事务内不同语句的可见性。
- `t_ctid`：行指针。被 UPDATE 的旧版本指向新版本，形成**更新链（update chain）**；最新版本指向自身。
- `t_infomask`：标志位集合，缓存提交状态（hint bits）与锁信息，如 `HEAP_XMIN_COMMITTED`、`HEAP_XMAX_LOCK_ONLY`、`HEAP_XMAX_IS_MULTI`、`HEAP_HOT_UPDATED` / `HEAP_ONLY_TUPLE`。

### 三种写操作的本质

- **INSERT**（最轻量）：分配 XID 作为 `t_xmin`，`t_xmax=0`；经 FSM 找到有空闲空间的页，写入行指针数组，`t_ctid` 指向自身；写 `XLOG_HEAP_INSERT` WAL，清除该页 VM 位。索引项由执行器单独插入。

- **DELETE**（打墓碑）：定位旧元组后，仅把 `t_xmax` 设为当前 XID，**物理数据原地不动**，索引项也不删除。一切清理留给 vacuum。

- **UPDATE**（最复杂，= 标记旧版本删除 + 插入新版本）：旧元组 `t_xmax` 设为当前 XID 并把 `t_ctid` 指向新版本；新元组带新的 `t_xmin`。其中最关键的是 **HOT（Heap-Only Tuple）优化**：
  - 若**没有索引列发生变化**且**同页有足够空间**，新版本写在同一页，旧版本标 `HEAP_HOT_UPDATED`、新版本标 `HEAP_ONLY_TUPLE`，**不向任何索引插入新条目**——扫描时沿 `t_ctid` 链找到最新版本。这是抑制索引膨胀与 WAL 量的关键。
  - 否则走普通 update：新版本可能落到其他页，且必须更新**所有**索引。

### 提交与 hint bit 回填——写入留下的「读债」

事务 COMMIT 时只在 CLOG 写状态，**不会回头修改已写元组的 infomask**。因此后续第一个读到该元组的查询会查 CLOG、回填 hint bit 并弄脏页面。这就是「读操作也产生写 I/O」的根源，也是写入机制埋给读取机制的伏笔（详见第五节）。

### 写入的性能代价

- **写放大**：一次逻辑 UPDATE 可能写多页 + 更新所有索引（非 HOT 时）。
- **膨胀**：死元组在 vacuum 前持续占用空间。
- **WAL 量大**：多版本 + full page writes 导致 WAL 体积可观。

核心优化是**提升 HOT 命中率**：合理设置表级 `fillfactor`（频繁更新表设 70–90）预留页内空间、减少不必要索引、避免更新索引列，并用 `pg_stat_user_tables.n_tup_hot_upd / n_tup_upd` 监控命中比。

---

## 三、读取机制

读取是 MVCC 的「版本筛选器」。它不依赖读锁，而是用快照对每个物理版本做可见性判定。

### 快照：事务时间轴上的一道横切线

`SnapshotData` 的核心字段：

- `xmin`：最老仍在运行的 XID，比它小的事务一定已结束。
- `xmax`：最新已分配 XID + 1，`≥` 它的事务在快照创建后才开始。
- `xip[]`：快照创建时正在运行的 XID 数组。

可见性的取快照时机由隔离级别决定，这正是隔离级别差异的来源：

- **READ COMMITTED**：每条语句开始时取新快照（同事务内能看到他人新提交的数据）。
- **REPEATABLE READ / SERIALIZABLE**：事务首条语句取一次，全程复用。

### 可见性判定：`HeapTupleSatisfiesMVCC`

对每个读到的元组，本质是回答两个问题：

**「这个版本诞生了吗？」（判 xmin）**
- 是当前事务自己创建 → 比较 `cmin` 与当前 command id；
- xmin 已回滚（hint bit 或 CLOG）→ 不可见；
- xmin 仍活跃或在快照之后才开始（`XidInMVCCSnapshot` 为真）→ 不可见；
- xmin 已提交且在快照之前 → 进入下一步。

**「这个版本死亡了吗？」（判 xmax）**
- 无 xmax 或仅是行锁（`HEAP_XMAX_LOCK_ONLY`）→ 可见；
- xmax 已提交且在快照之前 → 已删除，不可见；
- xmax 仍活跃或在快照之后才提交 → 删除对我尚未生效 → 可见。

其中 `XidInMVCCSnapshot` 的三段判断是高频路径：`xid ≥ xmax` 视为进行中；`xid < xmin` 已确定结束；介于中间则查 `xip[]`。

### Hint bit：与写入机制共担的写放大

首次判定若必须查 CLOG，判定后会把结果写回 `t_infomask` 并弄脏页面。这解释了「第一次 SELECT 慢、之后快」，以及「刚导完大批数据全表扫描时 I/O 飙高」（开启 checksum 或 `wal_log_hints` 时 hint bit 变更还写 WAL）。

### 读取的主要副作用：长快照钉住 vacuum

读取机制本身几乎没有调优开关，它最大的「性能问题」其实是**外溢给 vacuum 的**：系统的全局 `OldestXmin` 由最老的活跃快照决定。只要存在一个 `idle in transaction`、长跑 SELECT、未消费的 replication slot 或孤儿 prepared transaction，所有表的死元组都无法回收，造成全库膨胀。三大定位入口：

```sql
-- 三大「谁卡住 vacuum」入口
SELECT pid, state, xact_start, backend_xmin FROM pg_stat_activity ORDER BY age(backend_xmin) DESC;
SELECT slot_name, xmin, catalog_xmin FROM pg_replication_slots;
SELECT * FROM pg_prepared_xacts;
```

读取相关的关键 GUC 也都是为了「控制旧版本何时可回收」与「主备读取冲突」：`idle_in_transaction_session_timeout`（强烈建议设置）、备库长查询时的 `hot_standby_feedback`、`max_standby_streaming_delay`。另外 **PG14** 重写了 `GetSnapshotData`，显著缓解了高连接数下构建快照的扩展性瓶颈。

---

## 四、Vacuum 机制

Vacuum 是 MVCC 的「版本回收者」，承担两项不可或缺的职责：**回收 dead tuple 空间** 与 **冻结老元组以防 XID 回卷**。它是「就地多版本」设计的必然配套，而非可选项。

### 一次 VACUUM（非 FULL）的流程

1. **加锁**：`ShareUpdateExclusiveLock`，不阻塞读写，但同表同时只允许一个 vacuum，且阻塞 DDL。
2. **第一阶段扫描堆**：借助 VM 跳过 all-visible/all-frozen 页（aggressive vacuum 不跳 all-visible）。对每个元组调用 `HeapTupleSatisfiesVacuum()`，**关键阈值是 `OldestXmin`**——只有 `xmax` 早于 `OldestXmin` 且已提交的元组才判为 `DEAD`；仍可能被老快照看到的判为 `RECENTLY_DEAD`，本轮不回收。收集 DEAD 元组的 TID（**PG17 起用 TidStore 自适应基数树**替代受 `maintenance_work_mem` 限制的数组），顺带做 HOT 剪枝、设 hint bit、冻结达龄元组。
3. **第二阶段清理索引**：批量删除指向 DEAD 元组的索引项，通常是最耗时部分。TID 数组装满会触发「扫堆→清索引→回堆」多轮循环。
4. **第二次堆扫描**：把已从所有索引清除的行指针标为可重用，整理页内空间，更新 FSM 与 VM。
5. **截断**：若尾部有连续空页，尝试加 `AccessExclusiveLock` 归还给 OS（有超时让步保护）。
6. **更新元数据**：刷新 `relpages`/`reltuples`/`relfrozenxid`/`relminmxid`，推进 `datfrozenxid`，触发 CLOG/multixact 段截断。

### Autovacuum 触发逻辑

- 死元组数 `> threshold + scale_factor × reltuples` → 普通 vacuum；
- 插入元组数超阈值（PG13+）→ insert-only vacuum（维护 VM/freeze）；
- 表年龄 `age(relfrozenxid) > autovacuum_freeze_max_age` → 强制 **anti-wraparound（aggressive）vacuum**，即使 autovacuum 关闭也会执行。

### 冻结与 XID 回卷

XID 是 32 位循环值，比当前「老 20 亿以上」的 XID 会被误解释为「未来」，导致旧数据突然不可见。冻结把足够老的元组 xmin 标为 `HEAP_XMIN_FROZEN`（无条件可见），摆脱对真实 XID 比较的依赖。**注意：冻结同样不能冻结比 `OldestXmin` 新的事务**——所以长事务既挡空间回收，也挡冻结，回卷风险和膨胀风险同源。

### 关键认知与调优

- **vacuum 不把空间还给 OS**（除尾部 truncate），只是腾出页内可复用空间；真正缩表要靠 `VACUUM FULL`（全程 `AccessExclusiveLock`）或在线工具 **pg_repack / pg_squeeze**。
- 大表把 `autovacuum_vacuum_scale_factor` 调到 0.01–0.05（默认 0.2 对大表太迟钝），可用表级参数精细化。
- autovacuum 跟不上时调大 `autovacuum_vacuum_cost_limit`（如 1000–2000）或调小 `cost_delay`。
- 调大 `maintenance_work_mem`（1–2GB）减少索引扫描轮数。
- `autovacuum_freeze_max_age` 生产常调到 5亿–10亿留缓冲，但不要逼近 20 亿。
- 开启 `log_autovacuum_min_duration = 0` 观测每次 vacuum 的耗时、扫描页数与跳过原因。

---

## 五、三机制协同全景

把三者放到一行数据的完整生命周期里，协同关系就清晰了。注意几个**跨机制的耦合点**：

1. **写入埋债、读取还债**：写入只在 CLOG 记提交状态，hint bit 由首个读者回填；这把「确定提交状态」的成本从写路径推迟到读路径。
2. **读取定义回收边界**：读取持有的最老快照决定全局 `OldestXmin`，而 `OldestXmin` 正是 vacuum 判定 DEAD 与冻结的红线。读取的「长快照」直接决定 vacuum 能回收什么。
3. **写入与 vacuum 在堆页内合作**：HOT 剪枝让一部分死版本在写入路径上就地清理，减轻 vacuum 负担；VM 位由写入清除、由 vacuum 重建，又反过来加速读取（index-only scan）与下一轮 vacuum。

### 一行数据从写入到清理的全过程

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    一个 tuple 版本的完整 MVCC 生命周期                       │
└──────────────────────────────────────────────────────────────────────────┘

 [写入阶段] —— 版本生产
    │
    │  INSERT / UPDATE 新版本
    ▼
 ┌─────────────────────────┐      UPDATE 旧版本: t_xmax=XID,
 │ 堆页写入新元组           │      t_ctid ──► 新版本 (更新链)
 │ t_xmin=XID, t_xmax=0     │◄──── 索引: HOT 不写新条目 / 非HOT 写全部索引
 │ t_ctid ──► 自身          │
 └───────────┬─────────────┘      写 WAL，清除该页 VM 的 ALL_VISIBLE 位
             │
             │  事务 COMMIT：仅在 CLOG 标记 COMMITTED
             │  （不回写元组 infomask —— 留下 hint bit「读债」）
             ▼
 [读取阶段] —— 版本筛选            ┌─ GetSnapshotData(): 取快照 xmin/xmax/xip[]
    │                             │
    │  SELECT 持快照扫描          │  对每个物理版本：
    ▼                             ▼
 ┌─────────────────────────┐   HeapTupleSatisfiesMVCC:
 │ 判 xmin: 版本诞生了吗?   │   ── hint bit 缺失 ─► 查 CLOG ─► 回填 hint bit
 │ 判 xmax: 版本死亡了吗?   │                              (弄脏页面 = 读放大)
 └───────────┬─────────────┘
             │  挑出对本快照可见的唯一版本
             │
             │  ★ 最老活跃快照 ⇒ 全局 OldestXmin ⇒ 决定下游可回收边界 ★
             ▼
 [等待期] —— 旧版本变 dead
    │
    │  旧版本的 xmax 已提交，且 xmax < OldestXmin ?
    │        否 ─► RECENTLY_DEAD（仍被某老快照需要，不能动）─┐
    │        是 ─► DEAD（对所有快照不可见，可回收）          │ 长事务/慢查询/
    ▼                                                        │ replication slot/
 [Vacuum 阶段] —— 版本回收                                   │ prepared xact
    │                                                        │ 把 OldestXmin 钉死
    │  autovacuum 触发: dead_tup > threshold + scale×reltup  │ ⇒ 死元组无法回收
    ▼                                                        │ ⇒ 表/索引膨胀
 ┌─────────────────────────┐                                │
 │ ①扫堆: 标 DEAD, 收集 TID │   HOT 链可在写入期就地剪枝 ◄────┘
 │ ②清索引: 删对应索引项    │
 │ ③回堆: LP_DEAD→LP_UNUSED │   更新 FSM(空间可复用) + 重建 VM(ALL_VISIBLE)
 │ ④冻结: 老元组→FROZEN     │   推进 relfrozenxid ⇒ 防 XID 回卷 ⇒ 截断 CLOG
 │ ⑤截断尾部空页(可选)      │
 └───────────┬─────────────┘
             │
             ▼
   页内空间可被新 INSERT/UPDATE 复用 ──► 回到 [写入阶段]，闭环
   (注意: 空间默认不还给 OS, 表文件不缩小; 缩表需 VACUUM FULL / pg_repack)
```

这张图揭示了 MVCC 最重要的工程事实：**三机制通过 `OldestXmin` 这条红线相互制约**。读取定义它，vacuum 受它约束，写入产生的死元组在它之下才能被清理。任何一处长事务都会沿着这条链传导成全库膨胀与回卷风险——这也是为什么生产运维的重心，最终都落在「控制事务时长」与「让 autovacuum 跟上节奏」这两件事上。

---

## 六、最佳实践总结

基于三份调研，给出面向 DBA/开发者的可操作建议：

1. **死守 `OldestXmin` 红线，控制事务时长。** 设置 `idle_in_transaction_session_timeout`（如 60s–10min），定期巡检 `pg_stat_activity.backend_xmin`、`pg_replication_slots.xmin`、`pg_prepared_xacts` 三大入口，及时清理长事务、失效复制槽和孤儿 prepared xact。这是同时防膨胀、防回卷、保障 vacuum 有效的根本前提。

2. **针对大表与热点表精细化 autovacuum。** 把大表的 `autovacuum_vacuum_scale_factor` 表级调到 0.01–0.05；当 autovacuum 跟不上时调大 `autovacuum_vacuum_cost_limit`（1000–2000）或调小 `cost_delay`；调大 `maintenance_work_mem`（1–2GB）减少索引扫描轮数。默认值是为小库设计的，大库必须覆盖。

3. **用 fillfactor + 索引治理最大化 HOT 命中率。** 频繁更新表设 `fillfactor=70~90` 预留页内空间，避免更新索引列、删除冗余索引。HOT update 同时减少写放大、索引膨胀和 vacuum 压力，是性价比最高的写入优化。用 `n_tup_hot_upd / n_tup_upd` 持续监控。

4. **主动管理冻结，错峰处理大表。** 监控 `age(datfrozenxid)` 与各表 `age(relfrozenxid)`，逼近 `autovacuum_freeze_max_age` 前在业务低峰主动 `VACUUM (FREEZE)`，避免 anti-wraparound vacuum 在高峰被触发或被长事务阻塞。`autovacuum_freeze_max_age` 可调到 5亿–10亿留缓冲，但不要逼近 20 亿上限。

5. **批量写入后立即跑一次 VACUUM。** 大批量 `COPY`/导入后主动 `VACUUM`（或 `VACUUM FREEZE`），提前固化 hint bit 与冻结、设置 VM all-visible 位，避免把 hint bit 回填的冷读放大转嫁给后续在线查询（「刚导完查询特别慢且 I/O 高」的根因）。

6. **缩表用在线工具，避免 VACUUM FULL 阻塞。** 普通 vacuum 不把空间还 OS，真正膨胀需重建时优先用 **pg_repack**（在线、无长时间排他锁），而非全程持 `AccessExclusiveLock` 的 `VACUUM FULL`。大批量删除优先改为按分区 `DROP`/`TRUNCATE`。

7. **用分区分散写入与维护压力。** 大表分区后，vacuum 可逐分区进行、工作集更小、锁范围更窄，写入热点也被分散，便于按分区独立调参和按时间 `DROP` 老数据。

8. **建立膨胀与可见性的常态监控。** 用 `pg_stat_user_tables`（`n_dead_tup`、`last_autovacuum`）配合 `pgstattuple` 测真实膨胀率，开启 `log_autovacuum_min_duration = 0` 观测每次 vacuum 行为；高连接数场景升级到 PG14+ 或引入 PgBouncer 连接池，缓解快照构建开销。

---

一句话收束：PostgreSQL 的 MVCC 用「堆内多版本」换来了读写互不阻塞、回滚近乎零成本和不会 `snapshot too old`，但把代价记在了膨胀与 vacuum 上。运维这套系统的全部艺术，归根结底是管好 `OldestXmin` 这条贯穿写入、读取、回收三机制的红线。