// @agent-flow/core - bundled runtime. Source: https://git.kclab.cloud/industry-academia-research/FuClaw-OpenProse

// src/index.ts
import { config as dotenvConfig } from "dotenv";

// src/run.ts
import { AsyncLocalStorage as AsyncLocalStorage2 } from "node:async_hooks";
import { mkdir, writeFile as writeFile2, copyFile, readdir, readFile, stat, rm } from "node:fs/promises";
import path2 from "node:path";

// src/cli-engine.ts
import { existsSync } from "node:fs";
import { delimiter, join } from "node:path";
function parseClaudeJson(raw) {
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const o = parsed;
  let text;
  if ("structured_output" in o && o.structured_output != null) {
    text = JSON.stringify(o.structured_output);
  } else {
    text = typeof o.result === "string" ? o.result : "";
  }
  const meta = {};
  if (typeof o.session_id === "string") meta.sessionId = o.session_id;
  if (typeof o.total_cost_usd === "number") meta.costUSD = o.total_cost_usd;
  if (typeof o.duration_api_ms === "number") {
    meta.durationApiMs = o.duration_api_ms;
  }
  if (typeof o.model === "string") meta.model = o.model;
  const modelUsage = o.modelUsage;
  if (modelUsage && typeof modelUsage === "object") {
    const keys = Object.keys(modelUsage);
    if (keys.length > 0) {
      const firstKey = keys[0];
      if (!meta.model) meta.model = firstKey.replace(/\[[^\]]*\]$/, "");
      const entry = modelUsage[firstKey];
      if (entry && typeof entry === "object") {
        if (typeof entry.contextWindow === "number") {
          meta.contextWindow = entry.contextWindow;
        }
      }
    }
  }
  const usage = o.usage;
  if (usage && typeof usage === "object") {
    const tokenUsage = {
      input: typeof usage.input_tokens === "number" ? usage.input_tokens : 0,
      output: typeof usage.output_tokens === "number" ? usage.output_tokens : 0
    };
    if (typeof usage.cache_read_input_tokens === "number") {
      tokenUsage.cacheRead = usage.cache_read_input_tokens;
    }
    if (typeof usage.cache_creation_input_tokens === "number") {
      tokenUsage.cacheWrite = usage.cache_creation_input_tokens;
    }
    tokenUsage.total = tokenUsage.input + tokenUsage.output + (tokenUsage.cacheRead ?? 0) + (tokenUsage.cacheWrite ?? 0);
    meta.tokenUsage = tokenUsage;
  }
  return { text, meta };
}
function resolveGitBashPath() {
  if (process.platform !== "win32") return void 0;
  const fromParent = process.env.CLAUDE_CODE_GIT_BASH_PATH;
  if (fromParent) return fromParent;
  const candidates = [
    "C:\\Program Files\\Git\\bin\\bash.exe",
    "C:\\Program Files\\Git\\usr\\bin\\bash.exe",
    "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
    "D:\\Program Files\\Git\\bin\\bash.exe",
    "D:\\Program Files\\Git\\usr\\bin\\bash.exe"
  ];
  return candidates.find((p) => existsSync(p));
}
function hasAnthropicAuth() {
  return !!(process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN);
}
function findOnPath(name, exts) {
  const dirs = (process.env.PATH ?? "").split(delimiter).filter(Boolean);
  for (const dir of dirs) {
    for (const ext of exts) {
      const full = join(dir, name + ext);
      if (existsSync(full)) return full;
    }
  }
  return void 0;
}
function effortFromBudget(budgetTokens) {
  if (!Number.isFinite(budgetTokens) || budgetTokens <= 0) return void 0;
  if (budgetTokens <= 4e3) return "low";
  if (budgetTokens <= 1e4) return "medium";
  if (budgetTokens <= 32e3) return "high";
  return "max";
}
var claudeEngine = {
  name: "claude",
  capabilities: { tokenUsage: true, jsonSchema: true, tools: true },
  buildArgs(req) {
    const args = ["-p", "--output-format", "json"];
    const wantsTools = !!(req.tools && req.tools.length > 0);
    if (hasAnthropicAuth() && !wantsTools) args.push("--bare");
    if (req.model && req.model.length > 0) args.push("--model", req.model);
    if (req.jsonSchema) {
      args.push("--json-schema", JSON.stringify(req.jsonSchema));
    }
    if (wantsTools) {
      args.push("--allowedTools", req.tools.join(","));
    } else {
      args.push("--tools", "");
    }
    if (typeof req.maxTurns === "number") {
      args.push("--max-turns", String(req.maxTurns));
    }
    if (req.thinking) {
      const effort = effortFromBudget(req.thinking.budgetTokens);
      if (effort) args.push("--effort", effort);
    }
    const stdin = req.system && req.system.length > 0 ? `${req.system}

---

${req.prompt}` : req.prompt;
    return {
      command: "claude",
      args,
      stdin,
      // Windows 上 claude 是 claude.cmd shim，Node spawn 不追 .cmd，走 cmd.exe。
      useShell: process.platform === "win32"
    };
  },
  buildEnv(req) {
    const env = { ...req.env ?? {} };
    const gitBash = resolveGitBashPath();
    if (gitBash && env.CLAUDE_CODE_GIT_BASH_PATH === void 0) {
      env.CLAUDE_CODE_GIT_BASH_PATH = gitBash;
    }
    return env;
  },
  parse(stdout, exitCode) {
    const parsed = parseClaudeJson(stdout);
    if (parsed) return parsed;
    return { text: stdout.replace(/\n+$/, ""), meta: void 0 };
  }
};
var openclawEngine = {
  name: "openclaw",
  capabilities: { tokenUsage: true, jsonSchema: false, tools: true },
  buildArgs(req) {
    const message = req.system && req.system.length > 0 ? `${req.system}

---

${req.prompt}` : req.prompt;
    const agentId = process.env.FLOW_OPENCLAW_AGENT ?? "main";
    const sessionId = `flow-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const args = ["agent", "--local", "--session-id", sessionId, "--message", message, "--json", "--agent", agentId];
    if (req.model && req.model.length > 0) args.push("--model", req.model);
    if (process.platform === "win32") {
      const shim = findOnPath("openclaw", [".cmd"]);
      if (shim) {
        const mjs = join(shim, "..", "node_modules", "openclaw", "openclaw.mjs");
        if (existsSync(mjs)) {
          return { command: "node", args: [mjs, ...args], useShell: false };
        }
      }
    }
    return {
      command: "openclaw",
      args,
      useShell: process.platform === "win32"
    };
  },
  buildEnv(req) {
    return req.env;
  },
  parse(stdout, exitCode) {
    let parsed;
    try {
      parsed = JSON.parse(stdout);
    } catch {
      if (/EMBEDDED FALLBACK|Gateway .*failed|GatewayTransportError/i.test(stdout)) {
        throw new Error(
          `openclaw gateway \u4E0D\u53EF\u7528\uFF0C\u5DF2\u964D\u7EA7 embedded\uFF08\u65E0\u6CD5\u591A subagent\uFF09\u3002\u8BF7\u786E\u8BA4 \`openclaw gateway\` \u5728\u8FD0\u884C\u4E14\u5065\u5EB7\u3002stdout \u5934\u90E8\uFF1A
${stdout.slice(0, 400)}`
        );
      }
      return { text: stdout.replace(/\n+$/, ""), meta: void 0 };
    }
    if (!parsed || typeof parsed !== "object") {
      return { text: stdout.replace(/\n+$/, ""), meta: void 0 };
    }
    const top = parsed;
    const body = top.result && typeof top.result === "object" ? top.result : top;
    let text = "";
    const payloads = body.payloads;
    if (Array.isArray(payloads)) {
      text = payloads.map(
        (p) => p && typeof p === "object" && typeof p.text === "string" ? p.text : ""
      ).join("").trim();
    }
    if (!text) {
      text = typeof body.result === "string" && body.result || typeof body.text === "string" && body.text || "";
    }
    const meta = {};
    const metaObj = body.meta;
    const agentMeta = metaObj && typeof metaObj === "object" ? metaObj.agentMeta : void 0;
    if (agentMeta && typeof agentMeta === "object") {
      if (typeof agentMeta.sessionId === "string") {
        meta.sessionId = agentMeta.sessionId;
      }
      if (typeof agentMeta.sessionFile === "string") {
        meta.sessionFile = agentMeta.sessionFile;
      }
      if (typeof agentMeta.provider === "string") {
        meta.provider = agentMeta.provider;
      }
      if (typeof agentMeta.model === "string") meta.model = agentMeta.model;
      if (typeof agentMeta.contextTokens === "number") {
        meta.contextWindow = agentMeta.contextTokens;
      }
      const usage = agentMeta.lastCallUsage ?? agentMeta.usage;
      if (usage && typeof usage === "object") {
        const input = typeof usage.input === "number" ? usage.input : 0;
        const output = typeof usage.output === "number" ? usage.output : 0;
        const tu = { input, output, total: input + output };
        if (typeof usage.cacheRead === "number") tu.cacheRead = usage.cacheRead;
        if (typeof usage.cacheWrite === "number") {
          tu.cacheWrite = usage.cacheWrite;
        }
        meta.tokenUsage = tu;
      }
    }
    return { text, meta };
  }
};
var hermesEngine = {
  name: "hermes",
  capabilities: { tokenUsage: false, jsonSchema: false, tools: true },
  buildArgs(req) {
    const prompt = req.system && req.system.length > 0 ? `${req.system}

---

${req.prompt}` : req.prompt;
    const args = ["-z", prompt];
    if (req.model && req.model.length > 0) args.push("-m", req.model);
    const realExe = process.platform === "win32" ? findOnPath("hermes", [".exe"]) : void 0;
    if (realExe) {
      return { command: realExe, args, useShell: false };
    }
    return {
      command: "hermes",
      args,
      useShell: process.platform === "win32"
    };
  },
  buildEnv(req) {
    return req.env;
  },
  parse(stdout, exitCode) {
    return { text: stdout.replace(/\n+$/, ""), meta: void 0 };
  }
};
function splitEnvArgs(raw) {
  return raw ? raw.split(/\s+/).filter(Boolean) : [];
}
function resolveCommand(command) {
  if (process.platform !== "win32") return { command, useShell: false };
  const hasPathSep = command.includes("\\") || command.includes("/");
  const hasExt = /\.[a-z0-9]+$/i.test(command);
  if (!hasPathSep && !hasExt) {
    const exe = findOnPath(command, [".exe"]);
    if (exe) return { command: exe, useShell: false };
  }
  return { command, useShell: true };
}
function psiAiKind() {
  return (process.env.FLOW_PSI_AI ?? "openai-completions").toLowerCase();
}
var psiEngine = {
  name: "psi",
  capabilities: { tokenUsage: false, jsonSchema: false, tools: true },
  buildArgs(req) {
    const workspace = process.env.FLOW_PSI_WORKSPACE;
    if (!workspace) {
      throw new Error(
        `FLOW_ENGINE=psi requires FLOW_PSI_WORKSPACE to point at a psi-agent workspace.`
      );
    }
    const message = req.system && req.system.length > 0 ? `${req.system}

---

${req.prompt}` : req.prompt;
    const baseCommand = process.env.FLOW_PSI_COMMAND ?? "psi-agent";
    const resolved = resolveCommand(baseCommand);
    const args = [
      ...splitEnvArgs(process.env.FLOW_PSI_COMMAND_ARGS),
      "run",
      "--workspace",
      workspace,
      "--message",
      message,
      "--output-format",
      "text"
    ];
    const aiSocket = process.env.FLOW_PSI_AI_SOCKET;
    if (aiSocket) args.push("--ai-socket", aiSocket);
    const ai = process.env.FLOW_PSI_AI;
    if (ai) args.push("--ai", ai);
    const model = req.model || process.env.FLOW_PSI_MODEL;
    if (model) args.push("--model", model);
    const profile = process.env.FLOW_PSI_PROFILE;
    if (profile) args.push("--profile", profile);
    const config = process.env.FLOW_PSI_CONFIG;
    if (config) args.push("--config", config);
    if (/^(1|true|yes)$/i.test(process.env.FLOW_PSI_SHOW_REASONING ?? "")) {
      args.push("--show-reasoning");
    }
    return { command: resolved.command, args, useShell: resolved.useShell };
  },
  buildEnv(req) {
    const env = { ...req.env ?? {} };
    const apiKey = process.env.FLOW_PSI_API_KEY;
    const baseUrl = process.env.FLOW_PSI_BASE_URL;
    const ai = psiAiKind();
    if (apiKey) {
      if (ai === "anthropic-messages") env.ANTHROPIC_API_KEY = apiKey;
      else env.OPENAI_API_KEY = apiKey;
    }
    if (baseUrl) {
      if (ai === "anthropic-messages") env.ANTHROPIC_BASE_URL = baseUrl;
      else env.OPENAI_BASE_URL = baseUrl;
    }
    return env;
  },
  parse(stdout, exitCode) {
    return { text: stdout.replace(/\n+$/, ""), meta: void 0 };
  }
};
var ENGINES = {
  claude: claudeEngine,
  openclaw: openclawEngine,
  hermes: hermesEngine,
  psi: psiEngine
};
function pickEngine(name) {
  const raw = (name ?? process.env.FLOW_ENGINE ?? "claude").toLowerCase();
  const key = raw === "psi-agent" ? "psi" : raw;
  const engine = ENGINES[key];
  if (!engine) {
    throw new Error(
      `\u672A\u77E5 CLI \u5F15\u64CE "${raw}"\u3002\u652F\u6301\uFF1Aclaude / openclaw / hermes / psi\u3002\u7528 FLOW_ENGINE \u6216 flow.agent({engine}) \u6307\u5B9A\u3002`
    );
  }
  return engine;
}

// src/subprocess.ts
import { spawn as childSpawn } from "node:child_process";
function escapeForCmd(arg) {
  let s = String(arg);
  s = s.replace(/(\\*)"/g, '$1$1\\"');
  s = s.replace(/(\\*)$/, "$1$1");
  s = `"${s}"`;
  s = s.replace(/[()%!^"<>&|]/g, "^$&");
  return s;
}
async function runSubprocess(opts) {
  const baselineEnv = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (typeof v === "string") baselineEnv[k] = v;
  }
  const finalEnv = { ...baselineEnv, ...opts.env ?? {} };
  if (opts.signal?.aborted) {
    throw new Error(
      `runSubprocess: signal already aborted before start, subprocess not spawned.`
    );
  }
  return await new Promise((resolve, reject) => {
    let child;
    try {
      if (opts.useShell && process.platform === "win32") {
        const line = [opts.command, ...opts.args].map(escapeForCmd).join(" ");
        child = childSpawn("cmd.exe", ["/d", "/s", "/c", line], {
          cwd: opts.cwd,
          env: finalEnv,
          windowsVerbatimArguments: true,
          stdio: ["pipe", "pipe", "pipe"]
        });
      } else {
        child = childSpawn(opts.command, opts.args, {
          cwd: opts.cwd,
          env: finalEnv,
          shell: opts.useShell,
          stdio: ["pipe", "pipe", "pipe"]
        });
      }
    } catch (err) {
      reject(err);
      return;
    }
    const stdoutChunks = [];
    const stderrChunks = [];
    let timedOut = false;
    let aborted = false;
    let settled = false;
    const maxStdout = opts.maxStdoutBytes && Number.isFinite(opts.maxStdoutBytes) && opts.maxStdoutBytes > 0 ? opts.maxStdoutBytes : Infinity;
    let stdoutBytes = 0;
    let stdoutTruncated = false;
    const timer = setTimeout(() => {
      timedOut = true;
      try {
        child.kill("SIGKILL");
      } catch {
      }
    }, opts.timeoutMs);
    const onAbort = () => {
      aborted = true;
      try {
        child.kill("SIGKILL");
      } catch {
      }
    };
    if (opts.signal) opts.signal.addEventListener("abort", onAbort);
    const cleanup = () => {
      clearTimeout(timer);
      if (opts.signal) opts.signal.removeEventListener("abort", onAbort);
    };
    const finalize = (resolveValue) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(resolveValue);
    };
    const fail = (err) => {
      if (settled) return;
      settled = true;
      cleanup();
      try {
        child.kill("SIGKILL");
      } catch {
      }
      reject(err);
    };
    child.stdout.on("data", (chunk) => {
      if (stdoutTruncated) return;
      const remaining = maxStdout - stdoutBytes;
      if (chunk.length <= remaining) {
        stdoutChunks.push(chunk);
        stdoutBytes += chunk.length;
        return;
      }
      if (remaining > 0) {
        stdoutChunks.push(chunk.subarray(0, remaining));
        stdoutBytes += remaining;
      }
      stdoutTruncated = true;
      try {
        child.kill("SIGKILL");
      } catch {
      }
    });
    child.stderr.on("data", (chunk) => stderrChunks.push(chunk));
    child.on("error", (err) => fail(err));
    child.on("close", (code) => {
      const stdout = Buffer.concat(stdoutChunks).toString("utf8");
      const stderr = Buffer.concat(stderrChunks).toString("utf8");
      if (stdoutTruncated) {
        finalize({ stdout, stderr, exitCode: code ?? -1, truncated: true });
        return;
      }
      if (aborted) {
        fail(new Error(`runSubprocess: received abort signal, subprocess SIGKILLed.`));
        return;
      }
      if (timedOut) {
        fail(
          new Error(
            `runSubprocess: subprocess timed out after ${opts.timeoutMs}ms, SIGKILLed. command="${opts.command} ${opts.args.join(" ")}"
stderr tail: ${stderr.slice(-300)}`
          )
        );
        return;
      }
      finalize({ stdout, stderr, exitCode: code ?? -1 });
    });
    if (typeof opts.stdin === "string") {
      child.stdin.write(opts.stdin);
      child.stdin.end();
    } else {
      child.stdin.end();
    }
  });
}

// src/cli.ts
import { rmSync, readdirSync } from "node:fs";
import { join as join2 } from "node:path";
function resolveTimeoutMs() {
  const raw = process.env.FLOW_CLI_TIMEOUT_MS;
  if (raw) {
    const n = Number(raw);
    if (Number.isFinite(n) && n > 0) return n;
  }
  return 3e5;
}
function resolveMaxConcurrency() {
  const raw = process.env.FLOW_MAX_CONCURRENCY;
  if (raw) {
    const n = Number(raw);
    if (Number.isInteger(n) && n > 0) return n;
  }
  return 4;
}
var active = 0;
var waiters = [];
async function acquire(signal) {
  if (signal?.aborted) {
    throw new Error("acquire aborted before acquiring concurrency slot");
  }
  const max = resolveMaxConcurrency();
  if (active < max) {
    active++;
    return;
  }
  await new Promise((resolve, reject) => {
    const node = { resolve, reject };
    waiters.push(node);
    if (signal) {
      signal.addEventListener(
        "abort",
        () => {
          const i = waiters.indexOf(node);
          if (i >= 0) waiters.splice(i, 1);
          reject(new Error("acquire aborted while waiting for concurrency slot"));
        },
        { once: true }
      );
    }
  });
  active++;
}
function release() {
  active--;
  const next = waiters.shift();
  if (next) next.resolve();
}
var warnedNoSchema = /* @__PURE__ */ new Set();
var callViaCli = async (opts) => {
  const engine = pickEngine(opts.engine);
  if (opts.jsonSchema && !engine.capabilities.jsonSchema) {
    if (!warnedNoSchema.has(engine.name)) {
      warnedNoSchema.add(engine.name);
      console.warn(
        `  [warn] CLI engine "${engine.name}" does not support --json-schema strict structured output; flow.evaluate / choice will fall back to text parsing (may be flaky). Prefer the claude engine for evaluators.`
      );
    }
  }
  const req = {
    system: opts.system,
    prompt: opts.userPrompt,
    model: opts.model,
    jsonSchema: engine.capabilities.jsonSchema ? opts.jsonSchema : void 0,
    tools: opts.tools,
    maxTurns: opts.maxTurns,
    // #17: thinking 透到 CliRequest，claude 引擎据此映射 --effort（真生效，不再 no-op）。
    thinking: opts.thinking
  };
  const built = engine.buildArgs(req);
  const env = engine.buildEnv ? engine.buildEnv(req) : void 0;
  await acquire(opts.signal);
  let out;
  try {
    out = await runSubprocess({
      command: built.command,
      args: built.args,
      stdin: built.stdin,
      useShell: built.useShell,
      env,
      timeoutMs: resolveTimeoutMs(),
      signal: opts.signal
    });
  } finally {
    release();
  }
  if (out.exitCode !== 0) {
    const cleanStderr = out.stderr.replace(/\[[0-9;]*m/g, "");
    throw new Error(
      `CLI engine "${engine.name}" subprocess exited abnormally exit=${out.exitCode}.
command: ${built.command}
args: ${JSON.stringify(built.args).slice(0, 600)}
model=${opts.model || "(default)"} tools=${(opts.tools ?? []).join(",") || "(none)"} maxTurns=${opts.maxTurns ?? "(default)"}
stderr tail: ${cleanStderr.slice(-300)}`
    );
  }
  const parsed = engine.parse(out.stdout, out.exitCode);
  const sf = parsed.meta?.sessionFile;
  const m = sf ? /^(.*[\\/])(flow-[a-z0-9-]+)\.jsonl$/i.exec(sf) : null;
  if (m) {
    const dir = m[1];
    const baseName = m[2];
    try {
      for (const entry of readdirSync(dir)) {
        if (entry === baseName || entry.startsWith(`${baseName}.`)) {
          try {
            rmSync(join2(dir, entry), { force: true });
          } catch {
          }
        }
      }
    } catch {
    }
  }
  const tu = parsed.meta?.tokenUsage;
  return {
    text: parsed.text,
    inputTokens: tu?.input ?? 0,
    outputTokens: tu?.output ?? 0,
    cacheReadTokens: tu?.cacheRead ?? 0,
    cacheWriteTokens: tu?.cacheWrite ?? 0,
    costUSD: parsed.meta?.costUSD,
    engine: engine.name
  };
};

// src/flow.ts
import { AsyncLocalStorage } from "node:async_hooks";
import { createHash } from "node:crypto";
import { appendFile, writeFile } from "node:fs/promises";
import path from "node:path";
var nodeStorage = new AsyncLocalStorage();
var cancelStorage = new AsyncLocalStorage();
function currentCancelSignal() {
  return cancelStorage.getStore();
}
function resolveParallelGraceMs() {
  const raw = process.env.FLOW_PARALLEL_GRACE_MS;
  if (raw) {
    const n = Number(raw);
    if (Number.isFinite(n) && n >= 0) return n;
  }
  return 5e3;
}
async function settleLaggards(promises) {
  const all = Promise.allSettled(promises);
  const graceMs = resolveParallelGraceMs();
  let timer;
  const grace = new Promise((resolve) => {
    timer = setTimeout(resolve, graceMs);
  });
  await Promise.race([all.then(() => void 0), grace]);
  if (timer) clearTimeout(timer);
}
function currentParent(rootFallback) {
  return nodeStorage.getStore() ?? rootFallback;
}
function runUnderNode(node, fn) {
  return nodeStorage.run(node, fn);
}
function nowIso() {
  return (/* @__PURE__ */ new Date()).toISOString();
}
function nodeLabel(node) {
  const n = node;
  return n.agent ?? n.name ?? n.label ?? n.service ?? n.branch ?? void 0;
}
function appendProgress(ctx, event, node, extra) {
  if (ctx.sealed) return;
  const label = nodeLabel(node);
  const record = {
    ts: nowIso(),
    event,
    id: node.id,
    type: node.type
  };
  if (label !== void 0) record.label = label;
  if (extra?.status !== void 0) record.status = extra.status;
  if (extra?.durationMs !== void 0) record.durationMs = extra.durationMs;
  const line = JSON.stringify(record) + "\n";
  const progressPath = path.join(ctx.runDir, "progress.jsonl");
  ctx.writeQueue = ctx.writeQueue.then(() => appendFile(progressPath, line, "utf8")).catch(() => {
  });
}
function pickProvider() {
  const engine = (process.env.FLOW_ENGINE ?? "claude").toLowerCase();
  return {
    provider: `cli:${engine}`,
    call: callViaCli,
    // 空串 = 用 CLI 自身默认模型；FLOW_MODEL 可指定（claude 接受 "opus"/"sonnet" 或全名）。
    defaultModel: process.env.FLOW_MODEL ?? ""
  };
}
function buildUserMessage(prompt, context) {
  if (!context || Object.keys(context).length === 0) return prompt;
  const ctxBlock = Object.entries(context).map(([key, value]) => `## context.${key}

${value}`).join("\n\n---\n\n");
  return `${ctxBlock}

---

${prompt}`;
}
function nextNodeId(ctx, prefix) {
  ctx.nodeIdSeq += 1;
  return `${prefix}-${ctx.nodeIdSeq.toString().padStart(4, "0")}`;
}
function pickBindingName(base, count, override) {
  const name = override ?? (count === 1 ? base : `${base}.${count}`);
  return assertSafeName("flow binding", name);
}
function makeInputHash(parts) {
  const h = createHash("sha256");
  for (const p of parts) {
    h.update(p);
    h.update(" ");
  }
  return h.digest("hex").slice(0, 16);
}
function tryResumeHit(ctx, bindingName, inputHash) {
  if (!ctx.isResumed) return void 0;
  const entry = ctx.resumeCache.get(bindingName);
  if (!entry) return void 0;
  if (entry.inputHash && entry.inputHash !== inputHash) return void 0;
  return { content: entry.content };
}
var UNSAFE_NAME_RE = new RegExp("[/\\\\]|\\p{C}|\\p{Z}", "u");
var WIN_RESERVED_RE = /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)/i;
function assertSafeName(kind, name) {
  if (typeof name !== "string" || name.length === 0) {
    throw new Error(`${kind}: name must not be empty`);
  }
  if (name === "." || name === ".." || name.includes("..")) {
    throw new Error(
      `${kind}: name "${name}" must not be "." / ".." or contain ".." (path traversal).`
    );
  }
  if (UNSAFE_NAME_RE.test(name)) {
    throw new Error(
      `${kind}: name "${name}" contains an illegal character. Path separators (/ \\), whitespace and control chars are not allowed (letters/digits/_/-/. and non-ASCII like \u4E2D\u6587 are fine).`
    );
  }
  if (WIN_RESERVED_RE.test(name)) {
    throw new Error(
      `${kind}: name "${name}" is a Windows reserved device name (CON/PRN/AUX/NUL/COMn/LPTn). Pick another name for cross-platform safety.`
    );
  }
  if (/[. ]$/.test(name)) {
    throw new Error(
      `${kind}: name "${name}" must not end with '.' or space (Windows fs strips them).`
    );
  }
  return name.normalize("NFC");
}
function assertContextSchema(agentName, schema, context) {
  if (!schema || schema.length === 0) return;
  const got = new Set(Object.keys(context ?? {}));
  const missing = schema.filter((k) => !got.has(k));
  const allowed = new Set(schema);
  const extra = [...got].filter((k) => !allowed.has(k));
  if (missing.length === 0 && extra.length === 0) return;
  const parts = [];
  if (missing.length > 0) parts.push(`\u7F3A\u5C11\u5B57\u6BB5 [${missing.join(", ")}]`);
  if (extra.length > 0) {
    parts.push(`\u591A\u51FA\u672A\u58F0\u660E\u5B57\u6BB5 [${extra.join(", ")}]\uFF08\u62FC\u9519\uFF1F\uFF09`);
  }
  throw new Error(
    `flow.session("${agentName}"): context \u4E0D\u5339\u914D contextSchema\u3002${parts.join("\uFF1B")}\u3002\u58F0\u660E\u7684\u5B57\u6BB5\u662F [${schema.join(", ")}]\u3002`
  );
}
function registerBindingName(ctx, kind, name, hadExplicitOverride) {
  if (ctx.writtenBindings.has(name)) {
    if (hadExplicitOverride) {
      throw new Error(
        `${kind}: bindingName "${name}" \u5DF2\u88AB\u5360\u7528\u3002\u4E24\u6B21\u663E\u5F0F\u7528\u540C\u540D\u4F1A\u8BA9\u7B2C\u4E00\u6B21\u7684\u5185\u5BB9\u88AB\u9759\u9ED8\u8986\u76D6\u3001\u5F7B\u5E95\u4E22\u5931\u3002\u8BF7\u6362\u4E00\u4E2A bindingName\uFF0C\u6216\u53BB\u6389\u663E\u5F0F\u547D\u540D\u8BA9\u6846\u67B6\u81EA\u52A8\u7F16\u53F7\u3002`
      );
    }
    return;
  }
  ctx.writtenBindings.add(name);
}
async function writeBinding(runDir, name, value, meta) {
  const bindingPath = path.join(runDir, "bindings", `${name}.md`);
  const metaPath = path.join(runDir, "bindings", `${name}.meta.json`);
  await writeFile(bindingPath, value, "utf8");
  await writeFile(metaPath, JSON.stringify(meta, null, 2), "utf8");
}
function assertNotSealed(ctx, kind) {
  if (ctx.sealed) {
    throw new Error(
      `${kind}: run() \u5DF2\u7ED3\u675F\uFF0C\u4E0D\u80FD\u518D\u5199\u76D8\u3002\u8FD9\u901A\u5E38\u662F detached promise\uFF08\u88F8 .then / setTimeout \u91CC\u8C03 flow.*\uFF09\u5BFC\u81F4\u2014\u2014\u5B83\u8131\u79BB\u4E86\u6267\u884C\u56FE\uFF0C\u4EA7\u7269\u56DE\u653E\u4E0D\u5230\u3002\u8BF7\u5728 run() \u7684 fn \u5185 await \u6BCF\u4E2A flow.* \u8C03\u7528\u3002`
    );
  }
}
function previewItem(x) {
  const s = typeof x === "string" ? x : (() => {
    try {
      return JSON.stringify(x);
    } catch {
      return String(x);
    }
  })();
  return s.length > 60 ? `${s.slice(0, 57)}...` : s;
}
async function withGraphNode(ctx, node, fn) {
  const parent = currentParent(ctx.rootGraphNode);
  parent.children.push(node);
  const startTs = Date.now();
  appendProgress(ctx, "node_start", node);
  try {
    const result = await runUnderNode(node, fn);
    node.status = "ok";
    node.endedAt = nowIso();
    node.durationMs = Date.now() - startTs;
    appendProgress(ctx, "node_end", node, {
      status: "ok",
      durationMs: node.durationMs
    });
    return result;
  } catch (err) {
    node.status = "error";
    node.endedAt = nowIso();
    node.durationMs = Date.now() - startTs;
    node.errorMessage = err.message;
    appendProgress(ctx, "node_end", node, {
      status: "error",
      durationMs: node.durationMs
    });
    throw err;
  }
}
var EVALUATOR_SYSTEM_PROMPT = `\u4F60\u662F\u4E00\u4E2A\u4E25\u8C28\u7684\u7ED3\u6784\u5316\u5224\u65AD\u5668\u3002

\u4F60\u53EA\u8F93\u51FA JSON\uFF0C\u4E0D\u8981\u4EFB\u4F55\u89E3\u91CA\u3001\u524D\u540E\u7F00\u3001Markdown \u4EE3\u7801\u5757\u3002

\u6839\u636E\u7528\u6237\u7ED9\u7684 \`kind\` \u5B57\u6BB5\uFF0C\u8F93\u51FA\u5BF9\u5E94\u683C\u5F0F\uFF1A

- kind = "boolean"\uFF1A\u8F93\u51FA {"value": true} \u6216 {"value": false}
- kind = "number"\uFF1A\u8F93\u51FA {"value": <number>}\uFF0C\u5FC5\u987B\u662F\u6570\u5B57\u5B57\u9762\u91CF
- kind = "choice"\uFF1A\u8F93\u51FA {"value": "<\u5019\u9009\u9879\u539F\u6587>"}\uFF0Cvalue \u5FC5\u987B\u4E25\u683C\u7B49\u4E8E options \u4E2D\u7684\u67D0\u4E00\u9879

\u5982\u679C\u4FE1\u606F\u4E0D\u8DB3\u4EE5\u5224\u65AD\uFF0C\u6309\u4F60\u7684\u6700\u4F73\u63A8\u6D4B\u7ED9\u51FA value\uFF0C\u4F46\u4FDD\u6301 JSON \u683C\u5F0F\u3002
\u7EDD\u5BF9\u4E0D\u8981\u8F93\u51FA\u989D\u5916\u5B57\u6BB5\u3002`;
var EVALUATOR_AGENT_NAME = "__evaluator__";
function buildEvaluatePrompt(options) {
  const lines = [];
  lines.push(`# \u4EFB\u52A1`);
  lines.push(options.question);
  lines.push("");
  if (options.context && Object.keys(options.context).length > 0) {
    lines.push(`# \u4E0A\u4E0B\u6587`);
    for (const [k, v] of Object.entries(options.context)) {
      lines.push(`## context.${k}`);
      lines.push(v);
      lines.push("");
    }
  }
  lines.push(`# \u8F93\u51FA\u683C\u5F0F`);
  if (options.kind === "boolean") {
    lines.push(`kind = "boolean"\uFF0C\u8F93\u51FA {"value": true} \u6216 {"value": false}\u3002`);
  } else if (options.kind === "number") {
    const range = [];
    if (typeof options.min === "number") range.push(`min=${options.min}`);
    if (typeof options.max === "number") range.push(`max=${options.max}`);
    if (options.integer) range.push(`\u5FC5\u987B\u4E3A\u6574\u6570`);
    const tail = range.length ? `\uFF08${range.join("\uFF0C")}\uFF09` : "";
    lines.push(`kind = "number"\uFF0C\u8F93\u51FA {"value": <number>}${tail}\u3002`);
  } else {
    lines.push(`kind = "choice"\uFF0C\u5FC5\u987B\u4ECE\u4E0B\u5217\u5019\u9009\u9879\u4E2D\u9009\u4E00\u4E2A\uFF1A`);
    for (const opt of options.options) lines.push(`- ${opt}`);
    lines.push(`\u8F93\u51FA {"value": "<\u5019\u9009\u9879\u539F\u6587>"}\u3002`);
  }
  return lines.join("\n");
}
function buildEvaluateSchema(options) {
  let valueSchema;
  if (options.kind === "boolean") {
    valueSchema = { type: "boolean" };
  } else if (options.kind === "number") {
    valueSchema = { type: options.integer ? "integer" : "number" };
  } else {
    valueSchema = { type: "string", enum: [...options.options] };
  }
  return {
    type: "object",
    properties: { value: valueSchema },
    required: ["value"],
    additionalProperties: false
  };
}
function parseEvaluateAnswer(raw, options) {
  const stripped = raw.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
  if (stripped.length === 0) {
    throw new Error(
      `flow.evaluate: LLM \u6CA1\u6709\u8FD4\u56DE\u4EFB\u4F55\u6587\u672C\uFF08content \u548C reasoning_content \u90FD\u4E3A\u7A7A\uFF09\u3002\u5982\u679C\u4F60\u7528\u7684\u662F thinking \u6A21\u578B\uFF08DeepSeek-R1 / \u8C46\u5305 thinking \u7B49\uFF09\uFF0C\u8BF7\u68C0\u67E5 max_tokens \u662F\u5426\u591F\uFF08thinking \u4F1A\u5148\u5403 token\uFF09\uFF0C\u6216\u6362\u6210\u975E thinking \u7248\u672C\uFF08deepseek-chat / doubao-seed-1.6-250615\uFF09\u3002`
    );
  }
  let parsed;
  try {
    parsed = JSON.parse(stripped);
  } catch {
    throw new Error(
      `flow.evaluate: \u65E0\u6CD5\u628A LLM \u8F93\u51FA\u89E3\u6790\u4E3A JSON\u3002\u539F\u6587\uFF1A${raw.slice(0, 200)}`
    );
  }
  if (typeof parsed !== "object" || parsed === null || !("value" in parsed)) {
    throw new Error(
      `flow.evaluate: LLM \u8F93\u51FA\u7F3A\u5C11 value \u5B57\u6BB5\u3002\u539F\u6587\uFF1A${raw.slice(0, 200)}`
    );
  }
  const value = parsed.value;
  if (options.kind === "boolean") {
    if (typeof value === "boolean") return value;
    if (value === "true") return true;
    if (value === "false") return false;
    throw new Error(`flow.evaluate(boolean): value \u4E0D\u662F\u5E03\u5C14\uFF1A"${String(value)}"`);
  }
  if (options.kind === "number") {
    const num = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(num)) {
      throw new Error(`flow.evaluate(number): value \u4E0D\u662F\u6570\u5B57\uFF1A"${String(value)}"`);
    }
    let n = num;
    if (options.integer) n = Math.round(n);
    if (typeof options.min === "number" && n < options.min) n = options.min;
    if (typeof options.max === "number" && n > options.max) n = options.max;
    return n;
  }
  const text = String(value).trim();
  if (options.options.includes(text)) return text;
  const lowered = text.toLowerCase();
  const hit = options.options.find((o) => o.toLowerCase() === lowered);
  if (hit) return hit;
  throw new Error(
    `flow.evaluate(choice): value "${text}" \u4E0D\u5728\u5019\u9009\u9879\u4E2D\uFF1A${JSON.stringify(options.options)}`
  );
}
function createFlowAPI(ctx) {
  const evaluatorAgent = {
    __kind: "agent",
    name: EVALUATOR_AGENT_NAME,
    config: {
      name: EVALUATOR_AGENT_NAME,
      system: EVALUATOR_SYSTEM_PROMPT,
      maxTokens: 256,
      temperature: 0
    }
  };
  async function runEvaluator(options, overrideEvaluator) {
    const { provider, call, defaultModel } = pickProvider();
    const agent = overrideEvaluator ?? evaluatorAgent;
    const cfg = agent.config;
    const model = cfg.model ?? defaultModel;
    const maxTokens = cfg.maxTokens ?? 256;
    const temperature = cfg.temperature ?? 0;
    const userPrompt = buildEvaluatePrompt(options);
    const system = cfg.system ?? EVALUATOR_SYSTEM_PROMPT;
    const result = await call({
      model,
      system,
      userPrompt,
      maxTokens,
      temperature,
      engine: cfg.engine,
      // jsonSchema：支持的引擎（claude）走原生强校验拿 structured_output；
      // 不支持的引擎 cli.ts 会忽略并 warn，退回 parseEvaluateAnswer 文本解析。
      jsonSchema: buildEvaluateSchema(options)
    });
    return {
      raw: result.text,
      inputTokens: result.inputTokens,
      outputTokens: result.outputTokens,
      cacheReadTokens: result.cacheReadTokens,
      cacheWriteTokens: result.cacheWriteTokens,
      provider,
      model,
      system,
      userPrompt
    };
  }
  const flow = {
    agent(config) {
      const name = assertSafeName("flow.agent", config.name);
      const systemPrompt = config.system ?? config.prompt;
      if (!systemPrompt) {
        throw new Error(
          `flow.agent({name: "${config.name}"}) \u7F3A\u5C11 system / prompt \u5B57\u6BB5`
        );
      }
      return {
        __kind: "agent",
        name,
        config: { ...config, name, system: systemPrompt }
      };
    },
    async session(agent, prompt, context, options) {
      assertNotSealed(ctx, "flow.session");
      const { provider, call, defaultModel } = pickProvider();
      const cfg = agent.config;
      assertContextSchema(agent.name, cfg.contextSchema, context);
      const model = cfg.model ?? defaultModel;
      const maxTokens = cfg.maxTokens ?? 8192;
      const temperature = cfg.temperature ?? 1;
      const userPrompt = buildUserMessage(prompt, context);
      const callCount = (ctx.sessionCallCount.get(agent.name) ?? 0) + 1;
      const bindingName = pickBindingName(
        agent.name,
        callCount,
        options?.bindingName
      );
      const commitCount = () => {
        registerBindingName(
          ctx,
          "flow.session",
          bindingName,
          options?.bindingName !== void 0
        );
        ctx.sessionCallCount.set(agent.name, callCount);
      };
      const traceFile = `trace/${bindingName}.json`;
      const node = {
        id: nextNodeId(ctx, "session"),
        type: "session",
        agent: agent.name,
        bindingName,
        traceFile,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, async () => {
        const inputHash = makeInputHash([
          provider,
          model,
          cfg.system ?? "",
          userPrompt,
          String(temperature),
          String(maxTokens),
          cfg.thinking ? `think:${cfg.thinking.budgetTokens}` : "think:off",
          // #18: tools / maxTurns 变更会改变 agent 行为（纯文本 ↔ agentic），
          //      必须进 hash，否则加了 tools 后 --resume 会假命中、拿回旧的纯文本结果。
          //      engine 已隐含在 provider（cli:<engine>）里，无需重复。
          `tools:${(cfg.tools ?? []).slice().sort().join(",")}`,
          `maxTurns:${cfg.maxTurns ?? ""}`
        ]);
        const hit = tryResumeHit(ctx, bindingName, inputHash);
        if (hit) {
          node.cached = true;
          commitCount();
          console.log(
            `  [resume] ${agent.name} -> bindings/${bindingName}.md (cached)`
          );
          return hit.content;
        }
        const engineLabel = cfg.engine ?? process.env.FLOW_ENGINE ?? "claude";
        console.log(
          `  [session] ${agent.name} -> spawning ${engineLabel} CLI...`
        );
        const result = await call({
          model,
          system: cfg.system,
          userPrompt,
          maxTokens,
          temperature,
          thinking: cfg.thinking,
          signal: currentCancelSignal(),
          engine: cfg.engine,
          tools: cfg.tools,
          maxTurns: cfg.maxTurns
        });
        const endedAt = nowIso();
        node.tokens = {
          input: result.inputTokens,
          output: result.outputTokens
        };
        if (result.engine) node.engine = result.engine;
        if (typeof result.costUSD === "number") node.costUSD = result.costUSD;
        const trace = {
          agent: agent.name,
          provider,
          model,
          startedAt: node.startedAt,
          endedAt,
          durationMs: 0,
          // 真值在外层 withGraphNode 回填到 node.durationMs；trace 这里随便给
          system: cfg.system,
          userPrompt,
          context,
          output: result.text,
          inputTokens: result.inputTokens,
          outputTokens: result.outputTokens,
          cacheReadTokens: result.cacheReadTokens,
          cacheWriteTokens: result.cacheWriteTokens
        };
        const tracePath = path.join(ctx.runDir, traceFile);
        const bindingMeta = {
          name: bindingName,
          producedBy: agent.name,
          producedAt: endedAt,
          tokens: { input: result.inputTokens, output: result.outputTokens },
          sourceNode: node.id,
          inputHash
        };
        ctx.writeQueue = ctx.writeQueue.then(async () => {
          trace.durationMs = node.durationMs ?? 0;
          await writeFile(tracePath, JSON.stringify(trace, null, 2), "utf8");
          await writeBinding(
            ctx.runDir,
            bindingName,
            result.text,
            bindingMeta
          );
          ctx.resumeCache.set(bindingName, {
            content: result.text,
            inputHash
          });
        });
        await ctx.writeQueue;
        commitCount();
        console.log(
          `  [session] ${agent.name} -> bindings/${bindingName}.md (in=${result.inputTokens} out=${result.outputTokens})`
        );
        return result.text;
      });
    },
    service(def) {
      const name = assertSafeName("flow.service", def.name);
      if (ctx.services.has(name)) {
        throw new Error(`flow.service: \u91CD\u590D\u6CE8\u518C\u670D\u52A1 "${name}"`);
      }
      ctx.services.set(name, { ...def, name });
      return { __kind: "service", name, signature: def.signature };
    },
    async call(service, args = {}, options) {
      assertNotSealed(ctx, "flow.call");
      const def = ctx.services.get(service.name);
      if (!def) {
        throw new Error(`flow.call: \u670D\u52A1 "${service.name}" \u672A\u6CE8\u518C`);
      }
      const params = def.signature?.params ?? [];
      const knownParams = new Set(params.map((p) => p.name));
      for (const required of params.filter((p) => p.required !== false)) {
        if (!(required.name in args)) {
          throw new Error(
            `flow.call("${service.name}"): \u7F3A\u5C11\u5FC5\u586B\u53C2\u6570 "${required.name}"`
          );
        }
      }
      for (const passed of Object.keys(args)) {
        if (params.length > 0 && !knownParams.has(passed)) {
          throw new Error(
            `flow.call("${service.name}"): \u672A\u58F0\u660E\u7684\u53C2\u6570 "${passed}"`
          );
        }
      }
      const callCount = (ctx.serviceCallCount.get(service.name) ?? 0) + 1;
      const bindingName = pickBindingName(
        service.name,
        callCount,
        options?.bindingName
      );
      const commitCount = () => {
        registerBindingName(
          ctx,
          "flow.call",
          bindingName,
          options?.bindingName !== void 0
        );
        ctx.serviceCallCount.set(service.name, callCount);
      };
      const node = {
        id: nextNodeId(ctx, "call"),
        type: "call",
        service: service.name,
        args,
        bindingName,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, async () => {
        const inputHash = makeInputHash([service.name, JSON.stringify(args)]);
        const hit = tryResumeHit(ctx, bindingName, inputHash);
        if (hit) {
          node.cached = true;
          commitCount();
          console.log(
            `  [resume] call ${service.name} -> bindings/${bindingName}.md (cached)`
          );
          return hit.content;
        }
        const result = await def.body(args);
        const meta = {
          name: bindingName,
          producedBy: service.name,
          producedAt: nowIso(),
          sourceNode: node.id,
          inputHash
        };
        ctx.writeQueue = ctx.writeQueue.then(
          () => writeBinding(ctx.runDir, bindingName, result, meta)
        );
        await ctx.writeQueue;
        commitCount();
        ctx.resumeCache.set(bindingName, { content: result, inputHash });
        console.log(`  [call] ${service.name} -> bindings/${bindingName}.md`);
        return result;
      });
    },
    // ============================================================
    // 第二批：控制流
    // ============================================================
    async parallel(tasks, options) {
      const join3 = options?.join ?? "all";
      const node = {
        id: nextNodeId(ctx, "parallel"),
        type: "parallel",
        joinStrategy: join3,
        taskCount: tasks.length,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, async () => {
        console.log(
          `  [parallel] launching ${tasks.length} branches (join=${typeof join3 === "string" ? join3 : JSON.stringify(join3)})...`
        );
        if (join3 === "all") {
          return Promise.all(tasks.map((task) => task()));
        }
        const controller = new AbortController();
        const promises = tasks.map(
          (task) => cancelStorage.run(controller.signal, () => task())
        );
        if (join3 === "first") {
          try {
            const first = await Promise.race(promises);
            return [first];
          } finally {
            controller.abort();
            await settleLaggards(promises);
          }
        }
        const n = join3.any;
        if (typeof n !== "number" || n <= 0) {
          throw new Error(`flow.parallel: \u975E\u6CD5 join \u914D\u7F6E ${JSON.stringify(join3)}`);
        }
        const results = [];
        try {
          await new Promise((resolve, reject) => {
            let remaining = n;
            let settled = false;
            for (const p of promises) {
              p.then(
                (v) => {
                  if (settled) return;
                  results.push(v);
                  remaining -= 1;
                  if (remaining <= 0) {
                    settled = true;
                    resolve();
                  }
                },
                (err) => {
                  if (settled) return;
                  settled = true;
                  reject(err);
                }
              );
            }
          });
          return results;
        } finally {
          controller.abort();
          await settleLaggards(promises);
        }
      });
    },
    async if(cond, thenFn, elseFn) {
      if (typeof cond !== "boolean") {
        throw new Error(
          `flow.if: cond must be a boolean (got ${typeof cond}). For LLM-judged conditions use flow.evaluate({kind:"boolean"}).`
        );
      }
      const taken = cond ? "then" : elseFn ? "else" : "none";
      const node = {
        id: nextNodeId(ctx, "if"),
        type: "if",
        conditionKind: "boolean",
        conditionValue: cond,
        takenBranch: taken,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, async () => {
        if (taken === "none") return void 0;
        const branch = {
          id: nextNodeId(ctx, "ifBranch"),
          type: "ifBranch",
          branch: taken,
          startedAt: nowIso(),
          status: "running",
          children: []
        };
        return withGraphNode(ctx, branch, async () => {
          return taken === "then" ? thenFn() : elseFn();
        });
      });
    },
    async ifElse(branches, elseFn) {
      branches.forEach((b, i) => {
        if (typeof b.cond !== "boolean") {
          throw new Error(
            `flow.ifElse: branch[${i}].cond must be a boolean (got ${typeof b.cond}). For LLM-judged conditions use flow.evaluate({kind:"boolean"}).`
          );
        }
      });
      const hitIdx = branches.findIndex((b) => b.cond);
      const taken = hitIdx >= 0 ? "then" : elseFn ? "else" : "none";
      const node = {
        id: nextNodeId(ctx, "if"),
        type: "if",
        conditionKind: "boolean",
        conditionValue: hitIdx >= 0,
        takenBranch: taken,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, async () => {
        if (taken === "none") return void 0;
        const branch = {
          id: nextNodeId(ctx, "ifBranch"),
          type: "ifBranch",
          branch: taken,
          startedAt: nowIso(),
          status: "running",
          children: []
        };
        return withGraphNode(
          ctx,
          branch,
          async () => hitIdx >= 0 ? branches[hitIdx].fn() : elseFn()
        );
      });
    },
    async forEach(items, fn) {
      const node = {
        id: nextNodeId(ctx, "forEach"),
        type: "forEach",
        parallel: false,
        itemCount: items.length,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      await withGraphNode(ctx, node, async () => {
        for (let i = 0; i < items.length; i++) {
          await runIteration(ctx, items[i], i, fn);
        }
      });
    },
    async parallelForEach(items, fn) {
      const node = {
        id: nextNodeId(ctx, "forEach"),
        type: "forEach",
        parallel: true,
        itemCount: items.length,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      await withGraphNode(ctx, node, async () => {
        await Promise.all(
          items.map((item, i) => runIteration(ctx, item, i, fn))
        );
      });
    },
    // ============================================================
    // 第三批：带 LLM 判断的高级控制流
    // ============================================================
    evaluate(options) {
      assertNotSealed(ctx, "flow.evaluate");
      const evaluator = options.evaluator;
      const evaluatorName = evaluator?.name ?? EVALUATOR_AGENT_NAME;
      const callCount = (ctx.sessionCallCount.get(evaluatorName) ?? 0) + 1;
      const bindingName = pickBindingName(
        `evaluate.${evaluatorName}`,
        callCount,
        options.bindingName
      );
      const commitCount = () => ctx.sessionCallCount.set(evaluatorName, callCount);
      const traceFile = `trace/${bindingName}.json`;
      const node = {
        id: nextNodeId(ctx, "evaluate"),
        type: "evaluate",
        kind: options.kind,
        question: options.question,
        options: options.kind === "choice" ? [...options.options] : void 0,
        bindingName,
        traceFile,
        evaluatorAgent: evaluatorName,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, async () => {
        const startedAt = node.startedAt;
        const evalResult = await runEvaluator(options, evaluator);
        const endedAt = nowIso();
        node.rawAnswer = evalResult.raw;
        node.tokens = {
          input: evalResult.inputTokens ?? 0,
          output: evalResult.outputTokens ?? 0
        };
        const parsed = parseEvaluateAnswer(evalResult.raw, options);
        node.parsedValue = parsed;
        const trace = {
          agent: evaluatorName,
          provider: evalResult.provider,
          model: evalResult.model,
          startedAt,
          endedAt,
          durationMs: 0,
          system: evalResult.system,
          userPrompt: evalResult.userPrompt,
          context: options.context,
          output: evalResult.raw,
          inputTokens: evalResult.inputTokens,
          outputTokens: evalResult.outputTokens,
          cacheReadTokens: evalResult.cacheReadTokens,
          cacheWriteTokens: evalResult.cacheWriteTokens
        };
        const tracePath = path.join(ctx.runDir, traceFile);
        const bindingMeta = {
          name: bindingName,
          producedBy: evaluatorName,
          producedAt: endedAt,
          tokens: {
            input: evalResult.inputTokens ?? 0,
            output: evalResult.outputTokens ?? 0
          },
          sourceNode: node.id
        };
        ctx.writeQueue = ctx.writeQueue.then(async () => {
          trace.durationMs = node.durationMs ?? 0;
          await writeFile(tracePath, JSON.stringify(trace, null, 2), "utf8");
          await writeBinding(
            ctx.runDir,
            bindingName,
            JSON.stringify({ value: parsed }, null, 2),
            bindingMeta
          );
        });
        await ctx.writeQueue;
        commitCount();
        console.log(
          `  [evaluate.${options.kind}] -> ${JSON.stringify(parsed)} (in=${evalResult.inputTokens} out=${evalResult.outputTokens})`
        );
        return parsed;
      });
    },
    async loopUntil(condFn, fn, options) {
      const maxIterations = options?.maxIterations ?? 8;
      const node = {
        id: nextNodeId(ctx, "loop"),
        type: "loop",
        loopKind: "until",
        iterations: 0,
        maxIterations,
        hitMaxIterations: false,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      await withGraphNode(ctx, node, async () => {
        for (let round = 0; round < maxIterations; round++) {
          await runIteration(ctx, `round-${round}`, round, async (_, idx) => {
            await fn(idx);
          });
          node.iterations = round + 1;
          const done = await condFn();
          if (done) return;
        }
        node.hitMaxIterations = true;
        console.warn(
          `  [loopUntil] hit max ${maxIterations} iterations without satisfying condition, forcing exit`
        );
      });
    },
    async loopWhile(condFn, fn, options) {
      const maxIterations = options?.maxIterations ?? 8;
      const node = {
        id: nextNodeId(ctx, "loop"),
        type: "loop",
        loopKind: "while",
        iterations: 0,
        maxIterations,
        hitMaxIterations: false,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      await withGraphNode(ctx, node, async () => {
        for (let round = 0; round < maxIterations; round++) {
          const should = await condFn();
          if (!should) return;
          await runIteration(ctx, `round-${round}`, round, async (_, idx) => {
            await fn(idx);
          });
          node.iterations = round + 1;
        }
        node.hitMaxIterations = true;
        console.warn(
          `  [loopWhile] hit max ${maxIterations} iterations without stopping, forcing exit`
        );
      });
    },
    async choice(options) {
      if (options.branches.length === 0) {
        throw new Error("flow.choice: branches \u4E0D\u80FD\u4E3A\u7A7A");
      }
      const labels = options.branches.map((b) => b.label);
      const node = {
        id: nextNodeId(ctx, "choice"),
        type: "choice",
        question: options.question,
        options: labels,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, async () => {
        let chosenLabel;
        try {
          chosenLabel = await flow.evaluate({
            kind: "choice",
            question: options.question,
            context: options.context,
            options: labels,
            evaluator: options.evaluator,
            bindingName: options.bindingName
          });
        } catch (err) {
          if (options.defaultLabel && labels.includes(options.defaultLabel)) {
            console.warn(
              `  [choice] evaluate failed, falling back to default branch "${options.defaultLabel}": ${err.message}`
            );
            chosenLabel = options.defaultLabel;
          } else {
            throw err;
          }
        }
        const idx = labels.indexOf(chosenLabel);
        node.chosen = chosenLabel;
        node.chosenIndex = idx;
        const branchNode = {
          id: nextNodeId(ctx, "choiceBranch"),
          type: "choiceBranch",
          branch: chosenLabel,
          index: idx,
          startedAt: nowIso(),
          status: "running",
          children: []
        };
        return withGraphNode(
          ctx,
          branchNode,
          () => options.branches[idx].fn()
        );
      });
    },
    // ============================================================
    // 第四批：数据流原语
    // ============================================================
    async map(items, fn) {
      const node = {
        id: nextNodeId(ctx, "forEach"),
        type: "forEach",
        parallel: false,
        itemCount: items.length,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      const results = new Array(items.length);
      await withGraphNode(ctx, node, async () => {
        for (let i = 0; i < items.length; i++) {
          await runIteration(ctx, items[i], i, async (it, idx) => {
            results[idx] = await fn(it, idx);
          });
        }
      });
      return results;
    },
    async pmap(items, fn) {
      const node = {
        id: nextNodeId(ctx, "forEach"),
        type: "forEach",
        parallel: true,
        itemCount: items.length,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      const results = new Array(items.length);
      await withGraphNode(ctx, node, async () => {
        await Promise.all(
          items.map(
            (it, i) => runIteration(ctx, it, i, async (item, idx) => {
              results[idx] = await fn(item, idx);
            })
          )
        );
      });
      return results;
    },
    async filter(items, predicate) {
      const node = {
        id: nextNodeId(ctx, "forEach"),
        type: "forEach",
        parallel: false,
        itemCount: items.length,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      const keep = new Array(items.length);
      await withGraphNode(ctx, node, async () => {
        for (let i = 0; i < items.length; i++) {
          await runIteration(ctx, items[i], i, async (it, idx) => {
            keep[idx] = await predicate(it, idx);
          });
        }
      });
      return items.filter((_, i) => keep[i]);
    },
    async pfilter(items, predicate) {
      const node = {
        id: nextNodeId(ctx, "forEach"),
        type: "forEach",
        parallel: true,
        itemCount: items.length,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      const keep = new Array(items.length);
      await withGraphNode(ctx, node, async () => {
        await Promise.all(
          items.map(
            (it, i) => runIteration(ctx, it, i, async (item, idx) => {
              keep[idx] = await predicate(item, idx);
            })
          )
        );
      });
      return items.filter((_, i) => keep[i]);
    },
    async reduce(items, fn, init) {
      const node = {
        id: nextNodeId(ctx, "forEach"),
        type: "forEach",
        parallel: false,
        itemCount: items.length,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      let acc = init;
      await withGraphNode(ctx, node, async () => {
        for (let i = 0; i < items.length; i++) {
          await runIteration(ctx, items[i], i, async (it, idx) => {
            acc = await fn(acc, it, idx);
          });
        }
      });
      return acc;
    },
    async pipeline(input, steps) {
      const node = {
        id: nextNodeId(ctx, "pipeline"),
        type: "pipeline",
        stepCount: steps.length,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      let value = input;
      await withGraphNode(ctx, node, async () => {
        for (let i = 0; i < steps.length; i++) {
          const step = steps[i];
          const stepNode = {
            id: nextNodeId(ctx, "pipelineStep"),
            type: "pipelineStep",
            index: i,
            label: step.label,
            startedAt: nowIso(),
            status: "running",
            children: []
          };
          value = await withGraphNode(ctx, stepNode, () => step.fn(value));
        }
      });
      return value;
    },
    // ============================================================
    // 第五批：工程化（retry / evaluateStatic / use）
    // ============================================================
    async retry(fn, options) {
      const maxAttempts = options?.maxAttempts ?? 3;
      const initialDelayMs = options?.initialDelayMs ?? 200;
      const backoff = options?.backoff ?? 2;
      const maxDelayMs = options?.maxDelayMs ?? 8e3;
      const shouldRetry = options?.shouldRetry ?? (() => true);
      const node = {
        id: nextNodeId(ctx, "retry"),
        type: "retry",
        maxAttempts,
        attempts: 0,
        succeeded: false,
        errorTrail: [],
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, async () => {
        let lastErr;
        for (let attempt = 1; attempt <= maxAttempts; attempt++) {
          node.attempts = attempt;
          try {
            const result = await fn();
            node.succeeded = true;
            return result;
          } catch (err) {
            const e = err;
            lastErr = e;
            node.errorTrail.push(`attempt ${attempt}: ${e.message}`);
            console.warn(
              `  [retry] attempt ${attempt}/${maxAttempts} failed: ${e.message}`
            );
            if (!shouldRetry(e, attempt)) {
              throw e;
            }
            if (attempt >= maxAttempts) break;
            const delay = Math.min(
              initialDelayMs * Math.pow(backoff, attempt - 1),
              maxDelayMs
            );
            await new Promise((resolve) => setTimeout(resolve, delay));
          }
        }
        throw lastErr ?? new Error(`flow.retry: exceeded max attempts ${maxAttempts}`);
      });
    },
    async evaluateStatic(options) {
      assertNotSealed(ctx, "flow.evaluateStatic");
      const callCount = (ctx.sessionCallCount.get("__static__") ?? 0) + 1;
      const bindingName = pickBindingName(
        "evaluate.static",
        callCount,
        options.bindingName
      );
      const commitCount = () => ctx.sessionCallCount.set("__static__", callCount);
      const node = {
        id: nextNodeId(ctx, "evaluate"),
        type: "evaluate",
        kind: "static",
        question: options.question,
        staticRule: options.rule.kind,
        bindingName,
        evaluatorAgent: "__static__",
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, async () => {
        const r = options.rule;
        let value;
        if (r.kind === "regex") value = r.pattern.test(r.on);
        else if (r.kind === "contains") value = r.on.includes(r.needle);
        else if (r.kind === "equals") value = r.on === r.expected;
        else if (r.kind === "range") {
          value = (typeof r.min !== "number" || r.value >= r.min) && (typeof r.max !== "number" || r.value <= r.max);
        } else {
          value = await r.fn();
        }
        node.parsedValue = value;
        const meta = {
          name: bindingName,
          producedBy: "__static__",
          producedAt: nowIso(),
          sourceNode: node.id
        };
        ctx.writeQueue = ctx.writeQueue.then(
          () => writeBinding(
            ctx.runDir,
            bindingName,
            JSON.stringify({ value, rule: r.kind }, null, 2),
            meta
          )
        );
        await ctx.writeQueue;
        commitCount();
        console.log(
          `  [evaluate.static] ${r.kind} -> ${value}`
        );
        return value;
      });
    },
    async use(serviceName, args = {}, options) {
      const def = ctx.services.get(serviceName);
      if (!def) {
        throw new Error(`flow.use: \u670D\u52A1 "${serviceName}" \u672A\u6CE8\u518C`);
      }
      return flow.call(
        { __kind: "service", name: serviceName, signature: def.signature },
        args,
        options
      );
    },
    // ============================================================
    // 第六批：顶层结构（block / defineBlock / runBlock / repeat / input / output）
    // ============================================================
    async block(label, fn) {
      const node = {
        id: nextNodeId(ctx, "block"),
        type: "block",
        label,
        isDefined: false,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, () => fn());
    },
    defineBlock(def) {
      const name = assertSafeName("flow.defineBlock", def.name);
      if (ctx.blocks.has(name)) {
        throw new Error(`flow.defineBlock: \u91CD\u590D\u6CE8\u518C block "${name}"`);
      }
      ctx.blocks.set(name, def.body);
      return { __kind: "block", name, description: def.description };
    },
    async runBlock(handle, args = {}) {
      const body = ctx.blocks.get(handle.name);
      if (!body) {
        throw new Error(`flow.runBlock: block "${handle.name}" \u672A\u6CE8\u518C`);
      }
      const callCount = (ctx.blockCallCount.get(handle.name) ?? 0) + 1;
      const node = {
        id: nextNodeId(ctx, "block"),
        type: "block",
        label: handle.name,
        isDefined: true,
        args,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, async () => {
        const result = await body(args);
        ctx.blockCallCount.set(handle.name, callCount);
        return result;
      });
    },
    async repeat(times, fn) {
      const node = {
        id: nextNodeId(ctx, "forEach"),
        type: "forEach",
        parallel: false,
        itemCount: times,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      await withGraphNode(ctx, node, async () => {
        for (let i = 0; i < times; i++) {
          await runIteration(ctx, i, i, async (_, idx) => {
            await fn(idx);
          });
        }
      });
    },
    async input(name, defaultValue) {
      assertNotSealed(ctx, "flow.input");
      const inputName = assertSafeName("flow.input", name);
      if (ctx.inputRegistered.has(inputName)) {
        throw new Error(
          `flow.input: input "${inputName}" \u5DF2\u6CE8\u518C\u3002\u4E24\u6B21\u540C\u540D\u4F1A\u8BA9\u7B2C\u4E00\u6B21\u7684\u503C\u88AB\u9759\u9ED8\u8986\u76D6\u3002\u8BF7\u6362\u4E00\u4E2A\u540D\u5B57\uFF0C\u6216\u53EA\u8C03\u4E00\u6B21\u3002`
        );
      }
      ctx.inputRegistered.add(inputName);
      const prefix = `--input.${inputName}=`;
      let value = defaultValue;
      let fromCli = false;
      for (const arg of process.argv.slice(2)) {
        if (arg.startsWith(prefix)) {
          value = arg.slice(prefix.length);
          fromCli = true;
          break;
        }
      }
      const node = {
        id: nextNodeId(ctx, "input"),
        type: "input",
        name: inputName,
        fromCli,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, async () => {
        const inputPath = path.join(ctx.runDir, "input", `${inputName}.md`);
        ctx.writeQueue = ctx.writeQueue.then(
          () => writeFile(inputPath, value, "utf8")
        );
        await ctx.writeQueue;
        return value;
      });
    },
    async output(name, value) {
      assertNotSealed(ctx, "flow.output");
      const meta = {
        name,
        producedBy: "flow.output",
        producedAt: nowIso(),
        sourceNode: currentParent(ctx.rootGraphNode).id
      };
      ctx.writeQueue = ctx.writeQueue.then(
        () => writeBinding(ctx.runDir, name, value, meta)
      );
      await ctx.writeQueue;
    },
    async exec(opts) {
      assertNotSealed(ctx, "flow.exec");
      const execName = assertSafeName("flow.exec", opts.name);
      const callCount = (ctx.execCallCount.get(execName) ?? 0) + 1;
      const bindingName = pickBindingName(
        execName,
        callCount,
        opts.bindingName
      );
      const commitCount = () => ctx.execCallCount.set(execName, callCount);
      const maxStdoutBytes = opts.maxStdoutBytes !== void 0 ? opts.maxStdoutBytes : 4 * 1024 * 1024;
      const node = {
        id: nextNodeId(ctx, "exec"),
        type: "exec",
        name: execName,
        command: [opts.command, ...opts.args ?? []].join(" ").slice(0, 200),
        bindingName,
        startedAt: nowIso(),
        status: "running",
        children: []
      };
      return withGraphNode(ctx, node, async () => {
        const startTs = Date.now();
        const out = await runSubprocess({
          command: opts.command,
          args: opts.args ?? [],
          stdin: opts.stdin,
          cwd: opts.cwd,
          env: opts.env,
          timeoutMs: opts.timeout ?? 3e5,
          // 用户显式 command/args，不走 cmd.exe（否则括号/引号被吃）。
          // .cmd / .bat shim 用户自己加扩展名。
          useShell: false,
          signal: currentCancelSignal(),
          maxStdoutBytes
        });
        const durationMs = Date.now() - startTs;
        node.exitCode = out.exitCode;
        if (out.truncated) node.truncated = true;
        const truncationNote = out.truncated ? `

... [truncated at ${maxStdoutBytes} bytes by flow.exec maxStdoutBytes; subprocess SIGKILLed. raise maxStdoutBytes or narrow the command's output.]` : "";
        const stdout = out.stdout.replace(/\n+$/, "");
        const result = {
          stdout,
          raw: out.stdout,
          exitCode: out.exitCode,
          durationMs,
          truncated: out.truncated === true
        };
        const bindingMeta = {
          name: bindingName,
          producedBy: `exec:${execName}`,
          producedAt: nowIso(),
          sourceNode: node.id
        };
        ctx.writeQueue = ctx.writeQueue.then(
          () => writeBinding(ctx.runDir, bindingName, stdout + truncationNote, bindingMeta)
        );
        await ctx.writeQueue;
        commitCount();
        console.log(
          `  [exec] ${execName} -> bindings/${bindingName}.md (exit=${out.exitCode} ${durationMs}ms${out.truncated ? " TRUNCATED" : ""})`
        );
        if (!out.truncated && out.exitCode !== 0) {
          throw new Error(
            `flow.exec: subprocess exited abnormally exit=${out.exitCode}. name=${execName} command="${opts.command}"
stderr tail: ${out.stderr.slice(-300)}`
          );
        }
        return result;
      });
    }
  };
  return flow;
}
async function runIteration(ctx, item, index, fn) {
  const node = {
    id: nextNodeId(ctx, "iteration"),
    type: "iteration",
    index,
    itemPreview: previewItem(item),
    startedAt: nowIso(),
    status: "running",
    children: []
  };
  await withGraphNode(ctx, node, () => fn(item, index));
}

// src/run.ts
var ctxStorage = new AsyncLocalStorage2();
function pickProvider2() {
  const engine = (process.env.FLOW_ENGINE ?? "claude").toLowerCase();
  return {
    provider: `cli:${engine}`,
    call: callViaCli,
    defaultModel: process.env.FLOW_MODEL ?? ""
  };
}
function nowIso2() {
  return (/* @__PURE__ */ new Date()).toISOString();
}
function aggregateTokens(root) {
  const user = { calls: 0, input: 0, output: 0 };
  const internal = { calls: 0, input: 0, output: 0 };
  const visit = (node) => {
    const n = node;
    if (n.tokens && n.cached !== true) {
      const owner = n.evaluatorAgent ?? n.agent ?? "";
      const bucket = owner.startsWith("__") ? internal : user;
      bucket.calls += 1;
      bucket.input += n.tokens.input ?? 0;
      bucket.output += n.tokens.output ?? 0;
    }
    if (Array.isArray(n.children)) {
      for (const child of n.children) visit(child);
    }
  };
  visit(root);
  return {
    user,
    internal,
    calls: user.calls + internal.calls,
    input: user.input + internal.input,
    output: user.output + internal.output
  };
}
function formatTokenCount(n) {
  if (n < 1e3) return String(n);
  if (n < 1e6) return `${(n / 1e3).toFixed(1)}k`;
  return `${(n / 1e6).toFixed(2)}M`;
}
function makeRunId() {
  const d = /* @__PURE__ */ new Date();
  const pad = (n) => n.toString().padStart(2, "0");
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  const rand = Math.random().toString(36).slice(2, 8);
  return `${stamp}-${rand}`;
}
function buildUserMessage2(invocation) {
  if (!invocation.context || Object.keys(invocation.context).length === 0) {
    return invocation.prompt;
  }
  const ctxBlock = Object.entries(invocation.context).map(([key, value]) => `## context.${key}

${value}`).join("\n\n---\n\n");
  return `${ctxBlock}

---

${invocation.prompt}`;
}
function readCliResumeId() {
  for (const arg of process.argv.slice(2)) {
    if (arg.startsWith("--resume=")) return arg.slice("--resume=".length);
    if (arg === "--resume") {
      throw new Error(
        `--resume \u9700\u8981\u5E26 runId\uFF1A--resume=<runId> \u6216 --resume=last`
      );
    }
  }
  return void 0;
}
async function loadResumeCache(oldRunDir) {
  const cache = /* @__PURE__ */ new Map();
  const bindingsDir = path2.join(oldRunDir, "bindings");
  let entries;
  try {
    entries = await readdir(bindingsDir);
  } catch {
    return cache;
  }
  for (const entry of entries) {
    if (!entry.endsWith(".md")) continue;
    const name = entry.slice(0, -".md".length);
    const mdPath = path2.join(bindingsDir, entry);
    const metaPath = path2.join(bindingsDir, `${name}.meta.json`);
    let content;
    try {
      content = await readFile(mdPath, "utf8");
    } catch {
      continue;
    }
    let inputHash;
    try {
      const metaRaw = await readFile(metaPath, "utf8");
      const meta = JSON.parse(metaRaw);
      inputHash = meta.inputHash;
    } catch {
    }
    cache.set(name, { content, inputHash });
  }
  return cache;
}
async function pickLatestRunId(runsDir) {
  let entries;
  try {
    entries = await readdir(runsDir);
  } catch {
    throw new Error(`--resume=last \u5931\u8D25\uFF1A${runsDir} \u4E0D\u5B58\u5728\u6216\u4E0D\u53EF\u8BFB`);
  }
  const dirs = [];
  for (const e of entries) {
    const full = path2.join(runsDir, e);
    try {
      const s = await stat(full);
      if (s.isDirectory()) dirs.push(e);
    } catch {
    }
  }
  if (dirs.length === 0) {
    throw new Error(`--resume=last \u5931\u8D25\uFF1A${runsDir} \u4E0B\u6CA1\u6709\u4EFB\u4F55 run \u76EE\u5F55`);
  }
  dirs.sort();
  return dirs[dirs.length - 1];
}
async function gcRuns(runsDir, opts = {
  keepCount: 50,
  keepDays: 7
}) {
  const { keepCount, keepDays, excludeRunId } = opts;
  if (keepCount <= 0 && keepDays <= 0) return [];
  let entries;
  try {
    entries = await readdir(runsDir);
  } catch {
    return [];
  }
  const runs = [];
  for (const e of entries) {
    if (e === excludeRunId) continue;
    const full = path2.join(runsDir, e);
    try {
      const s = await stat(full);
      if (s.isDirectory()) runs.push({ id: e, mtimeMs: s.mtimeMs });
    } catch {
    }
  }
  if (runs.length === 0) return [];
  runs.sort((a, b) => a.id < b.id ? 1 : a.id > b.id ? -1 : 0);
  const keepIds = /* @__PURE__ */ new Set();
  if (keepCount > 0) {
    for (const r of runs.slice(0, keepCount)) keepIds.add(r.id);
  }
  if (keepDays > 0) {
    const cutoff = Date.now() - keepDays * 24 * 60 * 60 * 1e3;
    for (const r of runs) if (r.mtimeMs >= cutoff) keepIds.add(r.id);
  }
  const deleted = [];
  for (const r of runs) {
    if (keepIds.has(r.id)) continue;
    try {
      await rm(path2.join(runsDir, r.id), { recursive: true, force: true });
      deleted.push(r.id);
    } catch {
    }
  }
  return deleted;
}
function resolveRunsGcConfig() {
  const parse = (raw, dflt) => {
    if (raw === void 0) return dflt;
    const n = Number(raw);
    return Number.isFinite(n) ? n : dflt;
  };
  return {
    keepCount: parse(process.env.FLOW_RUNS_KEEP_COUNT, 50),
    keepDays: parse(process.env.FLOW_RUNS_KEEP_DAYS, 7)
  };
}
function Agent(config) {
  const systemPrompt = config.system ?? config.prompt;
  if (!systemPrompt) {
    throw new Error(`Agent({name: "${config.name}"}) \u7F3A\u5C11 system / prompt \u5B57\u6BB5`);
  }
  const callable = async (invocation) => {
    const ctx = ctxStorage.getStore();
    const { provider, call, defaultModel } = pickProvider2();
    const model = config.model ?? defaultModel;
    const maxTokens = config.maxTokens ?? 8192;
    const temperature = config.temperature ?? 1;
    const userPrompt = buildUserMessage2(invocation);
    const startedAt = nowIso2();
    const startTs = Date.now();
    const result = await call({
      model,
      system: systemPrompt,
      userPrompt,
      maxTokens,
      temperature,
      thinking: config.thinking
    });
    const endedAt = nowIso2();
    const durationMs = Date.now() - startTs;
    const trace = {
      agent: config.name,
      provider,
      model,
      startedAt,
      endedAt,
      durationMs,
      system: systemPrompt,
      userPrompt,
      context: invocation.context,
      output: result.text,
      inputTokens: result.inputTokens,
      outputTokens: result.outputTokens,
      cacheReadTokens: result.cacheReadTokens,
      cacheWriteTokens: result.cacheWriteTokens
    };
    if (ctx) {
      const count = (ctx.sessionCallCount.get(config.name) ?? 0) + 1;
      ctx.sessionCallCount.set(config.name, count);
      const traceName = count === 1 ? `${config.name}.json` : `${config.name}.${count}.json`;
      const tracePath = path2.join(ctx.runDir, "trace", traceName);
      ctx.writeQueue = ctx.writeQueue.then(
        () => writeFile2(tracePath, JSON.stringify(trace, null, 2), "utf8")
      );
      await ctx.writeQueue;
      console.log(
        `  [agent] ${config.name} done in ${durationMs}ms (provider=${provider} model=${model} in=${result.inputTokens} out=${result.outputTokens} cacheR=${result.cacheReadTokens} cacheW=${result.cacheWriteTokens})`
      );
    }
    return result.text;
  };
  return Object.assign(callable, {
    __agentName: config.name,
    __config: { ...config, system: systemPrompt }
  });
}
async function run(fn, options = {}) {
  const runsDir = options.runsDir ?? path2.resolve("runs");
  let resumeId = options.resumeFromRunId ?? readCliResumeId();
  if (resumeId === "last") {
    resumeId = await pickLatestRunId(runsDir);
  }
  const isResumed = !!resumeId;
  const runId = resumeId ?? makeRunId();
  const runDir = path2.join(runsDir, runId);
  await mkdir(path2.join(runDir, "input"), { recursive: true });
  await mkdir(path2.join(runDir, "bindings"), { recursive: true });
  await mkdir(path2.join(runDir, "trace"), { recursive: true });
  if (!isResumed) {
    const gc = resolveRunsGcConfig();
    try {
      const deleted = await gcRuns(runsDir, { ...gc, excludeRunId: runId });
      if (deleted.length > 0) {
        console.log(
          `[run] runs GC: removed ${deleted.length} old run dir${deleted.length > 1 ? "s" : ""} (keep latest ${gc.keepCount} + ${gc.keepDays}d; tune via FLOW_RUNS_KEEP_COUNT/DAYS)`
        );
      }
    } catch {
    }
  }
  const resumeCache = isResumed ? await loadResumeCache(runDir) : /* @__PURE__ */ new Map();
  if (isResumed) {
    console.log(
      `[run] resuming ${runId} (${resumeCache.size} cached bindings)`
    );
  }
  const programSnapshotPath = options.programPath ?? process.argv[1];
  if (programSnapshotPath) {
    try {
      await copyFile(programSnapshotPath, path2.join(runDir, "program.ts"));
    } catch (err) {
      console.warn(
        `[warn] failed to snapshot program: ${err.message}`
      );
    }
  }
  console.log(`[run] ${runId}`);
  console.log(`[run] dir: ${runDir}`);
  const startedAt = nowIso2();
  const startTs = Date.now();
  const rootGraphNode = {
    id: "run-root",
    type: "run",
    startedAt,
    status: "running",
    children: []
  };
  const internal = {
    runId,
    runDir,
    rootGraphNode,
    services: /* @__PURE__ */ new Map(),
    blocks: /* @__PURE__ */ new Map(),
    sessionCallCount: /* @__PURE__ */ new Map(),
    serviceCallCount: /* @__PURE__ */ new Map(),
    blockCallCount: /* @__PURE__ */ new Map(),
    execCallCount: /* @__PURE__ */ new Map(),
    writeQueue: Promise.resolve(),
    resumeCache,
    isResumed,
    nodeIdSeq: 0,
    writtenBindings: /* @__PURE__ */ new Set(),
    inputRegistered: /* @__PURE__ */ new Set(),
    sealed: false
  };
  const flowApi = createFlowAPI(internal);
  const ctx = {
    runId,
    runDir,
    flow: flowApi,
    // #19: ctx.input 委托给 flow.input，复用同名去重 + 入图逻辑，行为一致。
    input(name, defaultValue) {
      return flowApi.input(name, defaultValue);
    },
    async save(name, value) {
      await writeFile2(
        path2.join(runDir, "bindings", `${name}.md`),
        value,
        "utf8"
      );
    }
  };
  let status = "ok";
  let errorMessage;
  let caughtError;
  try {
    await ctxStorage.run(internal, () => fn(ctx));
  } catch (err) {
    status = "error";
    errorMessage = err.message;
    caughtError = err;
    console.error(`[run] \u{1F61F} \u6CA1\u8DD1\u6210\u529F\uFF0C\u6211\u770B\u4E86\u4E0B\uFF0C\u8FD9\u6B21\u6CA1\u80FD\u5B8C\u6210\u3002`);
    console.error(`[run] \u6280\u672F\u7EC6\u8282\u90FD\u8BB0\u5728\u4E86 meta.json \u91CC\uFF08\u4E0B\u9762\u90A3\u4E2A\u76EE\u5F55\uFF09\uFF0C\u9700\u8981\u6392\u67E5\u53EF\u4EE5\u770B\u5B83\u3002`);
  } finally {
    await internal.writeQueue;
    internal.sealed = true;
  }
  const endedAt = nowIso2();
  const durationMs = Date.now() - startTs;
  rootGraphNode.status = status;
  rootGraphNode.endedAt = endedAt;
  rootGraphNode.durationMs = durationMs;
  if (errorMessage) rootGraphNode.errorMessage = errorMessage;
  const graph = { runId, root: rootGraphNode };
  await writeFile2(
    path2.join(runDir, "execution-graph.json"),
    JSON.stringify(graph, null, 2),
    "utf8"
  );
  const totals = aggregateTokens(rootGraphNode);
  const allSessionCalls = Object.fromEntries(internal.sessionCallCount);
  const sessionCalls = {};
  const evaluatorCalls = {};
  for (const [k, v] of Object.entries(allSessionCalls)) {
    if (k.startsWith("__")) evaluatorCalls[k] = v;
    else sessionCalls[k] = v;
  }
  const meta = {
    runId,
    startedAt,
    endedAt,
    durationMs,
    status,
    errorMessage,
    sessionCalls,
    // #22：内部 evaluator 调用单列，不混进 sessionCalls。
    evaluatorCalls,
    serviceCalls: Object.fromEntries(internal.serviceCallCount),
    // #15：totalTokens / llmCalls 保留为 user+internal 合计（账单维度诚实总额），
    //      另拆 userTokens / evaluatorTokens 让用户能区分自己的花费 vs 框架内部评估。
    totalTokens: { input: totals.input, output: totals.output },
    llmCalls: totals.calls,
    userTokens: { input: totals.user.input, output: totals.user.output },
    userLlmCalls: totals.user.calls,
    evaluatorTokens: { input: totals.internal.input, output: totals.internal.output },
    evaluatorLlmCalls: totals.internal.calls,
    resumed: isResumed,
    resumeFromRunId: isResumed ? runId : void 0
  };
  await writeFile2(
    path2.join(runDir, "meta.json"),
    JSON.stringify(meta, null, 2),
    "utf8"
  );
  console.log(`[run] ${status} in ${durationMs}ms`);
  if (totals.calls > 0) {
    const split = totals.internal.calls > 0 ? ` (${totals.user.calls} user + ${totals.internal.calls} evaluator)` : "";
    const usagePart = totals.input === 0 && totals.output === 0 ? `\u7528\u91CF\u672A\u56DE\u4F20\uFF08\u8BE5\u5F15\u64CE CLI \u4E0D\u4E0A\u62A5 token\uFF09` : `~${formatTokenCount(totals.input)} in / ${formatTokenCount(totals.output)} out tokens`;
    console.log(
      `[run] total: ${totals.calls} LLM call${totals.calls > 1 ? "s" : ""}${split}, ${usagePart}`
    );
  }
  console.log(`[run] artifacts at ${runDir}`);
  if (status === "error") {
    process.exitCode = 1;
  }
  if (status === "error" && options.throwOnError) {
    throw caughtError instanceof Error ? caughtError : new Error(errorMessage ?? "run() failed");
  }
  return { runId, runDir, status };
}

// src/index.ts
dotenvConfig({ override: true });
(() => {
  const stale = [
    "LLM_PROVIDER",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_BETA",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL"
  ].filter((k) => process.env[k] !== void 0 && process.env[k] !== "");
  if (stale.length > 0) {
    console.warn(
      `[warn] \u68C0\u6D4B\u5230 v0.7 \u5DF2\u5E9F\u5F03\u7684 env \u5B57\u6BB5\uFF1A${stale.join(", ")}\u3002v0.7 \u8D77\u8C03 LLM \u5168\u8D70 CLI \u5F15\u64CE\uFF08FLOW_ENGINE / FLOW_MODEL\uFF09\uFF0C\u8FD9\u4E9B\u5B57\u6BB5\u5DF2\u65E0\u6548\u3002\u8BF7\u5BF9\u7167 .env.example \u6E05\u7406 core/.env\uFF0C\u907F\u514D\u88AB\u5B50\u8FDB\u7A0B\u91CC\u7684\u65E7\u7248 CLI \u8BEF\u8BFB\u3002`
    );
  }
})();
export {
  Agent,
  aggregateTokens,
  assertSafeName,
  formatTokenCount,
  gcRuns,
  pickEngine,
  run
};
