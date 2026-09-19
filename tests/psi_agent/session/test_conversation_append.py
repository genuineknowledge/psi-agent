"""Append-only JSONL persistence: only the new lines hit the disk.

The whole point of the append path is the *number of bytes written*, so these
tests measure that directly (``WriteCounter``) rather than only checking that
the resulting file has the right content — a full rewrite also produces the
right content, so a content-only assertion cannot tell the two apart.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from psi_agent.session.conversation import Conversation


class WriteCounter:
    """Tally bytes handed to the filesystem underneath ``anyio.Path``.

    ``anyio.Path`` delegates to the *pathlib* methods in a worker thread
    (``write_text``, ``open``), so patching those two class attributes catches
    every route the implementation can take to disk — the counter stays honest
    whether ``save()`` uses ``write_text`` for a full rewrite or an appending
    handle for the incremental path.
    """

    def __init__(self) -> None:
        self.written = 0
        self.opened: list[tuple[str, str]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        counter = self
        real_write_text = Path.write_text
        real_write_bytes = Path.write_bytes
        real_open = Path.open

        def write_text(self: Path, data: str, encoding: str | None = None, *a: Any, **kw: Any) -> int:
            counter.written += len(data.encode(encoding or "utf-8"))
            counter.opened.append((str(self), "w"))
            return real_write_text(self, data, encoding, *a, **kw)

        def write_bytes(self: Path, data: Any) -> int:
            counter.written += len(data)
            counter.opened.append((str(self), "w"))
            return real_write_bytes(self, data)

        def opener(self: Path, mode: str = "r", *a: Any, **kw: Any) -> Any:
            handle = real_open(self, mode, *a, **kw)
            if "r" in mode and "+" not in mode:
                return handle
            counter.opened.append((str(self), mode))
            return _CountingHandle(handle, counter)

        monkeypatch.setattr(Path, "write_text", write_text)
        monkeypatch.setattr(Path, "write_bytes", write_bytes)
        monkeypatch.setattr(Path, "open", opener)

    def reset(self) -> None:
        self.written = 0
        self.opened.clear()


class _CountingHandle:
    def __init__(self, handle: Any, counter: WriteCounter) -> None:
        self._handle = handle
        self._counter = counter

    def write(self, data: Any) -> int:
        self._counter.written += len(data.encode("utf-8")) if isinstance(data, str) else len(data)
        return self._handle.write(data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)

    def __enter__(self) -> _CountingHandle:
        self._handle.__enter__()
        return self

    def __exit__(self, *exc: Any) -> Any:
        return self._handle.__exit__(*exc)


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tmp_files(directory: Path) -> list[Path]:
    """Leftover ``.jsonl.tmp`` files, listed from a sync helper.

    A blocking ``pathlib`` call in an async body is ASYNC240, and the repo
    keeps those out of coroutines rather than silencing the rule.
    """
    return list(directory.glob("*.tmp"))


@pytest.fixture
def counter(monkeypatch: pytest.MonkeyPatch) -> WriteCounter:
    c = WriteCounter()
    c.install(monkeypatch)
    return c


# -- the core claim: a commit costs only the new lines ---------------------------


@pytest.mark.anyio
async def test_append_writes_only_the_new_line_not_the_whole_history(tmp_path: Path, counter: WriteCounter) -> None:
    """A second commit must not re-write the first (large) message.

    This is the assertion that goes red if ``save()`` reverts to a full
    rewrite: the history is ~200KB, the new line is ~40 bytes.
    """
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "user", "content": "x" * 200_000})
    await conv.commit()

    counter.reset()
    conv.add({"role": "assistant", "content": "ok"})
    await conv.commit()

    new_line = json.dumps(conv.messages[1], ensure_ascii=False) + "\n"
    assert counter.written == len(new_line.encode("utf-8"))
    assert counter.written < 1_000, f"wrote {counter.written} bytes — history was rewritten"
    assert read_lines(path) == conv.messages


@pytest.mark.anyio
async def test_append_does_not_go_through_a_tmp_file(tmp_path: Path, counter: WriteCounter) -> None:
    """Independent corroboration: the full-rewrite path is the only one that
    stages through ``.jsonl.tmp``, so an append must touch no tmp path."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "user", "content": "a"})
    await conv.commit()

    counter.reset()
    conv.add({"role": "user", "content": "b"})
    await conv.commit()

    assert not [p for p, _ in counter.opened if p.endswith(".tmp")]
    assert tmp_files(tmp_path) == []


@pytest.mark.anyio
async def test_many_commits_cost_stays_flat_as_history_grows(tmp_path: Path, counter: WriteCounter) -> None:
    """Per-commit bytes must not scale with history length."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    for i in range(20):
        conv.add({"role": "user", "content": f"msg {i} " + "y" * 5_000})
        await conv.commit()

    counter.reset()
    conv.add({"role": "user", "content": "last " + "y" * 5_000})
    await conv.commit()

    assert counter.written < 6_000, f"wrote {counter.written} bytes for one 5KB message"
    assert len(read_lines(path)) == 21


@pytest.mark.anyio
async def test_multiple_new_messages_in_one_commit_all_land(tmp_path: Path, counter: WriteCounter) -> None:
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "user", "content": "first"})
    await conv.commit()

    counter.reset()
    conv.add({"role": "assistant", "content": "second"})
    conv.add({"role": "tool", "content": "third"})
    await conv.commit()

    assert read_lines(path) == conv.messages
    assert counter.written < 200


# -- rewrite paths must still be correct ----------------------------------------


@pytest.mark.anyio
async def test_replace_system_rewrites_the_file(tmp_path: Path) -> None:
    """``replace_system`` edits line 0 — appending would leave the stale prompt."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "system", "content": "old prompt"})
    conv.add({"role": "user", "content": "hi"})
    await conv.commit()

    conv.replace_system("new prompt")
    await conv.commit()

    on_disk = read_lines(path)
    assert on_disk == conv.messages
    assert on_disk[0]["content"] == "new prompt"
    assert len(on_disk) == 2


@pytest.mark.anyio
async def test_trim_after_rewrites_the_file(tmp_path: Path) -> None:
    """``trim_after`` shortens history — the dropped lines must leave the file."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    for text in ("system", "u1", "a1", "u2"):
        conv.add({"role": "user", "content": text})
    await conv.commit()

    conv.trim_after(0)
    await conv.commit()

    assert read_lines(path) == conv.messages
    assert len(read_lines(path)) == 1


@pytest.mark.anyio
async def test_append_resumes_after_a_rewrite(tmp_path: Path, counter: WriteCounter) -> None:
    """A rewrite must re-establish the append baseline, not disable it."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "system", "content": "old"})
    conv.add({"role": "user", "content": "z" * 100_000})
    await conv.commit()
    conv.replace_system("new")
    await conv.commit()

    counter.reset()
    conv.add({"role": "assistant", "content": "after"})
    await conv.commit()

    assert counter.written < 1_000, f"wrote {counter.written} bytes — baseline was not restored"
    assert read_lines(path) == conv.messages


@pytest.mark.anyio
async def test_rollback_then_commit_leaves_disk_matching_memory(tmp_path: Path) -> None:
    """Rollback discards uncommitted messages; disk must still match memory.

    In the current flow a snapshot is always taken at a commit boundary, so a
    rollback restores exactly what is on disk and the following commit has
    nothing to write.  The assertion is the invariant, not a byte count.
    """
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "user", "content": "kept"})
    await conv.commit()
    conv.add({"role": "assistant", "content": "doomed"})
    await conv.commit()

    conv.add({"role": "user", "content": "uncommitted"})
    conv.rollback()
    await conv.commit()

    assert read_lines(path) == conv.messages
    assert len(read_lines(path)) == 2


@pytest.mark.anyio
async def test_trim_then_add_in_one_turn_rewrites(tmp_path: Path) -> None:
    """Trim one message and add another before committing: history is back to
    its original *length*, so a length comparison sees nothing wrong.  Only
    tracking that a prefix was edited catches this — otherwise the dropped
    message stays on disk and the new one lands after it (3 lines, not 2)."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "user", "content": "first"})
    conv.add({"role": "assistant", "content": "dropped"})
    await conv.commit()

    conv.trim_after(0)
    conv.add({"role": "assistant", "content": "replacement"})
    await conv.commit()

    on_disk = read_lines(path)
    assert on_disk == conv.messages
    assert len(on_disk) == 2
    assert [m["content"] for m in on_disk] == ["first", "replacement"]


@pytest.mark.anyio
async def test_replace_system_then_add_in_one_turn_rewrites(tmp_path: Path) -> None:
    """Same trap for ``replace_system``: history only grew by one, and the file
    is exactly the size we left it at, so both cheap checks pass.  Appending
    would keep the stale prompt on line 0."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "system", "content": "old prompt"})
    await conv.commit()

    conv.replace_system("new prompt")
    conv.add({"role": "user", "content": "hi"})
    await conv.commit()

    on_disk = read_lines(path)
    assert on_disk == conv.messages
    assert on_disk[0]["content"] == "new prompt"
    assert len(on_disk) == 2


@pytest.mark.anyio
async def test_compaction_message_is_appended(tmp_path: Path, counter: WriteCounter) -> None:
    """Compaction adds a ``compacted`` row (trimming is deferred to
    ``project_history_for_wire``), so it stays on the cheap path."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "user", "content": "q" * 50_000})
    await conv.commit()

    counter.reset()
    conv.add({"role": "compacted", "content": "summary", "kind": "compacted"})
    await conv.commit()

    assert counter.written < 1_000
    assert read_lines(path)[-1]["role"] == "compacted"
    assert read_lines(path) == conv.messages


# -- crash in the middle of a line ----------------------------------------------


@pytest.mark.anyio
async def test_load_skips_a_half_written_trailing_line(tmp_path: Path) -> None:
    """A crash mid-append leaves a truncated final line; load must return the
    intact prefix instead of raising."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "user", "content": "one"})
    conv.add({"role": "user", "content": "two"})
    await conv.commit()

    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"role": "user", "cont')

    loaded = await Conversation._load(path)
    assert [{k: v for k, v in m.items() if k != "created_at"} for m in loaded] == [
        {"role": "user", "content": "one"},
        {"role": "user", "content": "two"},
    ]


@pytest.mark.anyio
async def test_next_save_heals_a_half_written_line(tmp_path: Path) -> None:
    """After recovering from a torn line the file must not keep the garbage:
    the next commit rewrites it in full, so the corruption cannot outlive one
    turn (an append would strand the partial line mid-file forever)."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "user", "content": "one"})
    await conv.commit()
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"role": "user", "TORN_SENTINEL')

    messages, baseline = await Conversation._load_with_baseline(path)
    assert baseline is None, "a torn file must not hand out an append baseline"
    recovered = Conversation(messages=messages, path=path, persisted_bytes=baseline)
    recovered.add({"role": "assistant", "content": "after crash"})
    await recovered.commit()

    text = path.read_text(encoding="utf-8")
    assert "TORN_SENTINEL" not in text, "torn line survived the next commit"
    assert read_lines(path) == recovered.messages


@pytest.mark.anyio
async def test_external_truncation_is_detected_and_rewritten(tmp_path: Path) -> None:
    """If the file changed behind our back (foreign writer, truncation), the
    byte-offset baseline is stale — appending would corrupt it."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "user", "content": "one"})
    conv.add({"role": "user", "content": "two"})
    await conv.commit()

    path.write_text("", encoding="utf-8")

    conv.add({"role": "user", "content": "three"})
    await conv.commit()

    assert read_lines(path) == conv.messages
    assert len(read_lines(path)) == 3


@pytest.mark.anyio
async def test_missing_file_is_recreated(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "user", "content": "one"})
    await conv.commit()

    path.unlink()

    conv.add({"role": "user", "content": "two"})
    await conv.commit()

    assert read_lines(path) == conv.messages


# -- round trip -----------------------------------------------------------------


@pytest.mark.anyio
async def test_save_then_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "system", "content": "sys"})
    await conv.commit()
    conv.add({"role": "user", "content": '中文 with "quotes" and \\ backslash'})
    await conv.commit()
    conv.add({"role": "assistant", "content": "multi\nline\tvalue", "tool_calls": [{"id": "1"}]})
    await conv.commit()

    assert await Conversation._load(path) == conv.messages


@pytest.mark.anyio
async def test_reopened_conversation_appends_without_duplicating(tmp_path: Path, counter: WriteCounter) -> None:
    """The restart case: a fresh ``Conversation`` over an existing file must
    append after the loaded messages, not duplicate or rewrite them."""
    path = tmp_path / "s.jsonl"
    first = Conversation(path=path)
    first.add({"role": "user", "content": "w" * 100_000})
    first.add({"role": "assistant", "content": "reply"})
    await first.commit()

    messages, baseline = await Conversation._load_with_baseline(path)
    reopened = Conversation(messages=messages, path=path, persisted_bytes=baseline)
    counter.reset()
    reopened.add({"role": "user", "content": "next"})
    await reopened.commit()

    assert read_lines(path) == reopened.messages
    assert len(read_lines(path)) == 3
    assert counter.written < 1_000, f"wrote {counter.written} bytes — reloaded history was rewritten"


@pytest.mark.anyio
async def test_unicode_byte_offsets_stay_aligned(tmp_path: Path, counter: WriteCounter) -> None:
    """CJK content makes character count and byte count differ; the append
    offset bookkeeping must be in bytes."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "user", "content": "你好世界" * 500})
    await conv.commit()
    conv.add({"role": "assistant", "content": "回复内容"})
    await conv.commit()
    conv.add({"role": "user", "content": "第三条"})
    await conv.commit()

    assert read_lines(path) == conv.messages
    assert await Conversation._load(path) == conv.messages


@pytest.mark.anyio
async def test_legacy_dual_read_does_not_append_to_the_new_file(tmp_path: Path) -> None:
    """Read path (legacy workspace) != write path (AppData): the write target
    does not exist yet, so the loaded history must be written in full."""
    appdata = tmp_path / "appdata"
    workspace = tmp_path / "ws"
    (workspace / "histories").mkdir(parents=True)
    legacy = workspace / "histories" / "sess.jsonl"
    legacy.write_text(
        json.dumps({"role": "user", "content": "from legacy"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    conv = await Conversation.from_workspace(workspace, "sess", appdata_root=str(appdata))
    conv.add({"role": "assistant", "content": "new"})
    await conv.commit()

    written = appdata / "histories" / "sess.jsonl"
    assert read_lines(written) == conv.messages
    assert len(read_lines(written)) == 2, "legacy history was lost"


@pytest.mark.anyio
async def test_resumed_appdata_session_appends(tmp_path: Path, counter: WriteCounter) -> None:
    """The production restart path end to end: same file for read and write,
    so ``from_workspace`` must hand back an appendable conversation."""
    appdata = tmp_path / "appdata"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    first = await Conversation.from_workspace(workspace, "sess", appdata_root=str(appdata))
    first.add({"role": "user", "content": "v" * 100_000})
    await first.commit()

    resumed = await Conversation.from_workspace(workspace, "sess", appdata_root=str(appdata))
    counter.reset()
    resumed.add({"role": "assistant", "content": "cheap"})
    await resumed.commit()

    assert counter.written < 1_000, f"wrote {counter.written} bytes on a resumed session"
    assert len(read_lines(appdata / "histories" / "sess.jsonl")) == 2


@pytest.mark.anyio
async def test_second_writer_on_the_same_file_is_detected(tmp_path: Path) -> None:
    """Two Conversations over one file (two processes, or a session opened
    twice): the size check must catch the foreign write instead of appending
    into the middle of somebody else's tail."""
    path = tmp_path / "s.jsonl"
    a = Conversation(path=path)
    a.add({"role": "user", "content": "a1"})
    await a.commit()

    b = Conversation(messages=[*a.messages, {"role": "user", "content": "b1"}], path=path)
    await b.commit()

    a.add({"role": "user", "content": "a2"})
    await a.commit()

    # ``a`` cannot know about b1, but it must not corrupt the file: the result
    # is a valid JSONL that matches one of the two writers' views.
    on_disk = read_lines(path)
    assert on_disk == a.messages
    assert all("role" in m for m in on_disk)


@pytest.mark.anyio
async def test_failed_append_recovers_on_the_next_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial append (disk full, I/O error) leaves the file in a state the
    recorded size no longer describes.  ``save`` swallows the error, so the
    *next* commit must rewrite rather than append onto that unknown tail."""
    path = tmp_path / "s.jsonl"
    conv = Conversation(path=path)
    conv.add({"role": "user", "content": "one"})
    await conv.commit()

    real_open = Path.open
    broken = {"on": True}

    def flaky_open(self: Path, mode: str = "r", *a: Any, **kw: Any) -> Any:
        handle = real_open(self, mode, *a, **kw)
        if "a" in mode and broken["on"]:
            # Write half the payload, then fail — the torn-write shape.
            class Torn:
                def write(self, data: Any) -> int:
                    handle.write(data[: len(data) // 2])
                    raise OSError("No space left on device")

                def __getattr__(self, name: str) -> Any:
                    return getattr(handle, name)

                def __enter__(self) -> Any:
                    handle.__enter__()
                    return self

                def __exit__(self, *exc: Any) -> Any:
                    return handle.__exit__(*exc)

            return Torn()
        return handle

    monkeypatch.setattr(Path, "open", flaky_open)
    conv.add({"role": "assistant", "content": "lost to the failed write"})
    await conv.commit()

    broken["on"] = False
    conv.add({"role": "user", "content": "after recovery"})
    await conv.commit()

    assert read_lines(path) == conv.messages
    assert len(read_lines(path)) == 3


@pytest.mark.anyio
async def test_invalid_utf8_still_raises(tmp_path: Path) -> None:
    """Load reads bytes now (the baseline check needs them) but must decode as
    strictly as the previous ``read_text`` did.  Silently replacing a corrupt
    byte would drop the line and the healing rewrite would make that loss
    permanent — on a data path, raising is the conservative answer."""
    path = tmp_path / "s.jsonl"
    path.write_bytes(b'{"role": "user", "content": "\xff\xfe bad"}\n')

    with pytest.raises(UnicodeDecodeError):
        await Conversation._load(path)

    assert path.read_bytes().endswith(b'bad"}\n'), "the corrupt file must be left alone"


@pytest.mark.anyio
async def test_no_path_is_a_noop(tmp_path: Path) -> None:
    conv = Conversation()
    conv.add({"role": "user", "content": "hi"})
    await conv.commit()
    assert [{k: v for k, v in m.items() if k != "created_at"} for m in conv.messages] == [
        {"role": "user", "content": "hi"}
    ]
