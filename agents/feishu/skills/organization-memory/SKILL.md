---
name: organization-memory
description: Use when a conversation contains stable project, organization decision, shared process, constraint, status, or reference information that other organization members may need later.
---

# Organization Memory

Use organization memory for stable, reusable, traceable shared facts. Personal preferences, private schedules, and personal notes remain personal memory.

## Decide the Scope

| Information | Action |
|---|---|
| Project context, confirmed decision, shared process, organization constraint, public status, shared reference | Use organization memory |
| Personal preference, private schedule, individual note or commitment | Use personal memory |
| Transient chat, speculation, unconfirmed draft, one-time execution detail | Do not write memory |
| Work assignment participants, state, feedback, and delivery progress | Use `assignment_*` as the authoritative record |

For a question about shared project or organization information, call `memory_search` or `memory_answer_context` with `visibility="organization"`. For private user information, use `visibility="personal"`. If both are needed, make two calls and keep their evidence separate.

## Write One Fact

Call `organization_memory_add` only when all are true:

- The fact will remain useful to other members.
- The statement is confirmed and can stand alone.
- A stable `source_ref` identifies its evidence.
- The content does not expose private information.

一次只写一条独立事实，不保存聊天全文。不得把推测、闲聊、草稿或临时过程写成组织事实。相同事实无需重复写入；事实变化时新增一条，并在明确取代旧事实时传 `supersedes_fact_id`。

`category` must be exactly one of `project_context`, `decision`, `status`, `process`, `constraint`, `shared_reference`.

`source_type` must be exactly one of `feishu_message`, `feishu_doc`, `repository`, `task`, `other`.

Always include `content`, `category`, `source_type`, and `source_ref`. Optional fields are `project`, `observed_at`, `supersedes_fact_id`, and short `tags`. Never supply organization, user, workspace, Session, or Feishu identity; the service derives them.

If evidence is missing, do not guess and do not write. If an organization operation fails, do not fall back to personal memory or claim success. Do not repeatedly alter tool arguments or search code to infer the schema; use the enum and fields above.

Successful storage needs no separate process message. Mention a failure only when it changes the truthfulness or completeness of the current answer.
