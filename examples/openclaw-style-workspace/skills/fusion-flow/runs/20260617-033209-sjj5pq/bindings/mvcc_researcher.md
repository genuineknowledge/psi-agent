# PostgreSQL MVCC：读取（SELECT / 快照隔离）机制深度解析

## 1. 核心原理

PostgreSQL 的 MVCC 读取不依赖读锁，而是通过"版本可见性判定"实现。每个事务在读取时拿到一个**快照（Snapshot）**，可见性判定逻辑用这个快照去对比每个元组（tuple）头部记录的事务信息，决定该版本是否"对我可见"。核心是：**写不阻塞读，读不阻塞写**。

### 关键数据结构

HeapTupleHeader（堆元组头，每行物理版本都带一份）中与可见性直接相关的字段：

| 字段 | 含义 |
|------|------|
| `t_xmin` | 插入（产生）该版本的事务 XID |
| `t_xmax` | 删除该版本或对其加行锁的事务 XID（0 表示未删除） |
| `t_cid` (`t_field3`) | 同一事务内的 command id（cmin/cmax 复用），用于事务内自我可见性 |
| `t_ctid` | 指向本行的下一个更新版本（更新链，用于 HOT/EPQ） |
| `t_infomask` | 标志位，含 hint bits 与锁状态 |
| `t_infomask2` | 含属性数量与 HOT 标志 |

`t_infomask` 中的 hint bits 是性能关键：

- `HEAP_XMIN_COMMITTED` / `HEAP_XMIN_INVALID`：xmin 事务已提交/已回滚
- `HEAP_XMAX_COMMITTED` / `HEAP_XMAX_INVALID`：xmax 事务已提交/已回滚
- `HEAP_XMAX_LOCK_ONLY`：xmax 只是加锁（如 SELECT FOR UPDATE），并非真删除
- `HEAP_XMIN_FROZEN`（在新版本用 committed+invalid 组合表示）：该行已被冻结，永久可见

**CLOG（pg_xact，PG10 前叫 pg_clog）**：记录每个 XID 的最终状态，每个事务占 2 bit，四种状态 `IN_PROGRESS / COMMITTED / ABORTED / SUB_COMMITTED`。当 hint bit 还没设置时，可见性判定必须查 CLOG 才能知道 xmin/xmax 对应事务是否提交。

**ProcArray**：内存中所有活跃后端进程及其事务状态。`GetSnapshotData()` 扫描它来构建快照。

**Snapshot 结构**（`SnapshotData`）核心字段：

```c
TransactionId xmin;   // 最老仍在运行的 XID，比它小的事务一定已结束
TransactionId xmax;   // 最新已分配 XID + 1，>= 它的事务在快照创建后才开始
TransactionId *xip;   // 快照创建时正在运行的 XID 数组
uint32 xcnt;          // xip 数组长度
```

可以把快照理解成"事务时间轴上的一道横切线"：`xmin` 以下全部结束、`xmax` 以上全部未开始、中间的活跃事务列在 `xip[]` 里。

---

## 2. 关键流程

### 2.1 获取快照

- **READ COMMITTED**：每条 SQL 语句开始时调用 `GetSnapshotData()` 取新快照（所以同一事务里两条语句能看到别人新提交的数据）。
- **REPEATABLE READ / SERIALIZABLE**：事务第一条语句时取一次快照，整个事务复用，保证可重复读。

`GetSnapshotData()` 遍历 ProcArray，收集所有正在运行的 XID，计算出 `xmin`、`xmax`、填充 `xip[]`。

### 2.2 可见性判定：`HeapTupleSatisfiesMVCC`

对每个读到的元组，按以下逻辑判断（简化版）：

**第一步：判定 xmin（这个版本是否已经"诞生"且对我可见）**

1. 若 xmin 是**当前事务自己**：比较元组的 cmin 与当前 command id —— 当前命令之前插入的可见，之后插入的不可见。
2. 若 hint bit 显示 `XMIN_INVALID`，或查 CLOG 得知 xmin 已回滚 → **不可见**（该版本由已失败事务产生）。
3. 若 xmin 仍在运行、或 `XidInMVCCSnapshot(xmin)` 为真（在快照创建后才开始/仍活跃）→ **不可见**。
4. 否则 xmin 已提交且在快照之前 → 版本"已诞生且可见"，进入第二步。

**第二步：判定 xmax（这个版本是否已经"死亡"）**

1. 若无 xmax（`HEAP_XMAX_INVALID`）或仅是行锁（`HEAP_XMAX_LOCK_ONLY`）→ **可见**（没被删/没被实质改）。
2. 若 xmax 是当前事务自己且在当前命令前删除 → **不可见**。
3. 若 xmax 已提交且在快照之前 → 版本已被删除 → **不可见**。
4. 若 xmax 仍在运行或在快照之后才提交（`XidInMVCCSnapshot(xmax)` 为真）→ 删除对我尚未生效 → **可见**。

### 2.3 `XidInMVCCSnapshot` 三段判断

```
if (xid >= snapshot->xmax) return true;   // 快照之后才开始，视为"进行中"
if (xid <  snapshot->xmin) return false;  // 快照之前就结束，已确定
// 介于中间：在 xip[] 数组里找，找到则进行中
```

### 2.4 Hint bit 设置（lazy）

首次对某元组判定时若必须查 CLOG，判定完成后会把结果写回 `t_infomask`（如 `HEAP_XMIN_COMMITTED`）并把页面标记为脏。后续读直接看 hint bit，省去 CLOG 访问。这就是"第一次 SELECT 慢、之后快"现象的来源，也是隐性写放大的根源。

---

## 3. 重要参数与配置

| 参数 | 含义 | 推荐 |
|------|------|------|
| `default_transaction_isolation` | 默认隔离级别 | `read committed`（默认）；强一致需求用 `repeatable read` 或 `serializable` |
| `transaction_isolation` | 当前事务隔离级别（会话级覆盖） | 按事务需要设置 |
| `old_snapshot_threshold` | 旧快照存活上限，超时后访问被清理的旧版本报 "snapshot too old"，让 vacuum 能更早回收。**注意：因实现缺陷已在 PG17 移除** | PG17 前谨慎使用（如 `1h`），新版本不再可用 |
| `hot_standby_feedback` | 备库把最老快照 xmin 反馈给主库，避免主库 vacuum 清掉备库还需要的版本 | 备库有长查询时设 `on`（代价是主库可能膨胀） |
| `max_standby_streaming_delay` | 备库重放与查询冲突时，查询最多被推迟的时间 | 按业务容忍度，如 `30s`；`-1` 表示无限等待 |
| `vacuum_defer_cleanup_age` | 推迟清理的事务年龄（老机制，逐渐被 feedback 取代） | 一般保持默认 0 |
| `idle_in_transaction_session_timeout` | 杀掉空闲事务，避免长事务卡住快照 xmin | `60s ~ 10min`，强烈建议设置 |

> 说明：读取本身没有太多直接 GUC，关键参数都是"控制旧版本何时能被回收"以及"主备读取冲突"，因为长快照阻碍 vacuum 是 MVCC 读取的主要副作用。

---

## 4. 性能影响与优化

### 主要开销

1. **长事务/旧快照阻碍 vacuum**：系统的 `OldestXmin`（全局最老快照 xmin）由最老的活跃快照决定。只要存在一个 `idle in transaction` 或长跑 SELECT，所有表的死元组都无法被回收 → 表膨胀、索引膨胀、顺序扫描变慢。这是生产中最常见也最严重的问题。
2. **Hint bit 写放大**：大批量导入后第一次全表扫描会大量设置 hint bit 并弄脏页面，触发额外的 checkpoint/WAL（开启 `wal_log_hints` 或 checksum 时 hint bit 变更也会写 WAL），表现为"刚导完数据查询特别慢且 I/O 高"。
3. **`GetSnapshotData` 扩展性**：连接数极高时，每条语句构建快照需扫描 ProcArray，曾是高并发瓶颈。**PG14** 重写了快照计算（Andres Freund 的 snapshot scalability 工作），大幅降低了高连接数下的开销。
4. **更新链与 dead tuple 遍历**：频繁更新的行会形成长版本链，SELECT 需沿链遍历跳过不可见版本。

### 优化手段

- 控制事务时长，设置 `idle_in_transaction_session_timeout`；监控 `pg_stat_activity` 中 `state='idle in transaction'` 和 `xact_start` 老的会话。
- 监控膨胀：`pg_stat_user_tables.n_dead_tup`、`pgstattuple` 扩展。
- 调优 autovacuum（`autovacuum_vacuum_scale_factor` 调小、`autovacuum_naptime` 等），让死元组及时回收。
- 大批量导入后主动跑一次 `VACUUM`（或 `VACUUM FREEZE`），提前固化 hint bit 与冻结，避免后续在线查询承担代价。
- 利用 HOT（Heap-Only Tuple）更新：更新不涉及索引列时版本链留在同页，减少索引膨胀。保留合适的 `fillfactor`（如频繁更新表设 `70~90`）。
- 高连接场景升级到 PG14+，或用连接池（PgBouncer）压缩物理连接数。

---

## 5. 常见问题与排查

**问题一：表持续膨胀，autovacuum 不回收**
- 排查：`SELECT pid, state, xact_start, query FROM pg_stat_activity WHERE state <> 'idle' ORDER BY xact_start;` 找最老事务；查 `SELECT backend_xmin FROM pg_stat_activity ORDER BY age(backend_xmin) DESC;`
- 也检查未消费的 replication slot（`pg_replication_slots.xmin`）和 prepared transaction（`pg_prepared_xacts`），它们同样会卡住全局 xmin。
- 根因常是 idle-in-transaction、长分析查询、忘记 commit 的应用、孤儿 prepared xact。

**问题二：备库报 `ERROR: canceling statement due to conflict with recovery`**
- 备库重放删掉了正在被备库查询读取的旧版本。
- 处理：调大 `max_standby_streaming_delay`，或在主库开 `hot_standby_feedback=on`（权衡主库膨胀）。

**问题三：`ERROR: snapshot too old`（PG17 前）**
- 由 `old_snapshot_threshold` 触发，长查询访问到已被回收的旧版本。
- 处理：缩短查询时长，或调大/关闭该阈值。

**问题四：事务 ID 回卷风险 `database is not accepting commands to avoid wraparound`**
- XID 是 32 位，旧版本需被 FREEZE 否则会被"未来"事务误判可见。长事务 + vacuum 不及时会逼近回卷。
- 排查：`SELECT datname, age(datfrozenxid) FROM pg_database ORDER BY 2 DESC;` 接近 `autovacuum_freeze_max_age`（默认 2 亿）需立即处理。

**问题五：刚导入大量数据后查询慢、I/O 飙高**
- hint bit 首次设置导致大量页面变脏。导入后主动 `VACUUM` 即可。

**通用观测**：`xmin` 横向系统视图—— `pg_stat_activity.backend_xmin`、`pg_replication_slots.xmin`、`pg_prepared_xacts`，是定位"谁卡住了 vacuum"的三大入口。

---

## 6. 与其他数据库对比

### MySQL / InnoDB

- **版本存储方式根本不同**：InnoDB 行只存最新版本在聚簇索引中，旧版本通过 **undo log（回滚段）** 按需重建。PostgreSQL 把所有版本都存在堆表里（append-style）。
  - 后果：InnoDB 不需要 VACUUM，但有 **purge 线程** 清理 undo；长事务会让 undo 暴涨（History List Length 飙升）。PostgreSQL 长事务则导致堆表膨胀。问题表现不同，但根因（长事务挡住旧版本回收）一致。
  - InnoDB 删除/更新不留死元组在主表，二级索引扫描通常更紧凑；PostgreSQL 易索引膨胀。
- **快照机制相似**：InnoDB 的 **ReadView** 含 `m_ids`（活跃事务列表）、`up_limit_id`、`low_limit_id`、`creator_trx_id`，几乎对应 PG 的 `xip[]/xmin/xmax`，可见性判定思路一致。
- **默认隔离级别不同**：InnoDB 默认 **REPEATABLE READ**，PostgreSQL 默认 **READ COMMITTED**。
- **幻读处理**：InnoDB 在 RR 下用 **next-key lock（间隙锁）** 防幻读，是加锁方案；PostgreSQL 的 REPEATABLE READ 本质是快照隔离（SI），靠快照天然避免幻读，但允许 **写偏斜（write skew）**。
- **真正可串行化**：PostgreSQL 的 SERIALIZABLE 用 **SSI（Serializable Snapshot Isolation）**，跟踪读写依赖与谓词锁（SIReadLock），检测到危险环就回滚某事务，是乐观无额外阻塞的方案。InnoDB 的 SERIALIZABLE 退化为给所有读加共享锁，是悲观加锁。

### Oracle

- 同样基于 **UNDO 表空间** 回放旧版本（与 InnoDB 类似的回滚段思路），读一致性通过 **SCN（System Change Number）** 而非事务 ID 列表来判定。
- 经典的 **ORA-01555 "snapshot too old"** 与 PG 的旧快照失效本质同源：长查询需要的 undo 已被覆盖。Oracle 调 `UNDO_RETENTION`，PG 调 `old_snapshot_threshold` / 控制事务时长。
- Oracle 默认 READ COMMITTED，和 PostgreSQL 一致；其 SERIALIZABLE 也是快照隔离实现（不是真正可串行化，会有写偏斜），这点反而不如 PostgreSQL 的 SSI 严格。

**一句话总结差异**：PostgreSQL 把版本放在表里、用快照 + VACUUM；InnoDB/Oracle 把旧版本放在 undo 里、用回滚段 + 后台 purge。前者读判定简单但需对抗膨胀，后者表更紧凑但长事务撑爆 undo，且回滚段读放大可能更高。