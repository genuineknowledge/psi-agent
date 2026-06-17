import { flow } from "@agent-flow/core";

const researcher = flow.agent("researcher", { model: "claude-sonnet-4-6" });
const reviewer = flow.agent("reviewer", { model: "claude-sonnet-4-6" });

export default flow.define("test-promote-flow", async (topic: string) => {
  const [resA, resB] = await flow.parallel([
    () => researcher(`Research aspect A of: ${topic}`),
    () => researcher(`Research aspect B of: ${topic}`),
  ]);
  return await reviewer(`Review and combine:\nA: ${resA}\nB: ${resB}`);
}, {
  programPath: new URL(import.meta.url).pathname,
  runsDir: new URL("./runs", import.meta.url).pathname,
});
