from __future__ import annotations

import textwrap
from pathlib import Path

import anyio
import pytest

from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry

# ── FileEntry ─────────────────────────────────────────────────────────────────


def test_file_entry_defaults() -> None:
    entry = FileEntry(file_hash="abc", tools={}, funcs={})
    assert entry.file_hash == "abc"
    assert entry.tools == {}
    assert entry.funcs == {}
    assert entry.fresh is False


def test_file_entry_fresh_flag() -> None:
    entry = FileEntry(file_hash="abc", tools={}, funcs={}, fresh=True)
    assert entry.fresh is True


# ── ToolFunction.from_callable ────────────────────────────────────────────────


def test_from_callable_basic() -> None:
    async def echo(message: str) -> str:
        return message

    tf = ToolFunction.from_callable(echo)
    assert tf.name == "echo"
    assert tf.parameters["type"] == "object"
    assert "message" in tf.parameters["properties"]
    assert tf.parameters["properties"]["message"]["type"] == "string"
    assert "message" in tf.parameters["required"]


def test_from_callable_with_docstring() -> None:
    async def calc(a: int, b: int) -> int:
        """Add two numbers.

        Args:
            a: First number.
            b: Second number.
        """
        return a + b

    tf = ToolFunction.from_callable(calc)
    assert tf.description == "Add two numbers."
    assert tf.parameters["properties"]["a"]["description"] == "First number."
    assert tf.parameters["properties"]["b"]["description"] == "Second number."
    assert tf.parameters["properties"]["a"]["type"] == "integer"
    assert tf.parameters["required"] == ["a", "b"]


def test_from_callable_optional_param() -> None:
    async def query(city: str, units: str | None = None) -> str:
        return city

    tf = ToolFunction.from_callable(query)
    assert "city" in tf.parameters["required"]
    assert "units" not in tf.parameters["required"]


def test_from_callable_default_param() -> None:
    async def greet(name: str = "World") -> str:
        return f"Hello {name}"

    tf = ToolFunction.from_callable(greet)
    assert "name" not in tf.parameters["required"]


def test_from_callable_list_type() -> None:
    async def process(items: list[str]) -> str:
        return str(items)

    tf = ToolFunction.from_callable(process)
    prop = tf.parameters["properties"]["items"]
    assert prop["type"] == "array"
    assert prop["items"]["type"] == "string"


def test_from_callable_bool_float_types() -> None:
    async def check(flag: bool, score: float) -> str:
        return f"{flag} {score}"

    tf = ToolFunction.from_callable(check)
    assert tf.parameters["properties"]["flag"]["type"] == "boolean"
    assert tf.parameters["properties"]["score"]["type"] == "number"


def test_from_callable_variadic_rejected() -> None:
    async def bad(*args: str) -> str:
        return ""

    with pytest.raises(TypeError, match="Variadic"):
        ToolFunction.from_callable(bad)


def test_from_callable_unsupported_union_rejected() -> None:
    async def bad(x: int | str) -> str:
        return ""

    with pytest.raises(TypeError, match="Unsupported union"):
        ToolFunction.from_callable(bad)


# ── ToolRegistry empty / properties ───────────────────────────────────────────


def test_empty_registry_tools_property() -> None:
    tr = ToolRegistry()
    assert tr.tools == {}
    assert tr.get("nonexistent") is None


def test_registry_with_files() -> None:
    tf = ToolFunction(name="test", description="", parameters={})
    entry = FileEntry(file_hash="abc", tools={"test": tf}, funcs={"test": lambda: "x"})
    tr = ToolRegistry(files={"/tmp/t.py": entry})
    assert tr.tools == {"test": tf}
    assert tr.get("test") is not None
    assert tr.get("nonexistent") is None


# ── ToolRegistry.load ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_load_empty_dir(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    tr = await ToolRegistry.load(tools_dir)
    assert tr.tools == {}
    assert tr._work_dir == tools_dir


@pytest.mark.anyio
async def test_load_missing_dir(tmp_path: Path) -> None:
    tr = await ToolRegistry.load(tmp_path / "nonexistent")
    assert tr.tools == {}
    assert tr._work_dir == tmp_path / "nonexistent"


@pytest.mark.anyio
async def test_load_single_tool(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "echo.py").write_text(
        textwrap.dedent("""\
        async def echo(message: str) -> str:
            \"\"\"Echo a message.

            Args:
                message: The message to echo.
            \"\"\"
            return message
    """),
        encoding="utf-8",
    )
    tr = await ToolRegistry.load(tools_dir)
    assert set(tr.tools) == {"echo"}
    assert tr.tools["echo"].name == "echo"
    assert tr.get("echo") is not None


@pytest.mark.anyio
async def test_load_skips_underscore_files(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "_internal.py").write_text(
        "async def hidden() -> str:\n    return 'hidden'\n", encoding="utf-8"
    )
    tr = await ToolRegistry.load(tools_dir)
    assert tr.tools == {}


@pytest.mark.anyio
async def test_load_skips_non_async(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "misc.py").write_text(
        textwrap.dedent("""\
        def sync_func() -> str:
            return "sync"

        async def async_tool(x: int) -> str:
            return str(x)
    """),
        encoding="utf-8",
    )
    tr = await ToolRegistry.load(tools_dir)
    assert set(tr.tools) == {"async_tool"}


# ── _load_from_dir skip logic ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_load_from_dir_skip_unchanged(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "a.py").write_text("async def foo() -> str:\n    return 'foo'\n", encoding="utf-8")

    tr = await ToolRegistry.load(tools_dir)
    old_files = tr._files

    result = await ToolRegistry._load_from_dir(tools_dir, "test", old_files)
    assert len(result) == 1
    entry = next(iter(result.values()))
    assert entry.fresh is False
    assert entry.tools["foo"].name == "foo"


@pytest.mark.anyio
async def test_load_from_dir_imports_changed(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "a.py").write_text("async def foo() -> str:\n    return 'foo'\n", encoding="utf-8")

    tr = await ToolRegistry.load(tools_dir)
    old_files = tr._files

    await anyio.Path(tools_dir / "a.py").write_text(
        "async def foo() -> str:\n    return 'modified'\n", encoding="utf-8"
    )

    result = await ToolRegistry._load_from_dir(tools_dir, "test", old_files)
    entry = next(iter(result.values()))
    assert entry.fresh is True


# ── ToolRegistry.refresh ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_refresh_no_work_dir() -> None:
    tr = ToolRegistry()
    assert await tr.refresh() == {}


@pytest.mark.anyio
async def test_refresh_adds_new_file(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    tr = await ToolRegistry.load(tools_dir)
    assert tr.tools == {}

    await anyio.Path(tools_dir / "new.py").write_text("async def bar() -> str:\n    return 'bar'\n", encoding="utf-8")
    result = await tr.refresh()
    assert result == {"bar": "added"}
    assert set(tr.tools) == {"bar"}


@pytest.mark.anyio
async def test_refresh_updates_modified_file(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "a.py").write_text("async def foo() -> str:\n    return 'v1'\n", encoding="utf-8")
    tr = await ToolRegistry.load(tools_dir)

    await anyio.Path(tools_dir / "a.py").write_text(
        "async def foo(x: int) -> str:\n    return str(x)\n", encoding="utf-8"
    )
    result = await tr.refresh()
    assert result == {"foo": "updated"}


@pytest.mark.anyio
async def test_refresh_removes_deleted_file(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "a.py").write_text("async def foo() -> str:\n    return 'foo'\n", encoding="utf-8")
    tr = await ToolRegistry.load(tools_dir)
    assert set(tr.tools) == {"foo"}

    await anyio.Path(tools_dir / "a.py").unlink()
    result = await tr.refresh()
    assert result == {"foo": "removed"}
    assert tr.tools == {}
    assert tr.get("foo") is None


@pytest.mark.anyio
async def test_refresh_skips_unchanged_file(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "a.py").write_text("async def foo() -> str:\n    return 'foo'\n", encoding="utf-8")
    tr = await ToolRegistry.load(tools_dir)

    result = await tr.refresh()
    assert result == {"foo": "skipped"}
    assert set(tr.tools) == {"foo"}


@pytest.mark.anyio
async def test_refresh_adds_and_removes_tool_within_file(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "a.py").write_text(
        textwrap.dedent("""\
        async def foo() -> str:
            return 'foo'
        async def bar() -> str:
            return 'bar'
    """),
        encoding="utf-8",
    )
    tr = await ToolRegistry.load(tools_dir)
    assert set(tr.tools) == {"foo", "bar"}

    await anyio.Path(tools_dir / "a.py").write_text(
        textwrap.dedent("""\
        async def bar() -> str:
            return 'bar'
        async def baz() -> str:
            return 'baz'
    """),
        encoding="utf-8",
    )
    result = await tr.refresh()
    assert result == {"foo": "removed", "bar": "updated", "baz": "added"}
    assert set(tr.tools) == {"bar", "baz"}


@pytest.mark.anyio
async def test_refresh_mixed_changes(tmp_path: Path) -> None:
    """Add, modify, delete, and skip all in one refresh."""
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "keep.py").write_text(
        "async def kept() -> str:\n    return 'kept'\n", encoding="utf-8"
    )
    await anyio.Path(tools_dir / "modify.py").write_text("async def mod() -> str:\n    return 'v1'\n", encoding="utf-8")
    await anyio.Path(tools_dir / "delete.py").write_text(
        "async def gone() -> str:\n    return 'gone'\n", encoding="utf-8"
    )
    tr = await ToolRegistry.load(tools_dir)

    await anyio.Path(tools_dir / "modify.py").write_text(
        "async def mod(x: int) -> str:\n    return str(x)\n", encoding="utf-8"
    )
    await anyio.Path(tools_dir / "delete.py").unlink()
    await anyio.Path(tools_dir / "new.py").write_text(
        "async def fresh() -> str:\n    return 'fresh'\n", encoding="utf-8"
    )

    result = await tr.refresh()
    assert result["kept"] == "skipped"
    assert result["mod"] == "updated"
    assert result["gone"] == "removed"
    assert result["fresh"] == "added"
    assert set(tr.tools) == {"kept", "mod", "fresh"}


# ── ToolRegistry.get ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_last_file_wins(tmp_path: Path) -> None:
    """get() searches files in insertion order, returns first match."""
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "a.py").write_text("async def echo() -> str:\n    return 'a'\n", encoding="utf-8")
    await anyio.Path(tools_dir / "b.py").write_text("async def echo() -> str:\n    return 'b'\n", encoding="utf-8")
    tr = await ToolRegistry.load(tools_dir)
    func = tr.get("echo")
    assert func is not None
    assert await func() in ("a", "b")  # glob order is filesystem-dependent
