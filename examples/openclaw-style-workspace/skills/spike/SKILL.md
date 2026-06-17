---
name: spike
description: Time-boxed technical investigation — define a question, explore, summarise findings, and give a recommendation.
---

A spike is a focused investigation to answer a specific technical question before committing to an implementation.

**Process:**

1. **Define the question** — write a single, answerable question (e.g. "Can we use library X for Y?").
2. **Set scope** — note what is in/out of scope for this spike (time-box mentally).
3. **Explore** — use available tools (web search, read files, run code) to gather evidence.
4. **Document findings** — bullet points: what you found, what you tried, what failed.
5. **Recommend** — end with a clear go/no-go or preferred approach with a one-line rationale.

**Output format:**

```
## Spike: <question>

### Findings
- ...

### Recommendation
<go/no-go and why>
```

Do not over-engineer the investigation. Stop when you can answer the question.
