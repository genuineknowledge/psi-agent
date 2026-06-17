import { flow } from "@agent-flow/core";

const agent = flow.agent("summarizer", { model: "claude-haiku-4-5-20251001" });

export default flow.define("simple-summarizer", async (input: string) => {
  return await agent(`Summarize: ${input}`);
}, {
  programPath: new URL(import.meta.url).pathname,
  runsDir: new URL("./runs", import.meta.url).pathname,
});
