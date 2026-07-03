// Fusion Flow bundle doctor. Run: npm run doctor.
// Read-only environment check for the standalone skill bundle. Never prints secrets.
import { execFileSync, execSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let hardFail = 0;

function ok(msg) {
  console.log("  \u2713 " + msg);
}

function warn(msg) {
  console.log("  ! " + msg);
}

function bad(msg) {
  console.log("  \u2717 " + msg);
  hardFail++;
}

function quoteForShell(value) {
  return "\"" + String(value).replaceAll("\"", "\\\"") + "\"";
}

function findLocalBin(name) {
  const suffixes = process.platform === "win32" ? [".cmd", ".exe", ""] : [""];
  for (const suffix of suffixes) {
    const candidate = path.join(__dirname, "node_modules", ".bin", name + suffix);
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

function tryCmd(cmd, args) {
  try {
    if (process.platform === "win32") {
      return execSync([cmd, ...args].map(quoteForShell).join(" "), {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      }).trim();
    }
    return execFileSync(cmd, args, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return null;
  }
}

console.log("Fusion Flow bundle doctor\n");

const major = Number(process.versions.node.split(".")[0]);
if (major >= 20) ok("node " + process.versions.node + " (>= 20)");
else bad("node " + process.versions.node + " is too old; need >= 20");

const tsxBin = findLocalBin("tsx");
const tsxV = tsxBin && process.platform !== "win32" ? tryCmd(tsxBin, ["--version"]) : null;
if (tsxBin && process.platform === "win32") ok("tsx available (local npm shim)");
else if (tsxV) ok("tsx available (" + tsxV.split("\n")[0] + ")");
else bad("tsx not found; run `npm install` in this folder");

if (existsSync(path.join(__dirname, "node_modules"))) ok("node_modules present");
else bad("node_modules missing; run `npm install`");

// FLOW_ENGINE is user-controlled, so only known engine names are executed.
const ALLOWED_ENGINES = ["claude", "openclaw", "hermes", "psi", "psi-agent"];
const engineRaw = (process.env.FLOW_ENGINE || "claude").toLowerCase();
const engine = ALLOWED_ENGINES.includes(engineRaw) ? engineRaw : null;
if (engine === null) {
  bad(
    "FLOW_ENGINE=\"" +
      engineRaw.slice(0, 40) +
      "\" is not a known engine; expected one of: " +
      ALLOWED_ENGINES.join(" / "),
  );
} else {
  const engineCmd = engine === "psi" || engine === "psi-agent" ? "psi-agent" : engine;
  const engineArgs = engineCmd === "psi-agent" ? ["run", "--help"] : ["--version"];
  const engineV = tryCmd(engineCmd, engineArgs);
  if (engineV) ok(engineCmd + " CLI on PATH (" + engineV.split("\n")[0] + ")");
  else warn(engineCmd + " CLI not found on PATH; needed for any flow that calls the LLM");
}

if (process.platform === "win32" && engine === "claude") {
  const gb = process.env.CLAUDE_CODE_GIT_BASH_PATH;
  if (gb && existsSync(gb)) ok("git-bash at CLAUDE_CODE_GIT_BASH_PATH");
  else if (existsSync("C:\\Program Files\\Git\\bin\\bash.exe")) ok("git-bash found at default install path");
  else warn("git-bash not located; set CLAUDE_CODE_GIT_BASH_PATH if real claude runs fail");
}

if (engine === "claude") {
  if (process.env.ANTHROPIC_AUTH_TOKEN || process.env.ANTHROPIC_API_KEY) {
    ok("ANTHROPIC_* token set (claude --bare cost-saving path enabled)");
  } else {
    warn("no ANTHROPIC_* token; claude falls back to OAuth (input tokens ~2x bare, see README)");
  }
} else if (engine === "psi" || engine === "psi-agent") {
  if (process.env.FLOW_PSI_WORKSPACE) ok("FLOW_PSI_WORKSPACE set");
  else warn("FLOW_PSI_WORKSPACE not set; required for FLOW_ENGINE=psi");
} else if (engine !== null) {
  ok(engine + " uses its own CLI provider config (ANTHROPIC_* not relevant)");
}

console.log("\nnext: open SKILL.md in an LLM client and ask it to author a .flow.ts for you.");
console.log(hardFail === 0 ? "\ndoctor: ready \u2713" : "\ndoctor: " + hardFail + " blocking issue(s) \u2717");
process.exitCode = hardFail === 0 ? 0 : 1;
