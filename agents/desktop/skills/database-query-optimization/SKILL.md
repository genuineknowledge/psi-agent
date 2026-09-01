---
name: database-query-optimization
description: "Optimizing a SQL query (SQLite/Postgres/MySQL) to run faster while producing identical output: reading the execution plan, rewriting query structure (eliminate repeated scans, replace correlated subqueries with joins/window functions, prune needless DISTINCT/ORDER BY), and indexing. Use when a task gives a slow query and asks you to speed it up or produce an equivalent efficient query. NOT for recovering/forensically parsing a corrupted database file."
---
### Optimize against the GRADING environment, not a souped-up copy
The most damaging trap in query-optimization tasks: you speed the query up on a
database YOU modified (added indexes, ran ANALYZE, rebuilt), conclude "2s vs 3min,
solved", but the grader runs your deliverable on the ORIGINAL, unmodified database.
*   **Know what your deliverable may and may not change.** If the task wants a single query file (one statement, no comments), then you CANNOT ship `CREATE INDEX`/`PRAGMA`/`ANALYZE` — the grader executes only your query, on the database as given. A query that is only fast *because you pre-built indexes on a scratch copy* will be just as slow (or slower, if you added window functions/extra CTEs) when the grader runs it on the bare original. Your measured speedup on the indexed copy is meaningless to the score.
*   **Do your FINAL timing and output-equivalence check on the original database**, exactly as the grader will (run your query file against the original DB with the dialect's CLI, e.g. `sqlite3 <original.db> < <your_query.sql>`). A scratch copy with indexes is fine for *exploring* "what would help" — but the number that counts is the runtime on the unmodified DB. If it's still slow there, you have NOT optimized it.
*   **One fast reading is NOT proof — judge by the WORST/cold-cache time, and by the plan, not a single wall-clock number.** A query you've already run several times reads from the OS/DB page cache and can clock e.g. 3s, while the SAME query on the SAME DB takes minutes cold. The grader runs your query ONCE in a fresh container with a cold cache, so it sees the worst case. If repeated timings of the same query swing wildly (seconds vs minutes), the query is NOT actually optimized — it just occasionally hit a warm cache; do not trust the lucky fast run. Time it ≥2–3 times (ideally after dropping caches / on a fresh copy) and treat the SLOWEST as the real cost. The reliable signal that it's genuinely fast is that `EXPLAIN QUERY PLAN` shows the full-table `SCAN`/`TEMP B-TREE` nodes are gone — confirm that before believing any single low timing.
*   **If schema changes ARE allowed** (the task lets you add indexes / a setup script), then add the indexes the plan calls for — but say so explicitly and make sure they're part of what the grader runs, not just your scratch DB.

### Diagnose with the execution plan before rewriting
*   **Read the plan of the ORIGINAL query first:** `EXPLAIN QUERY PLAN <query>` (SQLite) / `EXPLAIN ANALYZE` (Postgres). Look for `SCAN` (full table scans), `USE TEMP B-TREE FOR ORDER BY`/`GROUP BY` (sorting/hashing spills), and repeated subquery execution. That tells you WHERE the time goes — optimize that, don't guess.
*   **Inspect the schema and existing indexes:** `PRAGMA table_info(t)`, `PRAGMA index_list(t)`, `.schema`. The query may already have usable indexes, or a join key may be unindexed.
*   **Compare candidate plans:** prefix each rewrite with `EXPLAIN QUERY PLAN` and confirm the expensive SCAN/TEMP-B-TREE nodes are gone before trusting a wall-clock measurement.

### Structural rewrites that cut work (no schema change needed)
*   **Compute each thing once.** If the original runs the same subquery per row (a correlated subquery) or scans a table multiple times, hoist it into a CTE / derived table computed a single time, then join to it.
*   **Replace correlated subqueries and self-joins with window functions** where possible: a per-group "top-1" or "rank" done via `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...)` is usually far cheaper than a correlated `MAX`/`NOT EXISTS` subquery — but verify on the plan, since on an unindexed table a window sort can itself be costly.
*   **Push filters down and prune early:** apply `WHERE`/`HAVING` as early as possible (inside the CTE that first produces the rows) so later joins operate on fewer rows.
*   **Remove needless work:** drop `DISTINCT` when a `GROUP BY` or key already guarantees uniqueness; drop `ORDER BY` in subqueries whose order doesn't matter; avoid `SELECT *` feeding a wrapping aggregate.
*   **Aggregate before joining** when you only need grouped values from a large table joined to a small one — group the big table down first, then join the small dimension tables.

### Preserve exact output — equivalence is part of "correct"
*   **The rewrite must produce byte-identical results to the original**, including row count, column order/names, and ORDER BY (a faster query with a different row order or a dropped `LIMIT` fails). Re-read the original for a trailing `LIMIT`/`ORDER BY` that's easy to miss.
*   **Verify by diffing both outputs on the original DB:** run the original and your candidate, sort/compare the full result sets (not just the first rows), and confirm counts match. Only then is "faster" also "correct".
*   **Match the deliverable contract exactly:** single statement vs multiple, semicolon termination, no comments, the named output path, and the SQL dialect the grader uses (don't use Postgres-only syntax for a SQLite grader).
