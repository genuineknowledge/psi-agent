---
name: summarize
description: Produce a concise bullet-point summary of documents, code, or conversations.
---

When asked to summarize:

1. **Read the full content first** — don't summarize what you haven't read.
2. **Identify the key points** — what are the 3–7 most important things?
3. **Write the summary:**

**For documents:**
```markdown
## Summary: <title or topic>

**Purpose:** One sentence on what this document is about.

**Key Points:**
- Point 1
- Point 2
- ...

**Action Items / Decisions:** (if any)
- ...
```

**For code:**
- State what the module/function does in one sentence.
- List inputs, outputs, and side effects.
- Note any non-obvious logic or caveats.

**For conversations:**
- State the topic and participants (if known).
- List decisions made.
- List open questions or next steps.

**Rules:**
- Be specific: "returns a list of user IDs sorted by last login" not "returns some data".
- One bullet = one idea. No compound bullets.
- Omit obvious context that the reader already knows.
- If the source is long, note the page/section range covered.
