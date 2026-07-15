# LLMRouter Multiline Upstream Arguments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change `psi-agent ai router --upstream` from one outer JSON-array string to one tyro list argument containing one JSON object per candidate.

**Architecture:** `AiRouter` owns a `list[str]` CLI field and passes it unchanged to `parse_upstreams`. The parser decodes and validates each candidate independently, preserving order and adding index-aware diagnostics; the adapter, routing decision, fallback, proxy, and SSE paths remain unchanged.

**Tech Stack:** Python 3.14, tyro, JSON, pytest, ruff, ty, PowerShell.

---

## Working-tree safety

The Router integration is already in a dirty worktree. Before each task, inspect
the diff for every file being modified. Do not restore or stage unrelated user
changes. Commit only a task's named files when they contain no unrelated work;
otherwise leave the task uncommitted and report it.

## File map

- Modify `src/psi_agent/ai/llmrouter_adapter.py`: accept and validate a list of
  independent JSON objects.
- Modify `src/psi_agent/ai/router.py`: expose `upstream: list[str]` through
  tyro and pass it to the parser.
- Modify `tests/psi_agent/ai/test_llmrouter_adapter.py`: parser schema,
  ordering, index-aware diagnostics, and PowerShell hint tests.
- Modify `tests/psi_agent/ai/test_router.py`: retain Router behavior with the
  new configuration shape where setup reaches `AiRouter`.
- Modify `src/psi_agent/ai/AGENTS.md`: replace stale Router CLI syntax and
  document one-object-per-line semantics.
- Check `README.md` and `README_en.md`: update only if active Router examples
  exist or are added by the current integration.

### Task 1: Parse one JSON object per upstream element

**Files:**
- Modify: `tests/psi_agent/ai/test_llmrouter_adapter.py`
- Modify: `src/psi_agent/ai/llmrouter_adapter.py`

- [ ] **Step 1: Replace the existing array parser tests with failing list-element tests**

Use this public contract:

```python
def test_parse_upstreams_accepts_independent_json_objects_in_order() -> None:
    raw = [
        json.dumps(
            {
                "addr": "http://127.0.0.1:8101",
                "model": "qwen-plus",
                "description": "General tasks",
            }
        ),
        json.dumps(
            {
                "addr": "http://127.0.0.1:8102",
                "model": "deepseek-reasoner",
                "description": "Complex reasoning",
            }
        ),
    ]

    assert parse_upstreams(raw) == [
        RouteTarget("http://127.0.0.1:8101", "qwen-plus", "General tasks"),
        RouteTarget(
            "http://127.0.0.1:8102",
            "deepseek-reasoner",
            "Complex reasoning",
        ),
    ]
```

Add explicit rejection tests:

```python
@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "at least one"),
        (["not-json"], r"upstream\[0\].*valid JSON"),
        (["[]"], r"upstream\[0\].*object"),
        (["{}"], r"upstream\[0\].*addr"),
        (
            ['{"addr":"a","model":"m","description":"d","api_key":"x"}'],
            "unsupported fields",
        ),
        (
            [
                '{"addr":"a","model":"m","description":"d"}',
                '{"addr":"b","model":"m","description":"d2"}',
            ],
            "duplicate upstream model",
        ),
    ],
)
def test_parse_upstreams_rejects_invalid_elements(raw: list[str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_upstreams(raw)
```

Update the diagnostic test so malformed JSON is the second element and assert
the message includes `upstream[1]`, `line 1 column 3`, `PowerShell`, and
`\"addr\"`.

- [ ] **Step 2: Run the parser tests and verify RED**

Run:

```powershell
& '.\.venv\Scripts\pytest.exe' tests\psi_agent\ai\test_llmrouter_adapter.py -q `
  --override-ini addopts='' `
  --basetemp '.\.pytest-tmp-multiline-red' `
  -p no:cacheprovider
```

Expected: the tests fail because `parse_upstreams` still passes the whole list
to `json.loads` or still expects one outer array string.

- [ ] **Step 3: Implement the minimal indexed parser**

Change the parser shape to:

```python
def parse_upstreams(raw: list[str]) -> list[RouteTarget]:
    if not raw:
        raise ValueError("--upstream must provide at least one JSON object")

    targets: list[RouteTarget] = []
    models: set[str] = set()
    allowed = {"addr", "model", "description"}
    for index, encoded in enumerate(raw):
        location = f"upstream[{index}]"
        try:
            value: Any = json.loads(encoded)
        except json.JSONDecodeError as exc:
            message = (
                f"{location} must be valid JSON: {exc.msg} "
                f"at line {exc.lineno} column {exc.colno}"
            )
            if encoded.lstrip().startswith("{") and '"' not in encoded:
                message += (
                    r'; PowerShell removed the JSON quotes; escape each inner quote as \" '
                    r'(for example: {\"addr\":\"http://127.0.0.1:8101\",...})'
                )
            raise ValueError(message) from exc
        if not isinstance(value, dict):
            raise ValueError(f"{location} must be a JSON object")
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"{location} has unsupported fields: {sorted(unknown)!r}")
        target = RouteTarget(
            addr=_required_text(value, "addr", location),
            model=_required_text(value, "model", location),
            description=_required_text(value, "description", location),
        )
        if target.model in models:
            raise ValueError(f"duplicate upstream model: {target.model!r}")
        models.add(target.model)
        targets.append(target)
    return targets
```

Do not accept an outer JSON array as a compatibility fallback.

- [ ] **Step 4: Run the adapter tests and verify GREEN**

Run the command from Step 2 with basetemp
`.pytest-tmp-multiline-green`. Expected: all adapter tests pass; the existing
Torch/Python 3.14 warnings may remain but no test fails.

- [ ] **Step 5: Run focused quality checks**

```powershell
& '.\.venv\Scripts\ruff.exe' format src\psi_agent\ai\llmrouter_adapter.py tests\psi_agent\ai\test_llmrouter_adapter.py
& '.\.venv\Scripts\ruff.exe' check src\psi_agent\ai\llmrouter_adapter.py tests\psi_agent\ai\test_llmrouter_adapter.py
uv run ty check src/psi_agent/ai/llmrouter_adapter.py tests/psi_agent/ai/test_llmrouter_adapter.py
```

Expected: each command exits zero without new ignores.

### Task 2: Expose the multiline list through `AiRouter`

**Files:**
- Modify: `tests/psi_agent/ai/test_router.py`
- Modify: `src/psi_agent/ai/router.py`

- [ ] **Step 1: Add failing dataclass and validation tests**

Add tests that assert the default is an independent empty list and that the
new parser input reaches startup validation:

```python
def test_ai_router_upstream_defaults_are_not_shared() -> None:
    first = AiRouter(session_socket="http://127.0.0.1:8100")
    second = AiRouter(session_socket="http://127.0.0.1:8101")

    first.upstream.append('{"addr":"a","model":"m","description":"d"}')

    assert second.upstream == []


@pytest.mark.anyio
async def test_ai_router_rejects_empty_upstream_list() -> None:
    router = AiRouter(
        session_socket="http://127.0.0.1:8100",
        router_model="router-small",
        router_base_url="https://router.example/v1",
        upstream=[],
    )

    with pytest.raises(ValueError, match="at least one"):
        await router.run()
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
& '.\.venv\Scripts\pytest.exe' tests\psi_agent\ai\test_router.py -q `
  --override-ini addopts='' `
  --basetemp '.\.pytest-tmp-router-list-red' `
  -p no:cacheprovider
```

Expected: construction or mutation fails because `upstream` is still a string.

- [ ] **Step 3: Change the dataclass field to a list factory**

Import `field` alongside `dataclass` and define:

```python
upstream: list[str] = field(default_factory=list)
"""Candidate JSON objects supplied as one or more values after --upstream."""
```

Leave `targets = parse_upstreams(self.upstream)` unchanged because Task 1 now
accepts the list directly. Do not change selection, timeout, fallback, or proxy
logic.

- [ ] **Step 4: Run Router and adapter tests and verify GREEN**

```powershell
& '.\.venv\Scripts\pytest.exe' `
  tests\psi_agent\ai\test_llmrouter_adapter.py `
  tests\psi_agent\ai\test_router.py `
  -q --override-ini addopts='' `
  --basetemp '.\.pytest-tmp-router-list-green' `
  -p no:cacheprovider
```

Expected: all focused tests pass.

### Task 3: Verify the real tyro CLI contract

**Files:**
- Modify: `tests/psi_agent/ai/test_router.py`
- Modify if required by a verified failure: `src/psi_agent/cli.py`

- [ ] **Step 1: Add a failing tyro argument parsing test**

Parse `AiRouter` without starting the service:

```python
import tyro


def test_tyro_accepts_one_upstream_option_with_multiple_json_values() -> None:
    first = '{"addr":"http://127.0.0.1:8101","model":"qwen-plus","description":"General"}'
    second = '{"addr":"http://127.0.0.1:8102","model":"reasoner","description":"Reasoning"}'

    router = tyro.cli(
        AiRouter,
        args=[
            "--session-socket",
            "http://127.0.0.1:8100",
            "--router-model",
            "router-small",
            "--router-base-url",
            "https://router.example/v1",
            "--upstream",
            first,
            second,
        ],
    )

    assert router.upstream == [first, second]
```

- [ ] **Step 2: Run the CLI parsing test and verify RED or document immediate GREEN**

Run only the new test with direct pytest and workspace basetemp. If it passes
immediately after Task 2, record that this is expected framework behavior
verified at the real tyro boundary; do not manufacture a production change.

- [ ] **Step 3: Verify generated help**

```powershell
uv run psi-agent ai router --help
```

Expected: help presents `--upstream STR [STR ...]` or tyro's equivalent
multi-value notation, and still includes all Router model, default, timeout,
context, detail-log, and verbose options.

- [ ] **Step 4: Verify PowerShell native argument transport**

Use a non-starting Python argv probe:

```powershell
& '.\.venv\Scripts\python.exe' -c "import sys,json; print([json.loads(x) for x in sys.argv[1:]])" `
  '{\"addr\":\"http://127.0.0.1:8101\",\"model\":\"qwen-plus\",\"description\":\"General\"}' `
  '{\"addr\":\"http://127.0.0.1:8102\",\"model\":\"reasoner\",\"description\":\"Reasoning\"}'
```

Expected: Python prints a two-object list with quoted keys and values intact.

### Task 4: Synchronize active documentation

**Files:**
- Modify: `src/psi_agent/ai/AGENTS.md`
- Check and modify when applicable: `README.md`
- Check and modify when applicable: `README_en.md`

- [ ] **Step 1: Replace the stale Router section in AI documentation**

Document this exact PowerShell shape:

```powershell
uv run psi-agent ai router `
  --session-socket "http://127.0.0.1:8100" `
  --router-model "qwen-turbo" `
  --router-base-url "https://router.example/v1" `
  --router-api-key "sk-router" `
  --upstream `
    '{\"addr\":\"http://127.0.0.1:8101\",\"model\":\"qwen-plus\",\"description\":\"General Chinese tasks\"}' `
    '{\"addr\":\"http://127.0.0.1:8102\",\"model\":\"deepseek-reasoner\",\"description\":\"Complex reasoning\"}' `
  --default-model "qwen-plus"
```

State that the outer JSON array form is no longer accepted, candidate order
controls the implicit fallback, and every object has exactly three fields.

- [ ] **Step 2: Search active documentation for stale syntax**

```powershell
rg -n "--upstream.*\[|JSON array|JSON 数组|route-model|semantic-router|round_robin|difficulty" `
  src/psi_agent/ai/AGENTS.md README.md README_en.md
```

Expected: no active Router instructions describe the old outer-array or old
semantic-policy contract. Do not rewrite historical specifications or plans.

- [ ] **Step 3: Compare documented flags to CLI help**

Run `uv run psi-agent ai router --help` and verify every flag in the example
exists with the documented spelling.

### Task 5: Final verification

**Files:**
- Verify all files modified by Tasks 1-4.

- [ ] **Step 1: Run focused tests**

```powershell
& '.\.venv\Scripts\pytest.exe' `
  tests\psi_agent\ai\test_llmrouter_adapter.py `
  tests\psi_agent\ai\test_router.py `
  -q --override-ini addopts='' `
  --basetemp '.\.pytest-tmp-multiline-final' `
  -p no:cacheprovider
```

Expected: all tests pass. Report Torch/Python 3.14 warnings separately rather
than treating them as upstream parser failures.

- [ ] **Step 2: Run formatting, lint, and type checks**

```powershell
uv run ruff format --check src/psi_agent/ai tests/psi_agent/ai
uv run ruff check src/psi_agent/ai tests/psi_agent/ai
uv run ty check src/psi_agent/ai tests/psi_agent/ai
```

Expected: all commands exit zero with no new suppressions.

- [ ] **Step 3: Run the AI-layer suite**

Use direct pytest with `--override-ini addopts=''`, a workspace basetemp, and
`-p no:cacheprovider` if the repository's configured coverage/temp behavior
prevents focused execution:

```powershell
& '.\.venv\Scripts\pytest.exe' tests\psi_agent\ai -q `
  --override-ini addopts='' `
  --basetemp '.\.pytest-tmp-ai-final' `
  -p no:cacheprovider
```

Expected: all AI tests pass.

- [ ] **Step 4: Verify diff hygiene**

```powershell
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors. Separate this feature's adapter, Router, test,
and documentation changes from pre-existing dirty files in the final report.
