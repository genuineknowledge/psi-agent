# PostgreSQL MVCC 写入机制深度调研：INSERT / UPDATE / DELETE

## 1. 核心原理

PostgreSQL 的 MVCC 实现采用「多版本元组」（multi-version tuple）模型，与 Oracle/InnoDB 的「就地更新 + undo 回滚段」模型有本质区别。其核心思想是：**任何写入都不会真正覆盖旧数据，而是通过元组头部的事务可见性字段来标记版本的生命周期。**

### 关键数据结构

每一行（tuple）在堆页（heap page）中由 `HeapTupleHeaderData` 描述，与写入机制最相关的字段：

```c
typedef struct HeapTupleHeaderData {
    union {
        HeapTupleFields t_heap;
        DatumTupleFields t_datum;
    } t_choice;

    ItemPointerData t_ctid;     /* 当前元组或其 UPDATE 后新版本的 TID */
    uint16          t_infomask2;/* 属性数 + 若干标志位 */
    uint16          t_infomask; /* 各种标志位 */
    uint8           t_hoff;     /* 用户数据偏移 */
    /* ... bitmap of NULLs, then user data ... */
} HeapTupleHeaderData;

typedef struct HeapTupleFields {
    TransactionId t_xmin;       /* 插入该版本的事务 XID */
    TransactionId t_xmax;       /* 删除/更新该版本的事务 XID（0 表示有效） */
    union {
        CommandId   t_cid;      /* 同事务内的命令序号 */
        TransactionId t_xvac;   /* 旧式 VACUUM FULL 使用 */
    } t_field3;
} HeapTupleFields;
```

可见性判定的几个核心字段：

- **xmin**：创建该元组版本的事务 ID。任何插入（包括 UPDATE 产生的新版本）都会设置 xmin。
- **xmax**：删除或更新（旧版本）该元组的事务 ID。0 表示该版本仍然「存活」。
- **t_cid**：命令 ID（CommandId），用于判定同一事务内不同语句之间的可见性（语句快照）。
- **t_ctid**：行指针。对于被 UPDATE 的旧版本，它指向新版本的位置，形成 **update chain（更新链）**；对于最新版本，t_ctid 指向自己。
- **t_infomask**：标志位集合，缓存提交状态以避免反复查 clog。关键标志：
  - `HEAP_XMIN_COMMITTED` / `HEAP_XMIN_INVALID`：xmin 事务的提交/中止 hint bit。
  - `HEAP_XMAX_COMMITTED` / `HEAP_XMAX_INVALID`：xmax 事务的提交/中止 hint bit。
  - `HEAP_XMAX_LOCK_ONLY`：xmax 只是行锁，不代表删除。
  - `HEAP_XMAX_IS_MULTI`：xmax 是 MultiXactId（多个事务同时持锁）。
  - `HEAP_HOT_UPDATED` / `HEAP_ONLY_TUPLE`：HOT 链相关标志。
  - `HEAP_KEYS_UPDATED`、`HEAP_XMAX_KEYSHR_LOCK` 等：行级锁强度标志。

### 配套结构

- **CLOG（pg_xact，commit log）**：每个事务 2 bit 状态（IN_PROGRESS / COMMITTED / ABORTED / SUB_COMMITTED）。可见性判定时若 hint bit 未设置，需查 clog。
- **Visibility Map（VM）**：每个堆页 2 bit，标记「all-visible」和「all-frozen」。写入会清除对应页的 VM 位，从而影响 index-only scan 和 VACUUM。
- **Free Space Map（FSM）**：记录每页空闲空间，INSERT/UPDATE 寻找可写入页时使用。
- **MultiXact（pg_multixact）**：当多个事务同时对一行加共享锁（如多个 `SELECT ... FOR SHARE` 或外键检查），xmax 存放一个 MultiXactId，指向成员列表。

---

## 2. 关键流程

### INSERT 流程

1. 解析后构造 `HeapTuple`，设置 `t_xmin = GetCurrentTransactionId()`（首次写入时才真正分配 XID，避免只读事务消耗 XID），`t_xmax = 0`，`t_cid = currentCommandId`。
2. 调用 `RelationGetBufferForTuple()`：先查 FSM 找到有足够空闲空间的页；找不到则扩展关系（新建 page）。在缓冲池中 pin + lock 目标 buffer。
3. `RelationPutHeapTuple()` 将元组写入页面的行指针数组（line pointer / ItemId），分配偏移，设置 `t_ctid` 指向自身。
4. 写入 WAL（`XLOG_HEAP_INSERT` 记录），标记 buffer 为 dirty，在 critical section 内更新 page LSN。
5. 若该页此前是 all-visible，清除 VM 中对应位。
6. 释放 buffer lock。索引插入由执行器在堆插入后单独完成（`ExecInsertIndexTuples`），每个索引都会插入一条指向该 TID 的 index entry。

INSERT 是最轻量的写入：不需要定位旧版本，只产生一个新版本。

### DELETE 流程

1. 通过索引或顺序扫描定位到目标元组（heap_delete 接收 TID）。
2. 锁定 buffer，调用 `heap_delete()`：
   - 检查可见性与并发冲突（`HeapTupleSatisfiesUpdate`）。若该元组已被其他未提交事务更新/删除，根据隔离级别决定等待（RC：等锁后重新检查）或报序列化错误（RR/SERIALIZABLE：`could not serialize access`）。
   - 设置旧元组的 `t_xmax = current XID`，写入对应锁强度的 infomask（删除是排他的），更新 `t_cid`。
   - **注意：物理数据并未删除**，只是打上了 xmax「墓碑」标记。
3. 写 WAL（`XLOG_HEAP_DELETE`），标记 dirty，清除 VM 位。
4. 索引项不删除（VACUUM 才会清理）。

### UPDATE 流程（最复杂）

UPDATE = 逻辑上的「标记旧版本删除 + 插入新版本」：

1. 定位旧元组，锁 buffer，`heap_update()`：
   - 并发检查同 DELETE（`HeapTupleSatisfiesUpdate`）。EvalPlanQual 在 RC 隔离级别下处理「更新行已被并发修改」的情况——重新读取最新版本并对其重评估 WHERE 条件。
2. 构造新元组，`t_xmin = current XID`，`t_xmax = 0`。
3. 旧元组：设置 `t_xmax = current XID`，并将旧元组的 `t_ctid` 指向新元组的 TID，形成更新链。
4. **HOT（Heap-Only Tuple）优化判定**：
   - 若**没有被索引的列发生变化**，且**目标页有足够空间容纳新版本**，则走 HOT update：
     - 新版本写在**同一页**，标记 `HEAP_ONLY_TUPLE`；
     - 旧版本标记 `HEAP_HOT_UPDATED`；
     - **不向任何索引插入新条目**——索引仍指向旧版本，扫描时沿 t_ctid 链找到最新版本。这是减少索引膨胀和 WAL 的关键优化。
   - 若索引列变化或同页空间不足，走普通 update：新版本可能写到其他页，且需要对**所有**索引插入新条目。
5. 写 WAL（`XLOG_HEAP_UPDATE` 或 `XLOG_HEAP_HOT_UPDATE`，跨页时可能拆为两条 WAL），更新两个 buffer 的 LSN，清 VM 位。

### 事务提交与 hint bit 回填

写入时 xmin/xmax 的提交状态未知。事务 COMMIT 时只在 clog 中写状态，**不会回头修改已写元组的 infomask**。后续第一个读到该元组的查询会：查 clog 得知事务已提交/中止 → 在元组上回填 `HEAP_XMIN_COMMITTED` 等 hint bit → 标记 buffer dirty。这就是「读操作也会产生写 I/O」的根源（cold read 后的脏页刷盘）。

---

## 3. 重要参数与配置

写入机制本身没有「开关」，但以下 GUC 直接影响写入行为、可见性与膨胀控制：

| 参数 | 含义 | 推荐 |
|---|---|---|
| `fillfactor`（表存储参数，非 GUC） | 页面填充率，预留空间给 HOT update。默认堆 100%。 | 频繁 UPDATE 的表设 70–90，提升 HOT update 命中率 |
| `wal_level` | WAL 记录详细程度（minimal/replica/logical） | 有复制/逻辑解码用 `replica` 或 `logical` |
| `synchronous_commit` | 提交是否等待 WAL 落盘 | 高吞吐可设 `off`（牺牲少量持久性换写入性能） |
| `wal_compression` | 全页镜像（FPI）压缩 | 写密集 + checkpoint 频繁时开启 |
| `full_page_writes` | checkpoint 后首次修改页写整页 | 保持 `on`（防部分写撕裂），除非底层保证原子写 |
| `autovacuum` | 自动回收死元组 | 保持 `on` |
| `autovacuum_vacuum_scale_factor` | 触发 VACUUM 的死元组比例阈值 | 大表调低至 0.01–0.05，避免膨胀 |
| `autovacuum_vacuum_insert_scale_factor` | （PG13+）纯插入表触发 VACUUM 的比例 | 大量 INSERT-only 表用于设置 VM all-visible |
| `vacuum_freeze_min_age` / `autovacuum_freeze_max_age` | 控制 xmin 冻结（防 XID wraparound） | 默认 5000万/2亿，超大写入量可适当调整 |
| `vacuum_cost_delay` / `vacuum_cost_limit` | VACUUM 限流 | 写入高峰期调节避免 I/O 争抢 |
| `commit_delay` / `commit_siblings` | 组提交延迟 | 高并发小事务可微调 |

针对 UPDATE 膨胀，最实用的是表级 `fillfactor`：

```sql
ALTER TABLE orders SET (fillfactor = 80);
-- 之后需 VACUUM FULL 或重写才能让旧页生效
```

---

## 4. 性能影响与优化

### 写入机制带来的性能特征

- **写放大（write amplification）**：UPDATE 产生新版本而非就地更新，意味着一次逻辑更新可能写多页 + 更新所有索引（非 HOT 时）。这是 PG 写入的主要开销来源。
- **表膨胀（bloat）**：死元组在 VACUUM 前一直占用空间。高频 UPDATE/DELETE 表容易膨胀，导致顺序扫描变慢、缓存命中率下降。
- **索引膨胀**：每次非 HOT update 都向所有索引插入新条目，旧条目由 VACUUM 清理，B-tree 易膨胀。
- **WAL 量大**：full_page_writes + 多版本导致 WAL 体积可观。

### 优化手段

1. **提升 HOT update 命中率**：
   - 设置合理 `fillfactor` 预留页内空间；
   - 避免更新被索引覆盖的列（哪怕值未变，列出现在 SET 列表且与索引相关也可能破坏 HOT 判定——实际上 PG 比较的是索引列的新旧值是否相同）；
   - 减少表上不必要的索引。
   - 用 `pg_stat_user_tables.n_tup_hot_upd / n_tup_upd` 监控 HOT 比例。

2. **控制膨胀**：积极的 autovacuum 配置、必要时 `VACUUM`/`pg_repack`（在线重组，避免 VACUUM FULL 的排他锁）。

3. **批量写入优化**：
   - 用 `COPY` 替代多条 INSERT；
   - 多行 `INSERT ... VALUES (...),(...)`；
   - 单事务批处理减少 commit 开销；
   - 大批量 DELETE 改为按分区 `DROP`/`TRUNCATE`。

4. **降低 WAL/同步成本**：`synchronous_commit=off`（容忍少量丢失）、`wal_compression`、合理增大 `max_wal_size` 减少 checkpoint 频率。

5. **减轻 hint bit 冷读放大**：批量加载后主动 `VACUUM`（或 `VACUUM FREEZE`）一次，使后续读不再触发 hint bit 回填的脏页。

6. **分区表**：将写入热点分散，VACUUM 可并行、按分区维护。

---

## 5. 常见问题与排查

### 问题 1：表/索引膨胀

**症状**：表占用空间远大于实际行数据；查询变慢。
**排查**：
```sql
-- 死元组统计
SELECT relname, n_live_tup, n_dead_tup,
       n_dead_tup::float/NULLIF(n_live_tup,0) AS dead_ratio,
       last_autovacuum
FROM pg_stat_user_tables ORDER BY n_dead_tup DESC;

-- 配合 pgstattuple 扩展查看真实膨胀率
SELECT * FROM pgstattuple('orders');
```
**处理**：调优 autovacuum 阈值；`pg_repack` 在线重建；检查是否有长事务阻止回收。

### 问题 2：长事务/空闲事务阻止 VACUUM 回收

死元组的可见范围由全局最老快照决定。一个长时间运行或 `idle in transaction` 的事务会把 `xmin horizon` 钉住，导致 VACUUM 无法回收其后产生的死元组。
```sql
SELECT pid, state, xact_start, now()-xact_start AS dur, query
FROM pg_stat_activity
WHERE state <> 'idle'
ORDER BY xact_start;

-- 查看复制槽/prepared xact/replica feedback 是否钉住 horizon
SELECT slot_name, xmin, catalog_xmin FROM pg_replication_slots;
SELECT * FROM pg_prepared_xacts;
```
设 `idle_in_transaction_session_timeout` 防止空闲事务长期持有。

### 问题 3：HOT update 命中率低

```sql
SELECT relname, n_tup_upd, n_tup_hot_upd,
       round(100.0*n_tup_hot_upd/NULLIF(n_tup_upd,0),1) AS hot_pct
FROM pg_stat_user_tables WHERE n_tup_upd > 0;
```
低于预期时检查：fillfactor 是否过高、是否频繁更新索引列、索引是否过多。

### 问题 4：XID Wraparound（事务 ID 回卷）

写入持续消耗 XID（32 bit，约 21 亿）。若 freeze 不及时，逼近 wraparound 时 PG 会强制进入只读保护（"database is not accepting commands to avoid wraparound"）。
```sql
SELECT datname, age(datfrozenxid) FROM pg_database ORDER BY 2 DESC;
SELECT relname, age(relfrozenxid) FROM pg_class
WHERE relkind IN ('r','m','t') ORDER BY 2 DESC LIMIT 20;
```
处理：监控 age，确保 autovacuum 的 freeze 正常工作，必要时手动 `VACUUM FREEZE`。

### 问题 5：MultiXact 膨胀

大量并发行级共享锁（外键检查、`FOR SHARE`）会快速消耗 MultiXactId，可能引发 multixact wraparound。监控 `pg_stat_activity` 与 `SELECT age(relminmxid) FROM pg_class`。

### 问题 6：并发更新报错

RR/SERIALIZABLE 下 `ERROR: could not serialize access due to concurrent update`，需应用层重试。RC 下表现为锁等待，可能死锁——用 `log_lock_waits` 与 `pg_locks` 排查。

---

## 6. 与其他数据库的对比

### vs MySQL InnoDB

| 维度 | PostgreSQL | InnoDB |
|---|---|---|
| 旧版本存储 | **元组多版本存于堆本身**（in-heap），新旧版本同居数据页 | **就地更新 + undo log（回滚段）**，旧版本在 undo 中重建 |
| UPDATE | 产生新元组 + 标记旧元组 xmax；非 HOT 时更新全部索引 | 就地修改记录，旧值进 undo；二级索引仅在被改列上更新 |
| 回滚 | 几乎零成本：只需在 clog 标记 ABORTED，死元组留给 VACUUM | 需用 undo 逆向回放，回滚大事务慢 |
| 膨胀 | 死元组导致表膨胀，依赖 VACUUM | undo 膨胀（history list），purge 线程清理 |
| 主键/聚簇 | 堆表，主键是普通索引（heap-organized） | 聚簇索引组织表（IOT），二级索引存主键值 |
| 可见性判定 | xmin/xmax + clog + 快照 | 行的 DB_TRX_ID/DB_ROLL_PTR + read view，沿 undo 链回溯 |
| 长事务影响 | 钉住 xmin horizon → 死元组无法回收 | 钉住 read view → undo history list 暴涨 |

核心差异：PG 把「历史版本」直接放数据文件里，换来回滚极快、实现简单，代价是膨胀与必须 VACUUM；InnoDB 把历史放 undo，数据页保持紧凑，代价是回滚慢、undo 需 purge。

### vs Oracle

Oracle 与 InnoDB 类似，采用**就地更新 + undo（回滚段）+ SCN（System Change Number）**。读一致性通过在 buffer 中用 undo 块构造 CR（Consistent Read）块实现，按 SCN 回溯。著名的 `ORA-01555 snapshot too old` 即 undo 被覆盖、无法重建旧版本——这是 PG 永远不会遇到的错误（因为旧版本就在堆里，只要没被 VACUUM 清理）。反过来 PG 的代价是膨胀和 VACUUM 运维负担。Oracle 通过 undo retention/表空间管理控制历史版本生命周期，PG 则通过 VACUUM 与 xmin horizon 控制。

---

如果你想，我可以进一步展开某一块，比如用 `pageinspect` 扩展实际 dump 出 heap page 看 xmin/xmax/infomask 的变化，或者深入 HOT 链的遍历代码（`heap_hot_search_buffer`）。