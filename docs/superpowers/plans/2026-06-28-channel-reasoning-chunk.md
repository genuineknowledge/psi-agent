# Channel ReasoningChunk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the Session's `delta.reasoning` SSE stream to terminal channels by introducing a `ReasoningChunk` type that `ChannelCore.post()` emits and CLI/REPL render in dim style.

**Architecture:** Channel-layer-only change. `_types.py` gains `ReasoningChunk` (extending the `Chunk` union). `ChannelCore.post()` is refactored from a single content buffer to a *single active buffer + active kind* model that flushes on type-switch / interval / stream-end, splitting `content`→`TextChunk` and `reasoning`→`ReasoningChunk` in order. `[SEND:...]` scanning stays content-only. CLI/REPL add an `isinstance(chunk, ReasoningChunk)` dim branch; Telegram/Feishu are untouched (they silently ignore the new type).

**Tech Stack:** Python 3.14, `anyio`, `aiohttp`, `pytest` + `pytest-asyncio` (anyio mode), `rich`, `ruff`, `ty`.

**Spec:** `docs/superpowers/specs/2026-06-28-channel-reasoning-chunk.md`

> **Evolution note (as of 2026-06-29):** Point-in-time record. The channel subpackage was later refactored — `Chunk` split into `InputChunk`/`OutputChunk`, and marker / SSE / buffer logic extracted into `_markers.py` / `_stream.py` (errors unified as `ChannelError`). See `src/psi_agent/channel/AGENTS.md` for the current authoritative state.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/psi_agent/channel/_types.py` | Chunk dataclasses + union | Add `ReasoningChunk`, extend `Chunk` |
| `src/psi_agent/channel/_core.py` | SSE→Chunk pipeline | Refactor `post()` to type-aware buffer |
| `src/psi_agent/channel/cli/client.py` | One-shot terminal render | Add dim `ReasoningChunk` branch |
| `src/psi_agent/channel/repl/client.py` | Interactive terminal render | Add dim `ReasoningChunk` branch |
| `src/psi_agent/channel/AGENTS.md` | Channel design doc | Reflect `ReasoningChunk` |
| `tests/psi_agent/channel/test__types.py` | Type unit tests | Add `ReasoningChunk` cases |
| `tests/psi_agent/channel/test__core.py` | Core unit tests | Add reasoning-split cases |

---

## Task 1: Add `ReasoningChunk` type

**Files:**
- Modify: `src/psi_agent/channel/_types.py`
- Test: `tests/psi_agent/channel/test__types.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/psi_agent/channel/test__types.py` (also update the existing import line at the top of the file):

Change the import line:
```python
from psi_agent.channel._types import FileChunk, TextChunk
```
to:
```python
from psi_agent.channel._types import FileChunk, ReasoningChunk, TextChunk
```

Append these tests at the end of the file:
```python
def test_reasoning_chunk_construction():
    rc = ReasoningChunk("thinking...")
    assert rc.text == "thinking..."


def test_reasoning_chunk_union_isinstance():
    rc = ReasoningChunk("hmm")
    tc = TextChunk("hi")
    fc = FileChunk("/a.txt")

    assert isinstance(rc, ReasoningChunk)
    assert not isinstance(rc, TextChunk)
    assert not isinstance(rc, FileChunk)
    assert not isinstance(tc, ReasoningChunk)
    assert not isinstance(fc, ReasoningChunk)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/psi_agent/channel/test__types.py -v`
Expected: FAIL — `ImportError: cannot import name 'ReasoningChunk'`

- [ ] **Step 3: Implement `ReasoningChunk`**

Edit `src/psi_agent/channel/_types.py` so the full file reads:
```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FileChunk:
    path: str


@dataclass
class TextChunk:
    text: str


@dataclass
class ReasoningChunk:
    text: str


Chunk = FileChunk | TextChunk | ReasoningChunk
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/psi_agent/channel/test__types.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/channel/_types.py tests/psi_agent/channel/test__types.py
git commit -m "feat(channel): add ReasoningChunk type"
```

---

## Task 2: Split reasoning stream in `ChannelCore.post()`

**Files:**
- Modify: `src/psi_agent/channel/_core.py`
- Test: `tests/psi_agent/channel/test__core.py`

- [ ] **Step 1: Write the failing tests**

Update the import line at the top of `tests/psi_agent/channel/test__core.py`:
```python
from psi_agent.channel._types import FileChunk, TextChunk
```
to:
```python
from psi_agent.channel._types import FileChunk, ReasoningChunk, TextChunk
```

Append these four tests at the end of `tests/psi_agent/channel/test__core.py`:
```python
@pytest.mark.anyio
async def test_post_reasoning_only(tmp_path):
    """A reasoning-only delta yields a ReasoningChunk."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"thinking"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=0.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert isinstance(chunks[0], ReasoningChunk)
    assert chunks[0].text == "thinking"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_reasoning_then_content_ordered(tmp_path):
    """Type switch flushes reasoning before content, preserving order."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"think"}}]}\n\n')
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"answer"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=10.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 2
    assert isinstance(chunks[0], ReasoningChunk)
    assert chunks[0].text == "think"
    assert isinstance(chunks[1], TextChunk)
    assert chunks[1].text == "answer"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_reasoning_merges_within_interval(tmp_path):
    """Consecutive reasoning deltas within interval merge into one ReasoningChunk."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"a"}}]}\n\n')
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"b"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=10.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert isinstance(chunks[0], ReasoningChunk)
    assert chunks[0].text == "ab"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_send_marker_ignored_in_reasoning(tmp_path):
    """[SEND:...] inside reasoning text does NOT yield a FileChunk."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"[SEND:/a.py] noted"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=0.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert not any(isinstance(c, FileChunk) for c in chunks)
    assert any(isinstance(c, ReasoningChunk) for c in chunks)

    await runner.cleanup()
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `uv run pytest tests/psi_agent/channel/test__core.py -k "reasoning or send_marker_ignored" -v`
Expected: FAIL — current `post()` ignores `reasoning`, so `test_post_reasoning_only` yields 0 chunks (assertion error), etc.

- [ ] **Step 3: Refactor `post()` to the type-aware single active buffer**

Edit `src/psi_agent/channel/_core.py`. Update the import line:
```python
from psi_agent.channel._types import Chunk, FileChunk, TextChunk
```
to:
```python
from psi_agent.channel._types import Chunk, FileChunk, ReasoningChunk, TextChunk
```

Replace the **entire `post` method** (current lines 30–127) with:
```python
    async def post(self, chunks: list[Chunk]) -> AsyncIterator[Chunk]:
        logger.debug(
            f"post: {len(chunks)} chunk(s) — "
            f"FileChunks={sum(1 for c in chunks if isinstance(c, FileChunk))} "
            f"TextChunks={sum(1 for c in chunks if isinstance(c, TextChunk))}"
        )

        parts: list[str] = []
        for chunk in chunks:
            if isinstance(chunk, FileChunk):
                logger.debug(f"  FileChunk → [RECV:{chunk.path}]")
                parts.append(f"[RECV:{chunk.path}]")
            elif isinstance(chunk, TextChunk):
                parts.append(chunk.text)
        content = "\n".join(parts)

        body = {"messages": [{"role": "user", "content": content}], "stream": True}

        buf = ""
        kind: str | None = None
        timer_target: float | None = None

        full_content = ""
        scan_ptr = 0
        emitted: set[str] = set()

        logger.debug(f"  POST {self._endpoint} content_len={len(content)}")
        async with self._session.post(self._endpoint, json=body) as resp:
            logger.debug(f"  HTTP {resp.status}")

            if resp.status != 200:
                msg = await resp.text()
                try:
                    error = json.loads(msg)
                    msg = error.get("error", {}).get("message", msg)
                except Exception:
                    pass
                logger.debug(f"  non-200 error: {msg}")
                raise Exception(msg)

            async for raw_line in resp.content:
                line = raw_line.decode().strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    logger.debug("  SSE stream ended [DONE]")
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.debug(f"  skip malformed SSE: {line[:80]}")
                    continue

                choices = data.get("choices", [])
                if not choices:
                    logger.debug("  skip chunk with 0 choices (heartbeat)")
                    continue
                if len(choices) != 1:
                    raise Exception(f"Expected exactly 1 choice, got {len(choices)}")
                choice = choices[0]

                if choice.get("finish_reason") == "error":
                    delta = choice.get("delta", {})
                    msg = delta.get("content", "Session error")
                    logger.debug(f"  finish_reason=error: {msg}")
                    raise Exception(msg)

                delta = choice.get("delta", {})

                for incoming_kind, text in (
                    ("reasoning", delta.get("reasoning") or ""),
                    ("text", delta.get("content") or ""),
                ):
                    if not text:
                        continue

                    if kind is not None and incoming_kind != kind and buf:
                        if kind == "reasoning":
                            logger.debug(f"  type switch → flush ReasoningChunk ({len(buf)} chars)")
                            yield ReasoningChunk(buf)
                        else:
                            logger.debug(f"  type switch → flush TextChunk ({len(buf)} chars)")
                            yield TextChunk(buf)
                        buf = ""
                        timer_target = None

                    kind = incoming_kind
                    buf += text

                    if incoming_kind == "text":
                        logger.debug(f"  delta.content ({len(text)} chars): {text[:60]}")
                        orig_len = len(full_content)
                        full_content += text
                        new = full_content[scan_ptr:]
                        for match in re.finditer(r"\[SEND:(.+?)\]", new):
                            path = match.group(1)
                            if path not in emitted:
                                logger.debug(f"  [SEND] detected → FileChunk({path})")
                                yield FileChunk(path)
                                emitted.add(path)
                            scan_ptr = orig_len + match.end()
                    else:
                        logger.debug(f"  delta.reasoning ({len(text)} chars): {text[:60]}")

                    if timer_target is None:
                        timer_target = time.monotonic() + self.interval

                    if time.monotonic() >= timer_target:
                        if kind == "reasoning":
                            logger.debug(f"  timer expired → yield ReasoningChunk ({len(buf)} chars)")
                            yield ReasoningChunk(buf)
                        else:
                            logger.debug(f"  timer expired → yield TextChunk ({len(buf)} chars)")
                            yield TextChunk(buf)
                        buf = ""
                        timer_target = None

        if buf:
            if kind == "reasoning":
                logger.debug(f"  stream end flush → ReasoningChunk ({len(buf)} chars)")
                yield ReasoningChunk(buf)
            else:
                logger.debug(f"  stream end flush → TextChunk ({len(buf)} chars)")
                yield TextChunk(buf)
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/psi_agent/channel/test__core.py -k "reasoning or send_marker_ignored" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full core suite to verify no regression**

Run: `uv run pytest tests/psi_agent/channel/test__core.py -v`
Expected: PASS (all original content/`[SEND]`/error/flush tests + 4 new tests)

- [ ] **Step 6: Commit**

```bash
git add src/psi_agent/channel/_core.py tests/psi_agent/channel/test__core.py
git commit -m "feat(channel): split reasoning stream into ReasoningChunk in ChannelCore"
```

---

## Task 3: Render reasoning in CLI and REPL (dim, inline)

**Files:**
- Modify: `src/psi_agent/channel/cli/client.py`
- Modify: `src/psi_agent/channel/repl/client.py`

- [ ] **Step 1: Update CLI client**

In `src/psi_agent/channel/cli/client.py`, change the import:
```python
from psi_agent.channel._types import TextChunk
```
to:
```python
from psi_agent.channel._types import ReasoningChunk, TextChunk
```

Change the streaming loop:
```python
            async for chunk in core.post([TextChunk(message)]):
                if isinstance(chunk, TextChunk):
                    console.print(chunk.text, end="")
```
to:
```python
            async for chunk in core.post([TextChunk(message)]):
                if isinstance(chunk, ReasoningChunk):
                    console.print(chunk.text, end="", style="dim")
                elif isinstance(chunk, TextChunk):
                    console.print(chunk.text, end="")
```

- [ ] **Step 2: Update REPL client**

In `src/psi_agent/channel/repl/client.py`, change the import:
```python
from psi_agent.channel._types import TextChunk
```
to:
```python
from psi_agent.channel._types import ReasoningChunk, TextChunk
```

Change the streaming loop:
```python
                    async for chunk in core.post([TextChunk(user_input)]):
                        if isinstance(chunk, TextChunk):
                            console.print(chunk.text, end="")
```
to:
```python
                    async for chunk in core.post([TextChunk(user_input)]):
                        if isinstance(chunk, ReasoningChunk):
                            console.print(chunk.text, end="", style="dim")
                        elif isinstance(chunk, TextChunk):
                            console.print(chunk.text, end="")
```

- [ ] **Step 3: Verify lint, format, and types**

Run: `uv run ruff check src/psi_agent/channel/cli/client.py src/psi_agent/channel/repl/client.py`
Expected: PASS (no errors; imports sorted `ReasoningChunk, TextChunk`)

Run: `uv run ty check`
Expected: PASS (no type errors)

- [ ] **Step 4: Verify existing channel suite still passes**

Run: `uv run pytest tests/psi_agent/channel/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/channel/cli/client.py src/psi_agent/channel/repl/client.py
git commit -m "feat(channel): render reasoning dim in CLI and REPL"
```

---

## Task 4: Update `channel/AGENTS.md` and run full quality gate

**Files:**
- Modify: `src/psi_agent/channel/AGENTS.md`

- [ ] **Step 1: Update the architecture block**

In `src/psi_agent/channel/AGENTS.md`, change the line:
```
├── _types.py          # FileChunk, TextChunk, Chunk
```
to:
```
├── _types.py          # FileChunk, TextChunk, ReasoningChunk, Chunk
```

- [ ] **Step 2: Document reasoning splitting in the ChannelCore section**

In `src/psi_agent/channel/AGENTS.md`, find the `### ChannelCore` bullet list and add this bullet immediately after the line that begins `- 检测输出中的 `[SEND:/path]` 标记...`:
```
- 将 SSE 的 `delta.reasoning` 流切分为 `ReasoningChunk`，与 `content`（`TextChunk`）按到达顺序交错产出（类型切换时先 flush 旧类型）；`[SEND:...]` 仅扫描 content
```

- [ ] **Step 3: Note terminal reasoning rendering**

In `src/psi_agent/channel/AGENTS.md`, under `## 终端输出约定`, change the line:
```
- 思考过程（reasoning）：`console.print(..., style="dim")`
```
to:
```
- 思考过程（reasoning）：`ChannelCore` 产出 `ReasoningChunk`，CLI/REPL 以 `console.print(..., end="", style="dim")` inline 渲染（Telegram/Feishu 忽略）
```

- [ ] **Step 4: Run the full quality gate**

Run: `uv run ruff check .`
Expected: PASS

Run: `uv run ruff format --check .`
Expected: PASS (if it reports files needing format, run `uv run ruff format .`, then re-run the check and include reformatted files in the commit)

Run: `uv run ty check`
Expected: PASS

Run: `uv run pytest -m "not schedule" -v`
Expected: PASS (full suite minus the long schedule tests)

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/channel/AGENTS.md
git commit -m "docs(channel): document ReasoningChunk in AGENTS.md"
```

---

## Self-Review

**Spec coverage check (against `docs/superpowers/specs/2026-06-28-channel-reasoning-chunk.md`):**
- §3 `ReasoningChunk` type + union → Task 1 ✓
- §4 type-aware single active buffer (switch/timer/end flush) → Task 2 Step 3 ✓
- §4.3 `[SEND]` content-only → Task 2 Step 3 (`if incoming_kind == "text"` guards scan) + `test_post_send_marker_ignored_in_reasoning` ✓
- §5 CLI/REPL dim inline rendering → Task 3 ✓
- §6 Telegram/Feishu unchanged → no task touches them (intentional) ✓
- §8.1 type tests → Task 1 Step 1 ✓
- §8.2 core tests (reasoning-only, switch order, interval merge, SEND-in-reasoning) → Task 2 Step 1 ✓
- §8.3 regression → Task 2 Step 5 + Task 3 Step 4 + Task 4 Step 4 ✓
- §9 docs → Task 4 ✓

**Placeholder scan:** No TBD/TODO; every code step contains complete code and exact commands. ✓

**Type consistency:** `ReasoningChunk(text=...)` used consistently across `_types.py`, `_core.py`, `cli/client.py`, `repl/client.py`, and tests. `kind` is `str | None` with values `"reasoning"`/`"text"`. `Chunk` union extended in one place. ✓

**Backward-compat note:** Task 2's `post()` preserves the original content path exactly (`full_content`/`scan_ptr`/`emitted`/`orig_len + match.end()` unchanged), so all pre-existing `test__core.py` cases remain green — verified by Task 2 Step 5.
