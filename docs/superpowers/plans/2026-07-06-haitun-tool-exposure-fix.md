# Haitun Tool Exposure Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the haitun-workspace system prompt's ## Tooling section list the tools the agent can actually call, by deriving names from real async functions instead of filenames, and by filling in missing tool descriptions.

**Architecture:** Two edits inside `examples/haitun-workspace/`, no `src/` changes. `_scan_tool_names` in `systems/system.py` switches from filename-stem scanning to AST parsing of top-level `async def` names, plus a static-map supplement for `@mcp` files (serper). `CORE_TOOL_SUMMARIES` / `TOOL_ORDER` in `systems/prompt_sections.py` gain six entries. Verification is the existing `python systems/system.py` smoke entry.

**Tech Stack:** Python 3, stdlib `ast`, no new dependencies.

## Global Constraints

- Only modify files under `examples/haitun-workspace/`. Do NOT touch `src/`.
- No new third-party dependencies; use stdlib `ast` only.
- AST parsing must be side-effect free — never import/exec the tool files.
- Per-file parse failures must be swallowed so one bad file cannot blank the whole tool list.
- serper tool names come from `serper-mcp-server` (garylab); prefix is `serper_`. Only list the primary `serper_google_search` in the prompt, with a description noting `serper_*` variants exist.

---

### Task 1: AST-based tool name scanning + serper supplement

**Files:**
- Modify: `examples/haitun-workspace/systems/system.py` — add `import ast`; replace `_scan_tool_names` (currently at lines ~691-700)

**Interfaces:**
- Consumes: nothing new. `_scan_tool_names(workspace_dir: anyio.Path) -> list[str]` keeps its exact signature and async nature — it is awaited at `build_system_prompt` line ~929 (`tools = tool_names or await _scan_tool_names(ws)`).
- Produces: sorted `list[str]` of tool names now reflecting real top-level `async def` names (non-`_`), including `background_list`, plus `serper_google_search` for `@mcp` files, and NO `search` ghost name.

- [ ] **Step 1: Add the `ast` import**

In `examples/haitun-workspace/systems/system.py`, the stdlib import block (lines ~32-42) currently reads:

```python
import contextlib
import hashlib
import json
import logging
import os
import platform
import re
```

Add `import ast` as the first line so the block becomes:

```python
import ast
import contextlib
import hashlib
import json
import logging
import os
import platform
import re
```

- [ ] **Step 2: Replace `_scan_tool_names` with the AST implementation**

Replace the entire current function:

```python
async def _scan_tool_names(workspace_dir: anyio.Path) -> list[str]:
    """Derive tool names from ``workspace/tools/*.py`` filenames (fallback)."""
    tools_dir = workspace_dir / "tools"
    if not await tools_dir.exists():
        return []
    names: list[str] = []
    async for entry in tools_dir.iterdir():
        if await entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
            names.append(entry.stem)
    return sorted(names)
```

with this AST-based version (place the module-level constant `_MCP_TOOL_NAMES` and helper `_scan_tool_file` immediately above the function):

```python
# Tools injected at runtime by an ``@mcp`` decorator are invisible to static
# analysis, so map each mcp-backed tool file to the real tool name(s) it
# surfaces. Keys are file stems; values are the runtime tool names. Only the
# primary tool is listed to keep the prompt's ## Tooling section readable; the
# summary for it notes that ``serper_*`` variants exist.
_MCP_TOOL_NAMES: dict[str, list[str]] = {
    "search": ["serper_google_search"],
}


def _scan_tool_file(source: str) -> list[str]:
    """Return top-level ``async def`` names (non-``_``) in one tool file.

    Uses ``ast`` only — the file is never imported or executed, so tool
    side effects (network, .env reads) do not run at prompt-build time.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_"):
            names.append(node.name)
    return names


async def _scan_tool_names(workspace_dir: anyio.Path) -> list[str]:
    """Derive tool names from real ``async def`` functions in ``tools/*.py``.

    Names come from AST parsing (not filenames), so a file defining several
    tools (e.g. ``background_stop.py`` -> ``background_stop`` +
    ``background_list``) surfaces all of them, and a file whose tools are
    injected by ``@mcp`` at runtime (e.g. ``search.py`` -> ``serper_*``)
    surfaces via ``_MCP_TOOL_NAMES`` instead of a non-existent ``search``.
    """
    tools_dir = workspace_dir / "tools"
    if not await tools_dir.exists():
        return []
    names: set[str] = set()
    async for entry in tools_dir.iterdir():
        if not (await entry.is_file() and entry.suffix == ".py"):
            continue
        if entry.name.startswith("_"):
            continue
        with contextlib.suppress(OSError):
            source = await entry.read_text(encoding="utf-8")
            names.update(_scan_tool_file(source))
        names.update(_MCP_TOOL_NAMES.get(entry.stem, []))
    return sorted(names)
```

- [ ] **Step 3: Smoke-run the system prompt builder**

Run from the repo root:

```bash
python examples/haitun-workspace/systems/system.py
```

Expected: prints the full system prompt (no traceback). It exits 0.

- [ ] **Step 4: Assert the Tooling section is correct**

Run this check (captures the printed prompt and greps the ## Tooling region):

```bash
python examples/haitun-workspace/systems/system.py > /tmp/haitun_prompt.txt 2>/dev/null
echo "--- background_list present? ---"; grep -c "^- background_list" /tmp/haitun_prompt.txt
echo "--- serper_google_search present? ---"; grep -c "serper_google_search" /tmp/haitun_prompt.txt
echo "--- ghost 'search' absent? (want 0) ---"; grep -cE "^- search: " /tmp/haitun_prompt.txt
```

Expected:
- `background_list present?` → `1`
- `serper_google_search present?` → `1`
- `ghost 'search' absent?` → `0`

(The `^- search: ` pattern targets the exact Tooling bullet `- search: ...`; `search_content` starts with `- search_content` so it will not match the space after `search`.)

- [ ] **Step 5: Commit**

```bash
git add examples/haitun-workspace/systems/system.py
git commit -m "fix(haitun): scan real async tool names via AST, surface serper_* and background_list

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Fill in missing tool summaries and ordering

**Files:**
- Modify: `examples/haitun-workspace/systems/prompt_sections.py` — `CORE_TOOL_SUMMARIES` (lines ~56-74) and `TOOL_ORDER` (lines ~77-95)

**Interfaces:**
- Consumes: `_scan_tool_names` output from Task 1 (the names `find_files`, `search_content`, `flow_run`, `describe_image`, `generate_image`, `serper_google_search` now appear in `tool_names`). `build_tooling_section` (line ~445) looks each name up in `CORE_TOOL_SUMMARIES` and orders by `TOOL_ORDER`.
- Produces: every real tool name now has a one-line summary; the six additions are placed in `TOOL_ORDER` so they render in a sensible position rather than trailing alphabetically.

- [ ] **Step 1: Add the six summaries to `CORE_TOOL_SUMMARIES`**

The dict currently ends (lines ~71-74):

```python
    "memory_add": "Store durable user preferences, project facts, or decisions",
    "memory_search": "Search Fusion Memory for raw evidence",
    "memory_answer_context": "Retrieve a query-grounded Fusion Memory context pack",
}
```

Insert the six new entries before the closing brace so it becomes:

```python
    "memory_add": "Store durable user preferences, project facts, or decisions",
    "memory_search": "Search Fusion Memory for raw evidence",
    "memory_answer_context": "Retrieve a query-grounded Fusion Memory context pack",
    "find_files": "Recursively find file paths matching a glob pattern, newest first",
    "search_content": "Search file contents for a regex or literal string and return matching lines",
    "flow_run": "Run a Fusion Flow (.flow.ts) in the background and poll node-level progress",
    "describe_image": "Return a text description or answer about an image file",
    "generate_image": "Create an image file from a scene description",
    "serper_google_search": "Web search via Serper (needs SERPER_API_KEY); serper_* variants exist for images/news/scholar/webpage_scrape etc.",
}
```

- [ ] **Step 2: Add the names to `TOOL_ORDER`**

`TOOL_ORDER` currently ends (lines ~92-95):

```python
    "memory_add",
    "memory_search",
    "memory_answer_context",
]
```

Extend it so file/search/media tools group after memory, and the mcp search tool sits last (matching the "extra tools after" comment at line ~76):

```python
    "memory_add",
    "memory_search",
    "memory_answer_context",
    "find_files",
    "search_content",
    "flow_run",
    "describe_image",
    "generate_image",
    "serper_google_search",
]
```

- [ ] **Step 3: Re-run the smoke check and confirm descriptions render**

```bash
python examples/haitun-workspace/systems/system.py > /tmp/haitun_prompt.txt 2>/dev/null
for t in find_files search_content flow_run describe_image generate_image serper_google_search; do
  echo "--- $t ---"; grep -E "^- $t: " /tmp/haitun_prompt.txt
done
```

Expected: each of the six prints one `- <tool>: <description>` line (no bare name without a colon+summary).

- [ ] **Step 4: Commit**

```bash
git add examples/haitun-workspace/systems/prompt_sections.py
git commit -m "fix(haitun): add summaries and ordering for find_files/search_content/flow_run/describe_image/generate_image/serper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- Ghost `search` name removed → Task 1 (AST parse yields 0 top-level async defs for `search.py`; `search` never added). Verified in Task 1 Step 4.
- `background_list` surfaces → Task 1 (AST finds both async defs in `background_stop.py`). Verified in Task 1 Step 4.
- 5 missing descriptions (`flow_run`, `find_files`, `search_content`, `describe_image`, `generate_image`) → Task 2 Step 1.
- serper real name surfaced + variant note → Task 1 `_MCP_TOOL_NAMES` + Task 2 `serper_google_search` summary.
- Only workspace changed, no `src/` → both tasks touch only `examples/haitun-workspace/`.
- No real import of mcp files → Task 1 uses `ast.parse` on file text only.
- Verification via `__main__` smoke → Task 1 Step 3-4, Task 2 Step 3.

**2. Placeholder scan:** No TBD/TODO; every code step shows full code; every command shows expected output. Clean.

**3. Type consistency:** `_scan_tool_names` keeps `-> list[str]` and async signature (awaited at build_system_prompt line ~929). `_scan_tool_file(source: str) -> list[str]` and `_MCP_TOOL_NAMES: dict[str, list[str]]` are consistent between definition and use. Tool names used in Task 2 (`serper_google_search` etc.) exactly match those produced in Task 1.
