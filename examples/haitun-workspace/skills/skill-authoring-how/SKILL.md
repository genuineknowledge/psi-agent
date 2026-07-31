---
name: skill-authoring-how
description: "HOW to write and save a skill via skill_manage. REQUIRED after skill-authoring-when. Prefer patch over create; body must include When to use / When not to use / Instructions (or equivalent procedure). Never raw-write under skills/."
category: knowledge-base
---

# 如何编写与落盘 Skill

先通过 `skill-authoring-when` 判定；再按本文调用 `skill_manage`。

## When to use

- 已决定要 **patch** 或 **create** 一个可复用 skill。

## When not to use

- 尚未 list / 尚未确认无同类 skill（回 `skill-authoring-when`）。  
- 应用 `write`/`edit` 直接改 `skills/**`（禁止；一律走 `skill_manage`）。

## Procedure

### A. 更新已有（默认路径）

```text
skill_manage(action="list")
skill_manage(action="view", skill_name="…")
skill_manage(action="patch", skill_name="…", content="<完整新正文，不含 frontmatter>")
```

- `patch` 会替换 **body**（保留 frontmatter；可写 `agent_editable: true` 的底座 skill，或 `created_by: agent` 的 skill）。  
- 合并用户新规则时：**改对应章节**，保留原流程骨架；在节首用一句话注明「用户规则 / 日期」可选。  
- 简历域：优先 patch `feishu-resume-review` 的「评分规则」「面试提问建议」。

### B. 新建（仅 when 判定通过）

```text
skill_manage(
  action="create",
  skill_name="kebab-case-name",
  description="英文短描述，含触发短语",
  category="productivity|knowledge-base|…",
  content="<正文，不含 frontmatter>"
)
```

正文至少包含：

1. `## When to use`  
2. `## When not to use`  
3. `## Instructions` 或等价「流程 / Procedure」  
4. 需要用户可改的量表/规则 → 独立成节，便于以后 patch  

可选 frontmatter 语义（create 时由 tool 写 `created_by: agent`）：

- 底座类、希望用户规则持续合并进来 → 在 create 后如需，可再说明；仓库预置底座用 `agent_editable: true`。

### 命名

- 类级、稳定：`feishu-resume-review`，不要 `fix-pr-123`、`debug-today`。  
- 禁止保留名：`fusion-flow`、`skill-authoring-when`、`skill-authoring-how`、`_universal`、`_*`。

## Boundaries

- 禁止手写 `skills/<name>/SKILL.md` 绕过 tool。  
- 禁止在未 list 的情况下 create。  
- 同域规则叠加 → patch，不 create。
