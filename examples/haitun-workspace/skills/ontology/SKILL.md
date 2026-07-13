---
name: ontology
description: Build a typed knowledge graph and structured agent memory as a relation layer over the LLM wiki. LOAD when the user asks to model entities/relations, track structured facts with types, answer "how is X related to Y", or give the wiki a queryable schema. Not for free-text notes (use llm_wiki) or short-lived task state (use todo).
category: agent
---

# Ontology — 带类型的知识图谱 / 结构化 agent 记忆

给 agent 一个**带类型的知识图谱**:实体有类型(`Person`/`Paper`/`Concept`/`Tool`…),
关系是**有方向、有类型的边**(`authored_by`/`cites`/`part_of`/`instance_of`…)。
它是 [[llm_wiki]] 的**结构化关系层** —— wiki 存自由文本,ontology 存可查询的类型与关系,
两者用 `[[wikilink]]` 互相指向,让技能可组合。

**纯文件约定,零新依赖**:全部用现有通用工具(`write`/`read`/`edit`/`list_dir`/`find_files`/`search_content`)
读写 `<workspace>/ontology/` 下的 Markdown。不需要也不要引入任何图数据库或本体库。

---

## 何时 LOAD / 不 LOAD

| 用 ontology | 用别的 |
|------|------|
| 建模实体与它们**之间的关系**("A 引用 B""X 属于 Y") | 纯自由文本笔记 → [[llm_wiki]] |
| 需要**类型约束 / schema**("只允许这几类实体和关系") | 本轮临时执行清单 → `todo` |
| 结构化问答:"谁写了 X""X 的上游依赖有哪些""从 A 到 B 有没有路径" | 问用户要什么 → `clarify` |
| 长期结构化记忆,跨会话可遍历 | 一次性偏好/事实 → `memory_add` |

**与 llm_wiki 的分工**:wiki 页面是"文章";ontology 实体是"图里的节点+带类型的边"。
一个概念常常两边都有:wiki 页写它是什么,ontology 实体记它和别的节点的类型化关系,
并在 `wiki:` 字段用 `[[slug]]` 指回 wiki。

---

## 存储布局(`<workspace>/ontology/`)

```
ontology/
  schema.md              # TBox:允许的实体类型 + 关系类型(含 domain/range/inverse)
  entities/<slug>.md     # ABox:每个实体一个文件,frontmatter 存类型/属性/关系
```

- **slug 规则**:小写、非字母数字转 `-`、首尾去 `-`(与 llm_wiki `slugify` 一致),
  这样 ontology 实体的 slug 能直接对上同名 wiki 页的 slug。
- 所有读写走通用文件工具;**不要**用 `wiki_write` 存实体 —— 它只保留固定 frontmatter 字段,
  会**丢弃** `type`/`relations` 等自定义键。

### `schema.md` 格式

```markdown
---
entity_types:
  Person:   { description: "人:作者/研究者/用户" }
  Paper:    { description: "论文或技术报告" }
  Concept:  { description: "抽象概念/方法/架构" }
  Tool:     { description: "软件/库/CLI/agent 工具" }
relation_types:
  authored_by: { domain: [Paper], range: [Person], inverse: authored }
  cites:       { domain: [Paper], range: [Paper], inverse: cited_by }
  instance_of: { domain: ["*"],   range: [Concept], inverse: has_instance }
  part_of:     { domain: ["*"],   range: ["*"],     inverse: has_part }
  depends_on:  { domain: [Tool],  range: [Tool],    inverse: dependency_of }
---

# Schema

本体的类型定义。新增类型前先读这里;需要新类型就在此登记(附一句 description),
保持 schema 与实体一致。`domain`/`range` 用 `"*"` 表示不限类型。
```

### `entities/<slug>.md` 格式

```markdown
---
id: rotary-positional-embeddings
type: Concept
labels: [RoPE, Rotary Positional Embeddings]   # 别名,便于检索
wiki: "[[rotary-positional-embeddings]]"        # 指回 llm_wiki 同名页(可空)
attributes:
  year: 2021
relations:
  - { predicate: instance_of, target: positional-encoding }
  - { predicate: cited_by,    target: roformer-paper }
created: 2026-07-13T00:00:00Z
updated: 2026-07-13T00:00:00Z
---

# Rotary Positional Embeddings

一句话说明这个节点是什么(详情放 wiki 页,这里只放图谱需要的结构)。
```

**约定**:
- `relations` 只存**出边**;查入边(反向)靠遍历所有实体(见下)。登记正向边时,
  优先用 schema 里定义了 `inverse` 的谓词,保证图是可双向遍历的。
- `target` 用**目标实体的 slug**(不是标题)。目标实体可以先不存在(悬空边),
  就像 wiki 的 broken link —— 是"接下来该建的节点"的信号。
- 时间戳用 ISO-8601 UTC。

---

## 操作配方

所有操作 = 读/写 `ontology/` 下文件 + 维护一致性。开始一句、结束给摘要,**中间静默**。

### 1. 建/改实体 (assert)
1. `read` `ontology/schema.md`(不存在就先按上面的模板 `write` 一个基础 schema)。
2. 校验实体 `type` 与每条关系的 `predicate` 都在 schema 里;缺就先补 schema。
3. 校验每条关系的 `domain`/`range` 与两端实体类型相符,不符则报告并让用户确认。
4. `read` `entities/<slug>.md`:存在则 `edit`(保留 `created`,更新 `updated`),
   不存在则 `write`(`created`=`updated`=now)。
5. 对有 `inverse` 的谓词,在**目标实体**文件里补一条反向边(目标不存在时可跳过,留作悬空)。

### 2. 查实体 / 邻居 (get)
- `read` `entities/<slug>.md` 拿类型、属性、出边。
- **入边(反向)**:`search_content` 在 `ontology/entities/` 里搜 `target: <slug>`,
  或 `list_dir` 后逐个 `read`,收集所有指向它的边。
- 需要文本细节时,顺着 `wiki:` 的 `[[slug]]` 用 `wiki_read` 取 wiki 正文。

### 3. 关系查询 / 找路径 (query)
- "谁 authored X" → `read` X 的实体,看 `authored_by`。
- "X 依赖什么" → 出边里 `depends_on` 的 `target`。
- "A 到 B 有没有路径" → 从 A 出边做**广度优先**:逐跳 `read` target 实体、
  展开其出边,直到命中 B 或用尽(**设跳数上限 ~4**,并记已访问 slug 防环)。
- 结果按 `{起点, 边序列, 终点}` 汇报,并列出沿途悬空/缺失的目标。

### 4. 校验 (validate)
- `list_dir` `entities/` → 逐个 `read`:
  - 每个 `type` 在 schema? 每个 `predicate` 在 schema?
  - 每条边 `target` 对应的实体文件是否存在(悬空边 = 建议下一步补的节点)?
  - 有 `inverse` 的边,反向边是否也在目标里(缺就补)?
- 汇报:未知类型/谓词、悬空边、缺失的反向边。

### 5. 删实体 (retract)
1. 先 `search_content` 找出所有指向它的入边,`delete` 前告知用户这些会变悬空。
2. 删实体文件;按用户意愿清理或保留对端的反向边。

---

## 与其他技能组合(可组合性)

- **[[llm_wiki]]**:wiki = 文本层,ontology = 关系层。写 wiki 页后,若它和别的节点有类型化关系,
  就在 ontology 建对应实体并用 `wiki:` 回指。用户问"关系/路径/类型"走 ontology,问"细节/解释"走 wiki。
- **`memory_add` / `memory_search`**:一次性偏好或事实进 memory;需要**类型与结构化关系**时进 ontology。
- **`clarify`**:类型/关系有歧义(该建哪类实体、用哪个谓词)时先问,别乱建 schema。
- **`todo`**:批量导入或大范围重构图谱时,用 todo 跟踪进度。

---

## 原则

- **schema 先行、保持一致**:先看/补 `schema.md`,再断言实体;类型和谓词都要有登记。
- **slug 对齐 llm_wiki**:同一概念两边同 slug,`[[wikilink]]` 才互通。
- **悬空边是特性不是错误**:等于 wiki 的 broken link,标记"下一个该建的节点"。
- **零依赖**:只用现有文件工具读写 Markdown,永远不要为此加图数据库/本体库依赖。
- **静默维护**:建/改多文件时别逐步播报,开头一句、结尾给结构化摘要。
