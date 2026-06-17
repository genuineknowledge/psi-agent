// PRIMITIVES: agent, parallel, session, output
// SCENARIO: Quick test - parallel two sessions with short prompts
// AUTHORED: 2026-06-17 03:23 by Fuclaw authoring mode for diagnostics

import { run } from "../runtime/agent-flow-core.bundle.mjs";

await run(
  async ({ flow, save }) => {
    const a1 = flow.agent({
      name: "researcher_a",
      system: "你是 Python 专家。回答简洁准确。只输出3-5句话。",
    });

    const a2 = flow.agent({
      name: "researcher_b",
      system: "你是 Python 专家。回答简洁准确。只输出3-5句话。",
    });

    const [r1, r2] = await flow.parallel([
      async () => flow.session(a1, "解释 asyncio 事件循环的核心原理。"),
      async () => flow.session(a2, "解释 uvloop 比 asyncio 快的主要原因。"),
    ]);

    await flow.output("r1", r1);
    await flow.output("r2", r2);
    console.log("\n=== R1 ===\n", r1);
    console.log("\n=== R2 ===\n", r2);
  },
  {
    programPath: new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"),
  },
);
