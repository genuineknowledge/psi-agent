from __future__ import annotations

import hashlib
import json
import re
import shutil

import anyio
import pytest

from psi_agent.fusion_flow import (
    ExecutionTrace,
    PipelineStep,
    RegexRule,
    RunContext,
    TokenUsage,
    aggregate_tokens,
    assert_safe_name,
    flow,
    format_token_count,
    run,
)

_BUNDLE = (
    anyio.Path(__file__).parent.parent.parent.parent
    / "examples"
    / "haitun-workspace"
    / "skills"
    / "fusion-flow"
    / "runtime"
    / "agent-flow-core.bundle.mjs"
)
_BUNDLE_SHA256 = "32fc3dc7edbce5a3016126255ebc31e0dd72f1fae93f2480dffb960173ca900c"
_NODE_BOOTSTRAP = r"""
import { readFileSync } from "node:fs";

const bundlePath = process.argv[2];
const dotenvImport = 'import { config as dotenvConfig } from "dotenv";';
let source = readFileSync(bundlePath, "utf8");
if (source.split(dotenvImport).length - 1 !== 1) {
  throw new Error("expected exactly one dotenv import");
}
source = source.replace(dotenvImport, "const dotenvConfig = () => {};");
const module = await import(
  "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);
"""
_NODE_HELPER_PROBE = (
    _NODE_BOOTSTRAP
    + r"""
const inputs = JSON.parse(process.argv[3]);
const safeNames = inputs.safeNames.map((name) => {
  try {
    return { ok: true, value: module.assertSafeName("differential", name) };
  } catch {
    return { ok: false };
  }
});
console.log(JSON.stringify({
  exports: Object.keys(module).sort(),
  safeNames,
  tokenCounts: inputs.tokenCounts.map((count) => module.formatTokenCount(count)),
  aggregateTokens: module.aggregateTokens({
    children: [
      { agent: "writer", tokens: { input: 20, output: 7 } },
      { evaluatorAgent: "__evaluator__", tokens: { input: 5, output: 1 } },
      {
        cached: true,
        tokens: { input: 1000, output: 500 },
        children: [{ tokens: { input: 10000, output: 5000 } }],
      },
    ],
  }),
}));
"""
)
_NODE_RUN_PROBE = (
    _NODE_BOOTSTRAP
    + r"""
const runsDir = process.argv[3];
const observed = {};
const result = await module.run(async (ctx) => {
  const input = await ctx.flow.input("topic", "default");
  const pipeline = await ctx.flow.pipeline(input, [
    { label: "", fn: async (value) => `${value}-one` },
    { fn: async (value) => value.toUpperCase() },
  ]);
  const filtered = await ctx.flow.filter([1, 2, 3], async (item) => item % 2 === 1);
  const mutable = [1, 2];
  const mutationFiltered = await ctx.flow.filter(mutable, async (item, index) => {
    mutable[index] = item * 10;
    return true;
  });
  const regexAscii = await ctx.flow.evaluateStatic({
    question: "ascii-word",
    rule: { kind: "regex", pattern: /^\w+$/, on: "中文" },
    bindingName: "regex-unicode",
  });
  const repeated = [];
  await ctx.flow.repeat(3, async (index) => repeated.push(index));
  Object.assign(observed, { input, pipeline, filtered, mutationFiltered, regexAscii, repeated });
  await ctx.flow.output("answer", JSON.stringify(observed));
}, { runsDir });
process.stdout.write(`PROBE:${JSON.stringify({ ...result, observed })}\n`);
"""
)
_NODE_EXEC_PROBE = (
    _NODE_BOOTSTRAP
    + r"""
const runsDir = process.argv[3];
let observed;
const result = await module.run(async (ctx) => {
  const execResult = await ctx.flow.exec({
    name: "truncate",
    command: process.execPath,
    args: [
      "-e",
      'process.stderr.write("stderr-first"); process.stdout.write("abcdef");',
    ],
    maxStdoutBytes: 3,
    bindingName: "exec-truncate",
  });
  observed = {
    stdout: execResult.stdout,
    raw: execResult.raw,
    exitCode: execResult.exitCode,
    truncated: execResult.truncated,
  };
}, { runsDir });
process.stdout.write(`PROBE:${JSON.stringify({ ...result, observed })}\n`);
"""
)


def _node_or_skip() -> str:
    """返回 Node.js 路径, 缺少运行时时跳过参考探针。"""

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js executable is required for the TypeScript reference probe")
    return node


@pytest.mark.anyio
async def test_typescript_reference_helpers_match_python_except_windows_names() -> None:
    bundle_bytes = await _BUNDLE.read_bytes()
    assert hashlib.sha256(bundle_bytes).hexdigest() == _BUNDLE_SHA256

    safe_names = (
        "agent",
        "cafe\u0301",
        "中文-name_1.2",
        "COM0",
        "CONSOLE",
        "",
        ".",
        "..",
        "a/b",
        "a\\b",
        "two words",
        "a\x00b",
        "CON",
        "con.txt",
        "name.",
        "a:b",
        'a"b',
        "a*b",
    )
    token_counts = (
        -1,
        0,
        999,
        1_000,
        1_150,
        1_250,
        999_999,
        1_000_000,
        1_005_000,
        1_125_000,
        9_007_199_254_744_999,
    )
    result = await anyio.run_process(
        [
            _node_or_skip(),
            "--input-type=module",
            "-",
            str(_BUNDLE),
            json.dumps({"safeNames": safe_names, "tokenCounts": token_counts}),
        ],
        input=_NODE_HELPER_PROBE.encode(),
    )
    probe = json.loads(result.stdout)

    python_safe_names: list[dict[str, object]] = []
    for name in safe_names:
        try:
            python_safe_names.append({"ok": True, "value": assert_safe_name(name)})
        except ValueError:
            python_safe_names.append({"ok": False})

    assert probe["exports"] == [
        "Agent",
        "aggregateTokens",
        "assertSafeName",
        "formatTokenCount",
        "gcRuns",
        "pickEngine",
        "run",
    ]
    assert probe["tokenCounts"] == [format_token_count(count) for count in token_counts]
    token_graph = ExecutionTrace(
        trace_id="root",
        kind="run",
        label="run",
        started_at="2026-07-26T00:00:00Z",
        children=(
            ExecutionTrace(
                trace_id="writer",
                kind="session",
                label="writer",
                started_at="2026-07-26T00:00:00Z",
                tokens=TokenUsage(calls=1, input=20, output=7),
                metadata={"agent": "writer"},
            ),
            ExecutionTrace(
                trace_id="evaluator",
                kind="evaluate",
                label="evaluator",
                started_at="2026-07-26T00:00:00Z",
                tokens=TokenUsage(calls=1, input=5, output=1),
                metadata={"evaluator_agent": "__evaluator__"},
            ),
            ExecutionTrace(
                trace_id="cached",
                kind="block",
                label="cached",
                started_at="2026-07-26T00:00:00Z",
                cached=True,
                tokens=TokenUsage(calls=1, input=1_000, output=500),
                children=(
                    ExecutionTrace(
                        trace_id="cached-child",
                        kind="session",
                        label="cached child",
                        started_at="2026-07-26T00:00:00Z",
                        tokens=TokenUsage(
                            calls=1,
                            input=10_000,
                            output=5_000,
                        ),
                    ),
                ),
            ),
        ),
    )
    python_tokens = aggregate_tokens(token_graph)
    assert probe["aggregateTokens"] == {
        "user": {
            "calls": python_tokens.user.calls,
            "input": python_tokens.user.input,
            "output": python_tokens.user.output,
        },
        "internal": {
            "calls": python_tokens.internal.calls,
            "input": python_tokens.internal.input,
            "output": python_tokens.internal.output,
        },
        "calls": python_tokens.calls,
        "input": python_tokens.input,
        "output": python_tokens.output,
    }
    differences = [
        {"name": name, "typescript": typescript, "python": python}
        for name, typescript, python in zip(
            safe_names,
            probe["safeNames"],
            python_safe_names,
            strict=True,
        )
        if typescript != python
    ]
    assert differences == [
        {
            "name": name,
            "typescript": {"ok": True, "value": name},
            "python": {"ok": False},
        }
        for name in ("a:b", 'a"b', "a*b")
    ]


@pytest.mark.anyio
async def test_typescript_and_python_core_flow_artifacts_match(tmp_path) -> None:
    ts_runs = anyio.Path(tmp_path, "ts-runs")
    process = await anyio.run_process(
        [
            _node_or_skip(),
            "--input-type=module",
            "-",
            str(_BUNDLE),
            str(ts_runs),
        ],
        input=_NODE_RUN_PROBE.encode(),
    )
    probe_line = next(line for line in process.stdout.decode("utf-8").splitlines() if line.startswith("PROBE:"))
    ts_result = json.loads(probe_line.removeprefix("PROBE:"))

    python_observed: dict[str, object] = {}

    async def append_one(item: object) -> str:
        return f"{item}-one"

    async def uppercase(item: object) -> str:
        return str(item).upper()

    async def is_odd(item: int, _: int) -> bool:
        return item % 2 == 1

    async def python_program(_: RunContext) -> None:
        value = await flow.input("topic", "default")
        pipeline = await flow.pipeline(
            value,
            (
                PipelineStep(label="", fn=append_one),
                PipelineStep(fn=uppercase),
            ),
        )
        filtered = await flow.filter((1, 2, 3), is_odd)
        mutable = [1, 2]

        async def mutate_and_keep(item: int, index: int) -> bool:
            mutable[index] = item * 10
            return True

        mutation_filtered = await flow.filter(mutable, mutate_and_keep)
        regex_ascii = await flow.evaluate_static(
            question="ascii-word",
            rule=RegexRule(
                pattern=re.compile(r"^\w+$", re.ASCII),
                on="中文",
            ),
            binding_name="regex-unicode",
        )
        repeated: list[int] = []

        async def remember(index: int) -> None:
            repeated.append(index)

        await flow.repeat(3, remember)
        python_observed.update(
            input=value,
            pipeline=pipeline,
            filtered=filtered,
            mutationFiltered=mutation_filtered,
            regexAscii=regex_ascii,
            repeated=repeated,
        )
        await flow.output(
            "answer",
            json.dumps(python_observed, separators=(",", ":")),
        )

    python_result = await run(
        python_program,
        runs_dir=anyio.Path(tmp_path, "python-runs"),
        run_id="python-golden",
        keep_count=0,
        keep_days=0,
        throw_on_error=True,
    )

    assert ts_result["observed"] == python_observed
    assert (
        json.loads(await anyio.Path(ts_result["runDir"], "bindings", "answer.md").read_text(encoding="utf-8"))
        == python_observed
    )
    assert (
        json.loads(
            await anyio.Path(
                python_result.run_dir,
                "bindings",
                "answer.md",
            ).read_text(encoding="utf-8")
        )
        == python_observed
    )

    ts_graph = json.loads(await anyio.Path(ts_result["runDir"], "execution-graph.json").read_text(encoding="utf-8"))
    python_graph = json.loads(
        await anyio.Path(
            python_result.run_dir,
            "execution-graph.json",
        ).read_text(encoding="utf-8")
    )
    assert (
        [child["type"] for child in ts_graph["root"]["children"]]
        == [child["kind"] for child in python_graph["root"]["children"]]
        == ["input", "pipeline", "forEach", "forEach", "evaluate", "forEach"]
    )

    ts_progress = [
        json.loads(line)
        for line in (await anyio.Path(ts_result["runDir"], "progress.jsonl").read_text(encoding="utf-8")).splitlines()
    ]
    python_progress = [
        json.loads(line)
        for line in (
            await anyio.Path(
                python_result.run_dir,
                "progress.jsonl",
            ).read_text(encoding="utf-8")
        ).splitlines()
    ]
    assert [(event["event"], event["type"], event.get("status")) for event in ts_progress] == [
        (event["event"], event["type"], event.get("status")) for event in python_progress
    ]


@pytest.mark.anyio
async def test_typescript_and_python_exec_truncation_match(tmp_path) -> None:
    process = await anyio.run_process(
        [
            _node_or_skip(),
            "--input-type=module",
            "-",
            str(_BUNDLE),
            str(anyio.Path(tmp_path, "ts-exec-runs")),
        ],
        input=_NODE_EXEC_PROBE.encode(),
    )
    probe_line = next(line for line in process.stdout.decode("utf-8").splitlines() if line.startswith("PROBE:"))
    ts_result = json.loads(probe_line.removeprefix("PROBE:"))

    python_observed: dict[str, object] = {}

    async def python_program(_: RunContext) -> None:
        exec_result = await flow.exec(
            "truncate",
            (
                _node_or_skip(),
                "-e",
                'process.stderr.write("stderr-first"); process.stdout.write("abcdef");',
            ),
            output_limit=3,
            binding_name="exec-truncate",
        )
        python_observed.update(
            stdout=exec_result.stdout,
            raw=exec_result.raw,
            exitCode=exec_result.exit_code,
            truncated=exec_result.truncated,
        )

    python_result = await run(
        python_program,
        runs_dir=anyio.Path(tmp_path, "python-exec-runs"),
        run_id="python-exec",
        keep_count=0,
        keep_days=0,
        throw_on_error=True,
    )

    ts_observed = ts_result["observed"]
    ts_normalized = {
        **ts_observed,
        "exitCode": 0 if ts_observed["exitCode"] == 0 else "terminated",
    }
    python_normalized = {
        **python_observed,
        "exitCode": 0 if python_observed["exitCode"] == 0 else "terminated",
    }
    for observed in (ts_normalized, python_normalized):
        assert observed.pop("exitCode") in (0, "terminated")
        assert observed == {
            "stdout": "abc",
            "raw": "abc",
            "truncated": True,
        }
    binding_prefix = "abc\n\n... [truncated at 3 bytes by flow.exec"
    assert (
        await anyio.Path(
            ts_result["runDir"],
            "bindings",
            "exec-truncate.md",
        ).read_text(encoding="utf-8")
    ).startswith(binding_prefix)
    assert (
        await anyio.Path(
            python_result.run_dir,
            "bindings",
            "exec-truncate.md",
        ).read_text(encoding="utf-8")
    ).startswith(binding_prefix)
