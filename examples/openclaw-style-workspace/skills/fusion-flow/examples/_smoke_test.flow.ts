import { run } from "../runtime/agent-flow-core.bundle.mjs";

await run(
  async ({ flow, save }) => {
    const msg = await flow.input("msg", "hello");
    await flow.output("echo", msg);
    console.log(msg);
  },
  {
    programPath: new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]):/, "$1"),
  },
);
