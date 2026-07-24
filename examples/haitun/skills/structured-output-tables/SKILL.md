---
name: structured-output-tables
description: Use for 3+ parallel items (options, products, brands, steps) with the same fields — table required. For 2 items, table only for dichotomy or exhaustive pairs; otherwise prose. Includes shopping/recommendation lists (price, pros, fit per row).
category: output
---

# Structured output as Markdown tables

## Decision rule (read this first)

Apply this skill in two stages: **count the parallel items**, then **decide format**.

```
How many parallel items share the same "shape" (same fields / same question)?

  1 item        → normal prose (this skill does not apply)

  2 items       → see "Exactly two items" below (table OR prose)

  3+ items      → MUST use a Markdown table (required)
```

**Parallel items** means entries you would otherwise list with the same structure: multiple options, multiple files each with status, multiple steps each with outcome, etc. They are **related** when they answer the same question or belong to one comparison set.

---

## Three or more items — table required

When there are **three or more** related parallel items, you **must** output a Markdown table. Do not substitute a long bullet list or multiple paragraphs for the same data.

Typical cases:

- Comparing 3+ tools, models, approaches, or configurations
- **Recommending 3+ products, brands, dishes, apps, or purchase options** (same columns: name, price, pros, fit)
- Enumerating files, dependencies, endpoints, or test cases (each with path / status / note)
- Checklists: many steps or requirements with done / pending / blocked
- Summarizing several sub-results that need aligned columns

**Natural-language triggers** (user will NOT say "use a table"): 推荐几个、有什么选择、哪款、买什么、哪个好、分别怎么样、帮我挑、几个方案、几个牌子。

### Product / recommendation lists (3+ options) — one table, not N sections

When you recommend **three or more** concrete options (shoes, apps, restaurants, configs, etc.) that share attributes like **price, strengths, who it's for**, you **must** put them in **one comparison table first**.

Required columns (adapt names to context):

| Option | Price (approx.) | Strengths | Best for |
|--------|-----------------|-------------|----------|

Example — running shoes for a student:

| Model | Price (approx.) | Strengths | Best for |
|-------|-----------------|-------------|----------|
| 必迈 远征者 Pure | ¥190–240 | Thick cushioning, knee-friendly | Beginners, comfort-first |
| 李宁 超轻 20 | ¥200–260 | Very light, good looks | Want light + casual wear |
| 多威 征途 MAX | ¥170–220 | Cheapest reliable, durable | Tight budget |
| 安踏 柏油路霸 | ¥220–280 (on sale) | Big brand, good cushioning | Wait for discounts |

After that table: **1–3 sentences** with your single pick and why. Optional short bullets for **buying tips** (sales, 闲鱼) — but do **not** repeat each product as a separate `###` section with the same fields.

**Anti-pattern for recommendations:** four headings `### 必迈 …` / `### 李宁 …` each with Price / 适合 / 关键词 in prose, then a tiny summary table at the end. That violates the required-table rule — merge into one table up front.

Templates:

**Comparison (3+ options)**

| Option | Pros | Cons | Recommendation |
|--------|------|------|----------------|
| … | … | … | — |

**Status / checklist (3+ rows)**

| Item | Status | Evidence |
|------|--------|----------|
| … | Done / Pending / Failed | … |

After the table: **2–4 sentences** — overall recommendation, main blocker, or next step. Do not re-list every row in prose.

---

## Exactly two items — sub-classify before choosing table vs prose

With **exactly two** items, **do not automatically use a table**. First classify:

### Use a table (2×2) when either condition holds

**A. Dichotomy / excluded-middle contrast**

The two items are **two sides of one decision**, not two independent tips. Choosing one side logically addresses the question; they are mutually framed opposites.

Signals:

- Do / don't; adopt / skip; enable / disable
- Before / after (same subject, one change)
- If you X / if you don't X (same X)
- Success path / failure path for the **same** action
- User asks «这么做会怎样，不这么做会怎样»

Examples that **should** be a table:

| Situation | Table? |
|-----------|--------|
| 启用 Gateway 托盘 vs 不启用 | Yes — one decision, two outcomes |
| 改 `AGENTS.md` 前 vs 改后 | Yes — same subject, temporal contrast |
| 用 `uv run` vs 继续用 `psi-agent.exe` | Yes — deployment dichotomy for same goal |

Minimal templates:

| | If you do | If you don't |
|---|-----------|--------------|
| **Outcome** | … | … |
| **Risk / cost** | … | … |

| Option | Pros | Cons |
|--------|------|------|
| A | … | … |
| B | … | … |

**B. Exhaustive pair (strongly related, covers most common cases)**

The two items are **not strict opposites**, but together they form a **complete or near-complete split** of the situation space the user cares about. A third option would be edge-case or out of scope.

Signals:

- «通常只有两种情况…» / most users choose one of two paths
- Online vs offline; dev vs prod install; CLI vs Web UI for the **same** task
- Two modes that partition the workflow (not two random suggestions)

Examples that **should** be a table:

| Situation | Table? |
|-----------|--------|
| 开发版（`uv run`）vs 用户安装版（exe） | Yes — two main deployment modes |
| REST API 派 subagent vs 三段式 CLI 派 subagent | Yes — two architecture paths for same goal |

If unsure whether the pair is exhaustive: ask internally «would a third row be a minor variant or a totally different answer?» — if minor, table is OK; if a third common path exists, you likely have **3+** and must use a multi-row table including it.

### Use normal prose (no table) when

The two items are **independent suggestions**, tips, or follow-ups — same topic area but **not** two columns of one matrix. Either stand alone; the user could follow both, neither, or one without choosing against the other.

Signals:

- «建议一… 建议二…» with no either/or
- Two optional improvements, not mutually exclusive
- Two warnings about different risks (not do/don't of one action)
- Two next steps of equal priority (do A **and** B, or either order)

Examples that **should NOT** be a table:

| Situation | Format |
|-----------|--------|
| 「可以先 `uv sync`，另外记得构建 spa」 | Prose or two bullets — both can apply |
| 「检查 PATH」和「检查 API key 是否设置」 | Prose — independent checks |
| 「读交接报告」和「先手动冒烟 Gateway」 | Prose — two tips, not a 2×2 matrix |

Use short bullets or a short paragraph. A **完成情况** block (see `task-self-review` skill when present) is enough; do not force a 2-row table.

After prose output: still end with **1–2 sentences** of priority if one suggestion matters more.

---

## When NOT to use this skill at all

- Single fact, single answer, or yes/no with no item set to structure
- Pure narrative, story, or explanation with no parallel rows
- User explicitly asked for no tables, bullets only, or prose only
- Secrets (API keys, tokens, passwords) — never in tables

---

## Output contract (when a table is required or chosen)

1. **GitHub-flavored pipe tables** only (`| col | col |`), not HTML `<table>`.
2. **Header row + separator row** required (`|---|---|`).
3. **Short column headers** — concrete names (`Option`, `If you do`, `Status`).
4. **One fact per cell**; very long text → truncate with `…` and put full text below the table.
5. **Unknown / N/A** → `—`, not blank cells.
6. After the table, **synthesize** — do not repeat every cell in prose.

## Anti-patterns

- **3+ parallel items as bullets only** — violates the required-table rule.
- **3+ product/options as separate `###` sections** — each with Price / 适合 / 特点 in prose; use **one table** instead, then a short recommendation.
- **Summary table only at the end** — if you already listed 3+ options in prose, you still failed; the main comparison table must come **before** the narrative pick.
- **2 independent tips forced into a 2×2 table** — looks structured but misleads (implies either/or when both apply).
- **Fake dichotomy** — «方案 A / 方案 B» when B is «also do A plus X»; use prose or 3+ table if a third real option exists.
- Same data as both bullets and a table.
- Invented columns not supported by the source.

## Quick reference

| Count | Relationship | Output |
|-------|----------------|--------|
| 1 | — | Prose |
| 2 | Dichotomy (do/don't, before/after) | **Table** |
| 2 | Exhaustive pair (two main modes) | **Table** |
| 2 | Independent suggestions / tips | **Prose** (bullets OK) |
| 3+ | Related parallel items (incl. products to buy) | **Table required** — one table, rows = options |
