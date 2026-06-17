# PostgreSQL MVCC 中的 Vacuum 机制深度调研

## 1. 核心原理

PostgreSQL 的 MVCC 实现采用「多版本就地存储」策略：每次 `UPDATE` 或 `DELETE` 并不会立刻覆盖或释放旧行，而是把旧版本保留在堆页（heap page）中，新版本作为新元组写入。这样不同事务可以看到属于各自快照的版本，读写互不阻塞。代价是产生了大量**死元组（dead tuple）**——对所有活跃事务都不再可见的旧版本。Vacuum 的本质就是回收这些死元组占用的空间，并维护若干全局正确性所必需的元数据。

### 关键数据结构

**HeapTupleHeader 中的事务标识**
每个堆元组头部记录了它的生命周期：
- `t_xmin`：插入该版本的事务 ID（XID）。
- `t_xmax`：删除或更新该版本的事务 XID；为 0 表示尚未被删除。
- `t_cid` / `t_ctid`：命令 ID 与指向后续版本的行指针（HOT 链或跨页更新链）。
- `t_infomask`：一组提交状态缓存位，如 `HEAP_XMIN_COMMITTED`、`HEAP_XMIN_INVALID`、`HEAP_XMAX_COMMITTED`，称为 **hint bits**。

一个元组的可见性判断依赖 `xmin`/`xmax` 对应事务是否已提交，以及它相对于当前快照的位置。

**CLOG（Commit Log，现称 pg_xact）**
记录每个 XID 的最终状态（提交/中止/进行中/子事务）。每个 XID 占 2 bit。可见性判断时如果 hint bit 未设置，就要回查 CLOG。Vacuum 推进 `relfrozenxid` 后，老的 CLOG 段可以被截断删除。

**Visibility Map（VM，`_vm` fork）**
每个堆页对应 2 bit：
- `ALL_VISIBLE`：页内所有元组对所有事务可见。设置后，索引扫描可走 **Index-Only Scan**，无需回表；后续 vacuum 也可跳过该页。
- `ALL_FROZEN`：页内所有元组已冻结。`VACUUM`（非 aggressive 模式）可直接跳过这些页。

**Free Space Map（FSM，`_fsm` fork）**
记录每个页的剩余可用空间，供后续 INSERT/UPDATE 快速寻找可放置新元组的页。Vacuum 回收死元组后会更新 FSM。

**事务 ID 回卷（Wraparound）问题**
XID 是 32 位、循环使用的。任意时刻，比当前 XID「老 20 亿以上」的 XID 会被解释为「未来」，导致旧数据突然不可见——这是灾难性的数据丢失。解决方案是**冻结（freeze）**：把足够老的元组的 xmin 标记为 `HEAP_XMIN_FROZEN`（特殊提交位），使其无条件可见，从而摆脱对真实 XID 比较的依赖。冻结正是 vacuum 的核心职责之一，由此引出每个表的 `relfrozenxid`（和针对 multixact 的 `relminmxid`）。

---

## 2. 关键流程

一次 `VACUUM`（非 FULL）的典型执行过程：

1. **获取锁**：对目标表加 `ShareUpdateExclusiveLock`。该锁与读写不冲突，但同一时刻只允许一个 vacuum，且阻塞 DDL。

2. **扫描堆（第一阶段）**：按页遍历。借助 Visibility Map，跳过标记为 `ALL_VISIBLE`/`ALL_FROZEN` 的页（aggressive vacuum 则不跳过 all-visible 页，只跳过 all-frozen）。对每个被扫描的页：
   - 调用 `HeapTupleSatisfiesVacuum()` 判断每个元组状态：`DEAD`、`RECENTLY_DEAD`、`LIVE`、`INSERT_IN_PROGRESS` 等。
   - **关键阈值是 `OldestXmin`**（最老的尚在运行/可能需要看到旧版本的 XID）。只有 xmax 早于 `OldestXmin` 且已提交的元组才能安全判定为 DEAD。`RECENTLY_DEAD` 表示已删除但仍可能被某个老快照看到，本轮不能回收。
   - 收集 DEAD 元组的 TID 到内存中（PG17 之前是受 `maintenance_work_mem` 限制的数组；**PG17 起改为 TidStore 自适应基数树**，内存利用率大幅提升）。
   - 顺带做 HOT 剪枝、设置 hint bits、冻结达到年龄阈值的元组。

3. **清理索引（第二阶段）**：对每个索引执行批量删除，移除指向已收集 DEAD 元组的索引项。这通常是 vacuum 中最耗时的部分。若死元组数组装满，会触发「扫描堆 → 清理索引 → 回到堆」的多轮循环。

4. **第二次堆扫描**：回到堆，把已确认从所有索引中清除的死元组行指针（line pointer）标记为可重用（`LP_DEAD` → `LP_UNUSED`），整理页内空间，更新 FSM 和 VM。

5. **截断（truncate）**：若表尾部有连续的全空页，尝试加 `AccessExclusiveLock` 把这些页归还给操作系统。这一步会获取排他锁，但有超时保护，遇到冲突会让步。

6. **更新统计与元数据**：更新 `pg_class` 中的 `relpages`、`reltuples`、`relfrozenxid`、`relminmxid`；推进数据库级的 `datfrozenxid`，触发 CLOG/multixact 段截断。

**Autovacuum 触发逻辑**：后台 launcher 周期性唤醒 worker。对每张表，当
`死元组数 > vacuum_threshold + vacuum_scale_factor × reltuples`
时触发 vacuum；当
`插入元组数 > vacuum_insert_threshold + vacuum_insert_scale_factor × reltuples`
（PG13+）时触发 insert-only vacuum；当表年龄 `age(relfrozenxid) > autovacuum_freeze_max_age` 时强制触发 **anti-wraparound（aggressive）vacuum**，这种 vacuum 即使在 autovacuum 被关闭时也会执行。

---

## 3. 重要参数与配置

**Autovacuum 总开关与调度**
- `autovacuum`（默认 on）：强烈建议保持开启。
- `autovacuum_max_workers`（默认 3）：并发 worker 数。大库或多表写入密集时可增至 5–10。
- `autovacuum_naptime`（默认 1min）：launcher 检查间隔。

**触发阈值**
- `autovacuum_vacuum_threshold`（默认 50）+ `autovacuum_vacuum_scale_factor`（默认 0.2）。0.2 意味着表要积累 20% 死元组才触发，对大表过于迟钝。**大表建议把 scale_factor 调到 0.01–0.05，或用绝对值控制**（可在表级 `ALTER TABLE ... SET (autovacuum_vacuum_scale_factor = 0.02)`）。
- `autovacuum_vacuum_insert_threshold`（默认 1000）/ `autovacuum_vacuum_insert_scale_factor`（默认 0.2）：针对只插入表，保证 VM/freeze 及时维护。

**冻结相关**
- `vacuum_freeze_min_age`（默认 5000万）：元组年龄超过此值才在普通 vacuum 中冻结。
- `vacuum_freeze_table_age`（默认 1.5亿）：表年龄超过此值时，普通 vacuum 升级为 aggressive。
- `autovacuum_freeze_max_age`（默认 2亿，上限约 20亿）：超过即强制 anti-wraparound vacuum。生产中常调到 5亿–10亿，给冻结留更多缓冲，但**不要逼近 20 亿**。
- `vacuum_multixact_freeze_min_age` / `vacuum_multixact_freeze_table_age` / `autovacuum_multixact_freeze_max_age`：对应 multixact 的冻结控制。

**节流（I/O 限流）**
- `autovacuum_vacuum_cost_limit`（默认 -1，沿用 `vacuum_cost_limit` 的 200）与 `autovacuum_vacuum_cost_delay`（默认 2ms）：通过累计「代价点数」达到 limit 后睡眠 delay。autovacuum 跟不上时，应**调大 cost_limit（如 1000–2000）或调小 cost_delay**，让 vacuum 更激进。

**内存**
- `maintenance_work_mem`（默认 64MB）：vacuum 收集死元组 TID 的内存。调大可减少索引扫描轮数，对大表显著提速，建议 1–2GB。
- `autovacuum_work_mem`（默认 -1，继承上者）：专门给 autovacuum worker。

**PG17+ 新增**
- 引入对 vacuum 进度更精细的控制及 TidStore，内存上限语义略有变化（不再受 1GB TID 数组硬上限限制）。

---

## 4. 性能影响与优化

**对性能的影响**
- **I/O 与 CPU 开销**：vacuum 要扫描堆和全部索引，是后台 I/O 的主要来源之一。索引数量越多，第二阶段越重。
- **WAL 放大**：冻结、行指针整理都会产生 WAL，aggressive/anti-wraparound vacuum 在大表上可能产生海量 WAL，冲击复制和归档。
- **膨胀（bloat）的反作用**：vacuum 回收的是页内空间，**不会把空间归还操作系统**（除尾部 truncate），表文件大小通常不缩小，只是腾出可复用空间。若 vacuum 长期跟不上，表和索引持续膨胀，扫描成本上升、缓存命中率下降，形成恶性循环。

**优化手段**
- **HOT（Heap-Only Tuple）更新**：当更新不涉及任何索引列、且页内有空间时，新版本不产生新索引项，旧版本可由页内的 HOT 剪枝即时清理，大幅减轻 vacuum 压力。为此应**降低 fillfactor**（如 `ALTER TABLE ... SET (fillfactor=80)`）给页内更新留空间，并避免对频繁更新的列建索引。
- **表级精细调参**：对热点高更新表单独把 scale_factor 调小、cost_delay 调小。
- **加大 `maintenance_work_mem`**：减少多轮索引扫描。
- **并行 vacuum**：手动 `VACUUM (PARALLEL n)` 可对多个索引并行清理（autovacuum 不并行）。
- **分区**：把大表拆分为分区，vacuum 可逐分区进行，单次工作集更小、锁范围更窄。
- **空间回收用 `VACUUM FULL` 或在线工具**：`VACUUM FULL` 重写整张表、真正归还空间，但持有 `AccessExclusiveLock` 全程阻塞读写。生产环境优先用 **pg_repack**（在线重建，无长时间排他锁）或 `pg_squeeze`。
- **监控冻结进度**，提前手动 `VACUUM (FREEZE)` 错峰处理大表，避免业务高峰触发 anti-wraparound vacuum。

---

## 5. 常见问题与排查

**问题一：表/索引膨胀**
现象：表文件远大于实际数据量，顺序扫描变慢。
排查：
```sql
-- 查死元组与最近 vacuum 时间
SELECT relname, n_live_tup, n_dead_tup, last_autovacuum, last_vacuum
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC;
```
配合 `pgstattuple` 扩展精确测量膨胀率。根因通常是 autovacuum 跟不上写入速度，或存在长事务阻塞回收。

**问题二：autovacuum 跟不上**
排查 `cost_delay` 是否过大、worker 是否被长时间占用、表是否被频繁触发但每次都被限流。可在日志中开启 `log_autovacuum_min_duration = 0` 观察每次 vacuum 的耗时、扫描页数、回收元组数和「跳过原因」。

**问题三：vacuum 无法回收死元组（`OldestXmin` 被压住）**
最常见的根因。任何一个长时间运行的事务、空闲的 `idle in transaction` 连接、未消费的复制槽、未完成的 prepared transaction，都会把全局 `OldestXmin` 钉在很老的位置，使 vacuum 判定大量元组为 `RECENTLY_DEAD` 而无法回收。
```sql
-- 找最老事务/长事务
SELECT pid, state, xact_start, query
FROM pg_stat_activity
WHERE state <> 'idle'
ORDER BY xact_start;

-- 检查复制槽是否拖住 xmin
SELECT slot_name, xmin, catalog_xmin, active FROM pg_replication_slots;

-- 检查 prepared transaction
SELECT * FROM pg_prepared_xacts;
```
处理：终止僵尸长事务、清理失效复制槽、回滚孤立的 prepared xact。

**问题四：事务 ID 回卷告警**
当数据库年龄逼近 `autovacuum_freeze_max_age`，日志会出现 "must be vacuumed within N transactions"。若耗尽，PostgreSQL 会进入只读保护状态，拒绝新事务，必须在单用户模式做 `VACUUM`。
```sql
-- 监控各数据库年龄
SELECT datname, age(datfrozenxid) FROM pg_database ORDER BY age DESC;
-- 监控各表年龄
SELECT relname, age(relfrozenxid) FROM pg_class
WHERE relkind IN ('r','m') ORDER BY age DESC LIMIT 20;
```
预防：监控告警 + 错峰主动 `VACUUM (FREEZE)`，确保 anti-wraparound vacuum 不被长事务阻塞。

**问题五：vacuum 持续运行但年龄不降**
通常仍是 `OldestXmin` 或某个老复制槽把 `relfrozenxid` 顶住——冻结同样不能冻结比 `OldestXmin` 新的事务。回到问题三的排查路径。

---

## 6. 与其他数据库的对比

**对比 MySQL（InnoDB）**
InnoDB 同样基于 MVCC，但**旧版本不存在数据页里**，而是写入独立的 **undo log（回滚段）**，行内通过 `DB_ROLL_PTR` 串成版本链，读旧版本时按 undo 回放。其垃圾回收对应组件是 **purge 线程**：当某个 delete-marked 记录对所有活跃 read view 都不可见时，purge 线程清理记录并回收 undo。

异同要点：
- **空间模型不同**：PostgreSQL 把死版本留在主表，导致表膨胀且需要 vacuum 回收页内空间；InnoDB 把旧版本放 undo，主索引（聚簇索引）相对紧凑。InnoDB 的膨胀风险主要在 **undo 表空间增长**——同样由长事务阻塞 purge 引起（对应 PG 的 `OldestXmin` 问题，机理高度相似，症状是 history list length 飙升、ibdata/undo 膨胀）。
- **无 XID 回卷问题**：InnoDB 的事务 ID 是 64 位且回滚逻辑不同，不需要 freeze/anti-wraparound 这套机制，这是 PostgreSQL 运维上独有的负担。
- **回收的触发**：InnoDB purge 是常驻后台线程持续推进，PostgreSQL autovacuum 是按阈值周期触发，调参语义不同。

**对比 Oracle**
Oracle 也采用 undo（撤销表空间）+ 聚簇/堆表 + redo 的模型，旧版本通过 undo 构造一致性读（CR block）。它没有 PostgreSQL 式的就地多版本，也就没有等价的 vacuum 进程；空间回收靠 undo 自动管理与段收缩（`ALTER TABLE ... SHRINK SPACE`）。Oracle 的经典痛点是长查询遇到 undo 被覆盖时报 **ORA-01555 (snapshot too old)**——这正是 PostgreSQL「保留旧版本不覆盖」所避免的问题，代价就是膨胀与 vacuum。两者本质是同一权衡的两端：PostgreSQL 用空间换取「不报 snapshot too old」，Oracle/InnoDB 用 undo 换取主表紧凑但可能撤销空间不足。

---

总结：Vacuum 是 PostgreSQL「就地多版本」设计的必然配套，它同时承担**死元组空间回收**和**事务 ID 防回卷冻结**两项职责。运维核心是确保 autovacuum 跟得上写入节奏、及时排查任何压住 `OldestXmin`/`relfrozenxid` 的长事务与复制槽，并对大表做表级精细调参与膨胀监控。