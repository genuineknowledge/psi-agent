import { flow } from "@agent-flow/core";

const researcher = flow.agent("researcher", { model: "claude-sonnet-4-6" });
const analyst = flow.agent("analyst", { model: "claude-sonnet-4-6" });
const writer = flow.agent("writer", { model: "claude-sonnet-4-6" });

export default flow.define("data-analysis-pipeline", async () => {
  const datasets = ["sales", "users", "events"];
  const results = await flow.parallel(
    datasets.map((ds) => () => researcher(`Analyze the ${ds} dataset`)),
  );
  const synthesis = await analyst(`Synthesize:\n${results.join("\n")}`);
  return await writer(`Write report based on: ${synthesis}`);
}, {
  programPath: new URL(import.meta.url).pathname,
  runsDir: new URL("./runs", import.meta.url).pathname,
});
