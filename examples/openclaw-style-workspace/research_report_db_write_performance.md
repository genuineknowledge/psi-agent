# 6大数据库高并发写入性能深度对比

> 调研日期: 2026-06-17 | 对比维度: 高并发写入吞吐、锁机制、持久化策略、扩展模型

---

## 一、总览矩阵

| 维度 | PostgreSQL | MySQL (InnoDB) | SQLite | MongoDB | Redis | Cassandra |
|------|-----------|----------------|--------|---------|-------|-----------|
| **类型** | 关系型 (ORDBMS) | 关系型 (RDBMS) | 嵌入式关系型 | 文档数据库 | 内存KV/数据结构 | 宽列分布式 |
| **写入模型** | WAL + MVCC | WAL (Redo Log) + MVCC + Doublewrite | WAL (Journal) / Rollback | WAL (Journal) + 内存映射 | 内存写入 + AOF/RDB | LSM Tree + CommitLog |
| **并发写控制** | 行级锁 + MVCC (写不阻塞读) | 行级锁 + MVCC + Gap Lock | 单写者 (WAL模式读写不互斥) | 文档级锁 (WiredTiger MVCC) | 单线程事件循环 | 无锁追加 (LSM) |
| **理论写入TPS** | 5万~20万+ | 5万~15万+ | 1千~5万 (WAL模式) | 5万~15万+ | 10万~180万+ (单实例) | 无限水平扩展 |
| **扩展方式** | 垂直为主 (逻辑复制) | 垂直+主从 (Group Replication) | 无 (单文件) | 水平分片 (Sharding) | 集群分片 (16384 slots) | 原生水平扩展 |
| **持久化保证** | fsync WAL (可配置) | fsync Redo Log (可配置) | fsync WAL/Journal | fsync Journal (可配置) | AOF fsync策略 (可配置) | CommitLog fsync |
| **适合场景** | 复杂事务+高并发写入 | 高并发OLTP | 嵌入式/移动端/单机 | 灵活Schema+高写入 | 极致性能缓存/队列 | 海量时序/日志写入 |

---

## 二、各数据库详细分析

### 2.1 PostgreSQL

**写入架构：**
- WAL (Write-Ahead Log): 所有修改先写入16MB的WAL段文件，再异步写入数据文件
- MVCC: 写操作创建新版本元组，不阻塞读；旧版本由VACUUM回收
- 背景写入器 (Background Writer): 定期将脏页刷盘，减少检查点时的I/O峰值
- 支持异步I/O: `io_method = worker` (多进程) 或 `io_uring` (Linux 5.1+)

**高并发写入关键参数：**
```
shared_buffers = 25%~40% RAM    # 共享缓冲池
max_connections = 200~500       # 最大连接数（每连接一进程）
wal_buffers = 16MB~64MB         # WAL缓冲区
max_wal_size = 4GB~16GB         # 触发检查点的WAL上限
checkpoint_timeout = 15min      # 检查点间隔
effective_io_concurrency = 200  # SSD建议值
synchronous_commit = off        # 牺牲持久性换吞吐
```

**并发写入瓶颈：**
1. **进程模型**: 每连接一个进程，高并发(>500)时上下文切换开销显著 → 建议用连接池(PgBouncer)
2. **WAL写入串行化**: 所有写操作共享WAL缓冲区，需获取WALInsertLocks
3. **表膨胀**: 频繁UPDATE/DELETE导致死元组，需要VACUUM维护
4. **锁竞争**: 行级锁在高频更新同一行时产生等待

**优化手段：**
- 分区表减少索引大小
- `synchronous_commit = off` + `wal_level = minimal` (批量导入场景)
- 并行Worker: `max_parallel_workers = 8~16`，`max_parallel_workers_per_gather = 4`
- 连接池: PgBouncer事务模式，将500+客户端收敛到~50个实际连接
- SSD + 独立WAL磁盘

**典型基准 (单机, SSD)：**
- ~5-10万 TPS (小事务, synchronous_commit=on)
- ~15-20万+ TPS (小事务, synchronous_commit=off)
- 批量COPY: ~100万+ 行/秒

---

### 2.2 MySQL (InnoDB)

**写入架构：**
- Doublewrite Buffer: 防止页部分写入，写入时先写doublewrite buffer再写数据文件
- Redo Log: 循环写，默认2个文件组，每个默认48MB
- Undo Log + MVCC: 读不阻塞写，但Purge线程清理旧版本
- Change Buffer: 对非唯一二级索引的插入/更新做延迟合并

**高并发写入关键参数：**
```
innodb_buffer_pool_size = 70%~80% RAM
innodb_log_file_size = 2GB~4GB       # Redo Log大小
innodb_log_buffer_size = 64MB~256MB
innodb_flush_log_at_trx_commit = 2   # 1=每次刷盘, 0=每秒, 2=每次提交写OS缓存
innodb_flush_method = O_DIRECT
innodb_io_capacity = 2000 (SSD)      # 后台I/O吞吐上限
innodb_write_io_threads = 8
innodb_doublewrite = OFF (有硬件原子写时可关)
```

**并发写入瓶颈：**
1. **Doublewrite开销**: 每次写操作对应2次I/O → 可关闭(需要支持原子写的SSD/Fusion-io)
2. **Redo Log大小**: 太小导致频繁检查点，写性能骤降
3. **行锁 + Gap Lock**: RR隔离级别下Gap Lock范围锁竞争严重 → RC级别放宽
4. **Change Buffer合并**: 大量插入时后台合并操作影响性能
5. **Purge线程**: 高并发UPDATE时Undo Purge可能滞后

**优化手段：**
- 使用RC隔离级别替代默认RR，减少Gap Lock
- 增大Redo Log减少检查点频率
- `innodb_flush_log_at_trx_commit = 2` 牺牲一点持久性换大幅吞吐
- 批量插入: `INSERT ... VALUES (...), (...), (...)`
- 分区表避免单表热点
- Group Replication (MGR) 实现多主写入扩展

**典型基准 (单机, SSD)：**
- ~3-8万 TPS (OLTP, 严格持久化)
- ~10-15万+ TPS (OLTP, 放宽持久化)
- 批量LOAD DATA: ~50万+ 行/秒

---

### 2.3 SQLite

**写入架构：**
- **Rollback Journal模式(默认)**: 写前复制原始页到journal → 修改数据文件 → 删除journal。读写互斥。
- **WAL模式(推荐)**: 修改追加到-wal文件 → 不阻塞读 → 定期checkpoint合并回主文件。**读不阻塞写，写不阻塞读，但同一时刻只有1个写者。**

**高并发写入关键参数：**
```sql
PRAGMA journal_mode = WAL;           -- 启用WAL (最关键)
PRAGMA synchronous = NORMAL;         -- FULL每次sync, NORMAL较少sync
PRAGMA wal_autocheckpoint = 4096;    -- WAL达到4096页时自动checkpoint
PRAGMA cache_size = -65536;          -- 64MB页缓存
PRAGMA mmap_size = 268435456;        -- 256MB内存映射I/O
PRAGMA busy_timeout = 5000;          -- 遇到锁时等待5秒
```

**并发写入瓶颈：**
1. **单写者模型**: WAL模式下同一时刻只能有1个写事务 → 极度不适合多客户端并发写入
2. **Checkpoint开销**: WAL文件超过阈值时，checkpoint触发大量I/O
3. **全库级锁**: 写入时对整个数据库文件加保留锁
4. **无网络支持**: 嵌入到进程内部，无法通过网络远程写入
5. **WAL文件增长**: 长事务或持续读会阻止checkpoint完成，WAL无限增长

**适用场景：**
- **单写入者场景**: 本地应用、移动App、嵌入式设备
- **读写分离**: WAL模式下多个读者+1个写者表现优秀
- **替代文件格式**: 配置文件存储、本地缓存等

**典型基准 (单线程写入, WAL模式)：**
- ~1千-5千 事务/秒 (默认配置, 每事务sync)
- ~1万-5万 插入/秒 (synchronous=NORMAL, 批量事务)
- **注意**: 并发写入>1时性能急剧下降，因WAL模式下串行化

---

### 2.4 MongoDB (WiredTiger)

**写入架构：**
- WiredTiger存储引擎: MVCC + B-Tree + WAL (Journal)
- Journal: 默认100ms间隔刷盘 (`commitIntervalMs = 100`)
- 文档级并发控制: WT使用乐观并发，写冲突自动重试
- 内存快照 + 定期Checkpoint
- 默认写入关注 (Write Concern): `{w: 1}` 即主节点确认即可

**高并发写入关键参数：**
```yaml
# mongod.conf
storage:
  wiredTiger:
    engineConfig:
      cacheSizeGB: 8           # WT缓存 (默认50% RAM)
      journalCompressor: snappy
    collectionConfig:
      blockCompressor: snappy
  journal:
    commitIntervalMs: 100      # 日志提交间隔

# 写入关注
writeConcern: { w: 1, j: false }  # 最快: 只等主节点确认，不刷journal
writeConcern: { w: "majority", j: true }  # 最安全但最慢
```

**并发写入瓶颈：**
1. **索引开销**: 每个索引都会增加写入负担 → 谨慎创建索引
2. **全局锁(早期版本)**: 4.0前有collection级锁，4.0+改进为文档级
3. **Journal与数据文件争用**: 同一磁盘时I/O竞争 → 分离Journal磁盘
4. **WT Cache压力**: 写入超出缓存时触发eviction，大量磁盘I/O
5. **Document大小**: 大文档(>16MB)写入慢，BSON序列化有开销

**优化手段：**
- `writeConcern: { w: 1, j: false }` 牺牲持久性
- 分离Journal到独立SSD
- 批量写入: `bulkWrite()` / `insertMany()`
- 预分配文档结构，避免文档增长导致的磁盘移动
- Sharding水平扩展写入能力

**典型基准 (单机, SSD, w:1)：**
- ~3-8万 文档/秒 (小文档, 带索引)
- ~10-15万 文档/秒 (无索引, 批量写入)
- Sharded集群: 近似线性扩展

---

### 2.5 Redis

**写入架构：**
- **纯内存操作**: 数据主要在内存，写入延迟亚毫秒级
- **单线程事件循环**: 命令执行串行化，无锁竞争
- **AOF持久化**: 追加命令日志，三种fsync策略:
  - `appendfsync always` → 每次命令sync (最安全, ~数万TPS)
  - `appendfsync everysec` → 每秒sync一次 (默认, 平衡)
  - `appendfsync no` → 由OS决定 (最快, 可能丢数据)
- **RDB快照**: 周期性全量快照(bgsave fork子进程)

**高并发写入关键参数：**
```
# redis.conf
appendonly yes
appendfsync everysec         # 每秒刷盘
save 900 1                   # RDB快照策略
maxmemory-policy noeviction  # 或 allkeys-lru

# 系统级
overcommit_memory = 1        # 允许bg-save fork
THP disabled                 # 关闭透明大页
```

**并发写入瓶颈：**
1. **单线程模型**: 一个Redis实例只用一个CPU核心 → 多实例部署利用多核
2. **网络带宽**: 10万QPS × 4KB payload = 3.2Gbps → 万兆网卡做高吞吐
3. **AOF fsync策略**: `always`模式下性能极差 → 推荐`everysec`
4. **BGSAVE fork**: 内存大时fork阻塞(通常数百ms, 取决于内存大小)
5. **Pipeline必要性**: 不用Pipeline时RTT开销显著

**官方基准数据 (redis-benchmark, 单实例, 50并发, 无Pipeline)：**
| 命令 | QPS | P50延迟 |
|------|-----|---------|
| SET (3 bytes) | ~180,000 | 0.143ms |
| LPUSH | ~188,000 | 0.135ms |
| GET | ~180,000 | 0.143ms |

**Pipeline模式 (P=16)：**
| 命令 | QPS |
|------|-----|
| SET | ~1,536,098 |
| GET | ~1,811,594 |

**优化手段：**
- 多实例部署: 每个CPU核心一个Redis实例
- 使用Pipeline减少RTT
- 关闭持久化仅做缓存时可达最高吞吐
- Redis Cluster: 16384个哈希槽自动分片
- 客户端连接池 + 批量命令

**适合场景：**
- 极致性能要求的写入: 缓存更新、计数器、排行榜、实时统计
- 消息队列 (List/Stream)
- 分布式锁、Session存储
- **不适合**: 需要复杂查询、事务ACID保证的大数据量持久存储

---

### 2.6 Apache Cassandra

**写入架构：**
- **LSM Tree (Log-Structured Merge Tree)**: 所有写入先追加到CommitLog(顺序写)，再写入MemTable(内存)
- **MemTable刷盘**: 内存中的MemTable满了之后，整个flush到SSTable(不可变)
- **Compaction**: 后台合并多个SSTable，去除重复数据和墓碑
- **无锁写入**: 追加操作无需获取锁，天然高并发
- **Multi-Master**: 所有副本均可接受写入，无主从之分

**高并发写入关键参数：**
```yaml
# cassandra.yaml
concurrent_writes: 32          # 并发写入线程数
concurrent_counter_writes: 16
memtable_heap_space: 2048MB    # MemTable总堆内存
memtable_offheap_space: 2048MB # MemTable堆外内存
commitlog_sync: periodic       # periodic或batch
commitlog_sync_period_in_ms: 10000  # periodic模式下10秒sync
compaction_throughput_mb_per_sec: 64  # Compaction吞吐限制
trickle_fsync: true            # 平滑fsync
```

**写入路径 (极简高效)：**
```
写入请求 → CommitLog (顺序写磁盘, 极快) → MemTable (内存)
                                              ↓ (满了)
                                         SSTable (不可变文件)
                                              ↓ (后台)
                                         Compaction (合并)
```

**并发写入优势：**
1. **追加写**: CommitLog是顺序写，不会有随机I/O瓶颈
2. **无锁**: LSM Tree天然不需在写入路径加锁
3. **水平扩展**: 增加节点即增加写入吞吐，近似线性
4. **多DC复制**: 每个DC独立写入，异步同步
5. **Tunable Consistency**: 写入一致性级别可调(ANY/ONE/QUORUM/ALL)

**并发写入瓶颈：**
1. **Compaction风暴**: 高写入产生大量SSTable，Compaction跟不上时性能骤降
2. **GC压力**: Java GC (建议G1GC或ZGC)
3. **MemTable满**: MemTable来不及刷盘时写被阻塞
4. **墓碑问题**: 大量DELETE操作产生墓碑，Compaction压力大
5. **热点分区**: 分区Key设计不当导致部分节点过热

**优化手段：**
- TWCS (TimeWindowCompactionStrategy) 用于时序数据
- 增大Memtable和CommitLog
- 降低Consistency Level: `ANY`或`ONE`提高吞吐
- 合理设置分区键，避免热点
- 限制Compaction吞吐，避免影响在线写入
- 使用SSD，Cassandra对磁盘I/O敏感(Compaction)

**典型基准 (3节点集群, SSD)：**
- 单节点: ~3-8万 写入/秒
- 3节点: ~10-25万 写入/秒
- 大规模集群: 百万级写入/秒 (线性扩展)

---

## 三、高并发写入性能排名

按照 **单机/小集群 高并发写入吞吐** 排名：

| 排名 | 数据库 | 单机写入TPS量级 | 并发模型 | 核心优势 |
|------|--------|----------------|----------|----------|
| 🥇 1 | **Redis** | 10万~180万+ | 单线程无锁 + Pipeline | 纯内存, 亚毫秒延迟 |
| 🥈 2 | **Cassandra** | 3万~8万/节点 (水平无限扩展) | LSM Tree追加写, Multi-Master | 水平扩展写入线性增长 |
| 🥉 3 | **PostgreSQL** | 5万~20万+ | MVCC + 多进程/WAL锁分段 | 成熟稳定, 优化手段丰富 |
| 4 | **MongoDB** | 3万~15万 | MVCC + 文档级锁 | 灵活Schema, 批量写入高效 |
| 5 | **MySQL** | 3万~15万 | MVCC + 行锁 + Gap Lock | OLTP经典, 生态丰富 |
| 6 | **SQLite** | 1千~5万 (单写者) | 单写者串行化 | 嵌入式, 零配置, WAL读不阻塞写 |

---

## 四、场景推荐

| 场景 | 首选 | 理由 |
|------|------|------|
| **极致吞吐的缓存/计数器** | Redis | 单实例18万+ QPS, Pipeline破百万 |
| **海量日志/时序数据摄入** | Cassandra | LSM天然适配追加写, 水平无限扩展 |
| **复杂事务+高并发OLTP** | PostgreSQL | MVCC成熟, 函数/触发器丰富, 扩展性好 |
| **灵活Schema快速迭代** | MongoDB | JSON文档模型, 水平分片, 开发效率高 |
| **传统关系型OLTP** | MySQL | 生态最成熟, ORM支持最好, 运维人才多 |
| **单机嵌入/移动端** | SQLite | 零配置, 无服务器进程, WAL模式读性能好 |

---

## 五、关键结论

1. **Redis是写入速度之王**: 纯内存+单线程无锁模型，但受限于内存容量和持久化策略权衡
2. **Cassandra的扩展性独一档**: LSM Tree配合水平扩展，写入吞吐随节点数量线性增长——这是其他5个数据库无法做到的
3. **SQLite不适合高并发写入**: WAL模式缓解了读写互斥，但单写者限制使其在>1并发写入时性能急剧下降
4. **PG vs MySQL**: PG的进程模型在高并发时内存开销更大，但WAL优化更先进(支持io_uring)；MySQL InnoDB的Doublewrite带来额外写放大，但线程池模型内存效率更高
5. **MongoDB的WiredTiger**: MVCC+文档级锁使其在JSON模型下表现优秀，但索引开销和Journal争用是高写入场景的潜在瓶颈

---

> 📊 数据来源: 各数据库官方文档、redis-benchmark官方基准、Cassandra Dynamo架构文档、PostgreSQL WAL文档、SQLite WAL文档、MongoDB WiredTiger文档
