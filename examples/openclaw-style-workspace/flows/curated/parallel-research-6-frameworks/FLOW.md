---
name: parallel-research-6-frameworks
description: Parallel N-framework technology research: fan-out researcher agents → synthesizer merges into structured comparison report with matrix, deep-dive, selection guide, and trends.
category: research
created_by: agent
created_at: 2026-06-16T16:47:29Z
updated_at: 2026-06-16T17:52:35Z
---

Parallel N-framework technology research — fan-out one researcher agent per framework, then a synthesizer merges findings into a structured comparison report with matrix, deep-dive, selection guide, and trends analysis.

## Flow Architecture
1. **Fan-out**: One researcher agent per framework (parallel)
2. **Synthesizer**: Merge all findings into structured comparison matrix
3. **Output**: Comparison matrix, deep-dive sections, selection guide, trend analysis

## When to Use
- Technology stack evaluation
- Framework/library selection
- Tool comparison research
- Build-vs-buy analysis

```typescript
import { flow } from "@agent-flow/core";

export default flow({
  name: "parallel-research-6-frameworks",
  description: "Parallel N-framework technology research with comparison matrix",
  agents: {
    researcher: {
      task: "Research the assigned framework/tool comprehensively: features, pros/cons, community, performance, learning curve, and production readiness.",
      model: "claude-sonnet-4-5",
    },
    synthesizer: {
      task: "Merge all researcher findings into a structured comparison report with: 1) comparison matrix, 2) deep-dive per framework, 3) selection guide by scenario, 4) trend analysis.",
      model: "claude-sonnet-4-5",
    },
  },
  steps: [
    {
      id: "fan-out",
      agent: "researcher",
      parallel: true,
      inputs: "{{frameworks}}",
    },
    {
      id: "synthesize",
      agent: "synthesizer",
      dependsOn: ["fan-out"],
      inputs: {
        findings: "{{fan-out}}",
        dimensions: "{{comparison_dimensions}}",
      },
    },
  ],
});
```