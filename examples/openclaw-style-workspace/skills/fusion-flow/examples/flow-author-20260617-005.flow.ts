// PRIMITIVES: agent, session, output
// SCENARIO: Quick test - single session with short prompt
// AUTHORED: 2026-06-17 03:22 by Fuclaw authoring mode for diagnostics

import { run } from "../runtime/agent-flow-core.bundle.mjs";

await run(
  async ({ flow, save }) => {
    const tester = flow.agent({
      name: "tester",
      system: "你是 Python 专家。回答简洁、准确。",
    });

    const result = await flow.session(
      tester,
      "用 5 句话解释 Python asyncio 事件循环的核心工作原理。",
    );

    await flow.output("final", result);
    console.log("\n=== Result ===\n", result);
  },
  {
    programPath: new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"),
  },
);
