import { flow } from "@agent-flow/core";

const agent = flow.agent("worker", { model: "claude-sonnet-4-6" });

export default flow.define("test-patch-flow", async (topic: string) => {
  // TODO: add parallel processing
  return await agent(`Research: ${topic}`);
}, {
  programPath: new URL(import.meta.url).pathname,
  runsDir: new URL("./runs", import.meta.url).pathname,
});
