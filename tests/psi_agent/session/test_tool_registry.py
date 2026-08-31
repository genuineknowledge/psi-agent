from __future__ import annotations

import importlib
import os
import sys
import textwrap
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
import pytest

from psi_agent.session.tool_registry import (
    _SYS_PATH_DEPTH,
    FileEntry,
    ToolFunction,
    ToolRegistry,
    _tools_dir_on_sys_path,
)

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


def test_from_callable_optional_param_without_default_remains_required() -> None:
    async def query(value: str | None) -> str:
        return str(value)

    tf = ToolFunction.from_callable(query)

    assert tf.parameters["required"] == ["value"]


def test_from_callable_default_param() -> None:
    async def greet(name: str = "World") -> str:
        return f"Hello {name}"

    tf = ToolFunction.from_callable(greet)
    assert "name" not in tf.parameters["required"]


def test_from_callable_exposes_annotated_constraints_literal_default_and_closed_object() -> None:
    async def query(
        text: Annotated[str, {"minLength": 1, "maxLength": 8000}],
        mode: Literal["low", "medium", "high"] = "medium",
    ) -> str:
        return text

    tf = ToolFunction.from_callable(query)

    assert tf.parameters == {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 8000,
                "description": "",
            },
            "mode": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "default": "medium",
                "description": "",
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    }


def test_from_callable_exposes_constraints_for_optional_annotated_type() -> None:
    async def query(value: Annotated[str, {"minLength": 1}] | None = None) -> str:
        return str(value)

    tf = ToolFunction.from_callable(query)

    assert tf.parameters["properties"]["value"] == {
        "type": "string",
        "minLength": 1,
        "default": None,
        "description": "",
    }
    assert tf.parameters["required"] == []


def test_from_callable_exposes_constraints_around_optional_type() -> None:
    async def query(value: Annotated[str | None, {"maxLength": 3}] = None) -> str:
        return str(value)

    tf = ToolFunction.from_callable(query)

    assert tf.parameters["properties"]["value"] == {
        "type": "string",
        "maxLength": 3,
        "default": None,
        "description": "",
    }


@pytest.mark.parametrize(
    ("annotation", "metadata"),
    [
        (int, {"minLength": 1}),
        (float, {"maxLength": 2}),
        (bool, {"pattern": "true"}),
        (str, {"minimum": 0}),
        (list[str], {"maximum": 1}),
    ],
)
def test_from_callable_rejects_constraints_for_inapplicable_types(annotation: Any, metadata: dict[str, object]) -> None:
    async def tool(value: str) -> str:
        return str(value)

    tool.__annotations__["value"] = Annotated[annotation, metadata]

    with pytest.raises(TypeError, match="not supported for schema type"):
        ToolFunction.from_callable(tool)


@pytest.mark.parametrize(
    ("annotation", "metadata"),
    [
        (str, {"minLength": -1}),
        (str, {"maxLength": 1.5}),
        (str, {"minLength": True}),
        (str, {"pattern": 1}),
        (int, {"minimum": 1.5}),
        (int, {"maximum": True}),
        (float, {"minimum": float("nan")}),
        (float, {"maximum": float("inf")}),
    ],
)
def test_from_callable_rejects_invalid_constraint_values(annotation: Any, metadata: dict[str, object]) -> None:
    async def tool(value: str) -> str:
        return str(value)

    tool.__annotations__["value"] = Annotated[annotation, metadata]

    with pytest.raises(TypeError, match="Invalid JSON Schema constraint"):
        ToolFunction.from_callable(tool)


@pytest.mark.parametrize(
    ("annotation", "metadata"),
    [
        (str, {"minLength": 3, "maxLength": 2}),
        (int, {"minimum": 3, "maximum": 2}),
        (float, {"minimum": 3.0, "maximum": 2}),
    ],
)
def test_from_callable_rejects_inverted_constraint_ranges(annotation: Any, metadata: dict[str, object]) -> None:
    async def tool(value: str) -> str:
        return str(value)

    tool.__annotations__["value"] = Annotated[annotation, metadata]

    with pytest.raises(TypeError, match="must not exceed"):
        ToolFunction.from_callable(tool)


def test_from_callable_accepts_arbitrarily_large_integer_constraints() -> None:
    lower_bound = 10**1000

    async def tool(value: int) -> str:
        return str(value)

    tool.__annotations__["value"] = Annotated[int, {"minimum": lower_bound}]
    tf = ToolFunction.from_callable(tool)

    assert tf.parameters["properties"]["value"]["minimum"] == lower_bound


@pytest.mark.parametrize(
    "metadata",
    [
        {"type": "integer"},
        {"enum": ["x"]},
        {"default": "x"},
        {"description": "replacement"},
        {"minItems": 1},
    ],
)
def test_from_callable_rejects_metadata_outside_constraint_allowlist(metadata: dict[str, object]) -> None:
    async def tool(value: str) -> str:
        return value

    tool.__annotations__["value"] = Annotated[str, metadata]

    with pytest.raises(TypeError, match="Unsupported JSON Schema constraints"):
        ToolFunction.from_callable(tool)


@pytest.mark.parametrize("metadata", ["minimum", object(), ["minimum", 1]])
def test_from_callable_rejects_non_mapping_annotated_metadata(metadata: object) -> None:
    async def tool(value: str) -> str:
        return value

    tool.__annotations__["value"] = Annotated[str, metadata]

    with pytest.raises(TypeError, match="Unsupported Annotated metadata"):
        ToolFunction.from_callable(tool)


def test_from_callable_reports_mixed_unsupported_metadata_keys() -> None:
    async def tool(value: str) -> str:
        return value

    tool.__annotations__["value"] = Annotated[str, {"type": "integer", 1: "invalid"}]

    with pytest.raises(TypeError, match="Unsupported JSON Schema constraints"):
        ToolFunction.from_callable(tool)


@pytest.mark.parametrize(
    "literal",
    [
        Literal[1, True],
        Literal["one", 2],
        eval("Literal[1.0, float('inf')]", {"Literal": Literal}),
        eval("Literal[()]", {"Literal": Literal}),
    ],
)
def test_from_callable_rejects_non_json_safe_or_heterogeneous_literals(literal: object) -> None:
    async def tool(value: str) -> str:
        return str(value)

    tool.__annotations__["value"] = literal

    with pytest.raises(TypeError, match="Unsupported Literal"):
        ToolFunction.from_callable(tool)


@pytest.mark.parametrize(
    ("annotation", "default"),
    [
        (str, object()),
        (str, float("nan")),
        (float, float("inf")),
        (list[float], [1.0, float("-inf")]),
        (list[str], ["ok", object()]),
    ],
)
def test_from_callable_rejects_defaults_that_are_not_strict_json(annotation: object, default: object) -> None:
    async def tool(value: str = "placeholder") -> str:
        return str(value)

    tool.__annotations__["value"] = annotation
    tool.__defaults__ = (default,)

    with pytest.raises(TypeError, match=r"default.*JSON", check=lambda e: "value" in str(e)):
        ToolFunction.from_callable(tool)


@pytest.mark.parametrize(
    ("annotation", "default"),
    [
        (str, 1),
        (int, True),
        (int, 1.0),
        (float, True),
        (bool, 1),
        (list[int], [1, True]),
        (list[str], {"item": "value"}),
        (Literal["low", "high"], "medium"),
        (str | None, 1),
        (str, None),
    ],
)
def test_from_callable_rejects_defaults_that_do_not_match_schema(annotation: Any, default: object) -> None:
    async def tool(value: str = "placeholder") -> str:
        return str(value)

    tool.__annotations__["value"] = annotation
    tool.__defaults__ = (default,)

    with pytest.raises(TypeError, match=r"default.*does not conform", check=lambda e: "value" in str(e)):
        ToolFunction.from_callable(tool)


@pytest.mark.parametrize(
    ("annotation", "metadata", "default"),
    [
        (str, {"minLength": 2}, "x"),
        (str, {"maxLength": 2}, "xxx"),
        (str, {"pattern": "^[a-z]+$"}, "123"),
        (int, {"minimum": 2}, 1),
        (int, {"maximum": 2}, 3),
        (float, {"minimum": 0.5, "maximum": 1}, 0.25),
    ],
)
def test_from_callable_rejects_defaults_that_violate_constraints(
    annotation: Any, metadata: dict[str, object], default: object
) -> None:
    async def tool(value: str = "placeholder") -> str:
        return str(value)

    tool.__annotations__["value"] = Annotated[annotation, metadata]
    tool.__defaults__ = (default,)

    with pytest.raises(TypeError, match=r"default.*does not conform", check=lambda e: "value" in str(e)):
        ToolFunction.from_callable(tool)


def test_from_callable_accepts_recursive_json_default_and_nullable_default() -> None:
    async def tool(values: list[int], label: str | None = None) -> str:
        return str(values) + str(label)

    tool.__defaults__ = ([1, 2], None)
    tf = ToolFunction.from_callable(tool)

    assert tf.parameters["properties"]["values"]["default"] == [1, 2]
    assert tf.parameters["properties"]["label"]["default"] is None
    assert tf.parameters["required"] == []


def test_from_callable_list_type() -> None:
    async def process(items: list[str]) -> str:
        return str(items)

    tf = ToolFunction.from_callable(process)
    prop = tf.parameters["properties"]["items"]
    assert prop["type"] == "array"
    assert prop["items"]["type"] == "string"


def test_from_callable_list_of_objects_type() -> None:
    async def process(items: list[dict[str, str]] | None = None) -> str:
        return str(items)

    tf = ToolFunction.from_callable(process)

    assert tf.parameters["properties"]["items"] == {
        "type": "array",
        "items": {"type": "object", "additionalProperties": {"type": "string"}},
        "description": "",
        "default": None,
    }


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


# ── helper imports / sys.path ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_tool_can_import_a_sibling_helper(tmp_path: Path) -> None:
    """Bare ``from _helper import x`` resolves because tools/ goes on sys.path.

    Without it, whether this worked depended on glob order and on whether some
    earlier tool file had patched ``sys.path`` as an import side effect — the same
    file would register on one run and raise ModuleNotFoundError on the next.
    """
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "_helper.py").write_text("VALUE = 'from helper'\n", encoding="utf-8")
    await anyio.Path(tools_dir / "uses_helper.py").write_text(
        "from _helper import VALUE\n\n\nasync def read_value() -> str:\n    return VALUE\n",
        encoding="utf-8",
    )

    tr = await ToolRegistry.load(tools_dir)

    assert set(tr.tools) == {"read_value"}
    func = tr.get("read_value")
    assert func is not None
    assert await func() == "from helper"


@pytest.mark.anyio
async def test_sys_path_is_restored_after_loading(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "t.py").write_text("async def t() -> str:\n    return 't'\n", encoding="utf-8")
    before = list(sys.path)

    await ToolRegistry.load(tools_dir)

    assert sys.path == before


@pytest.mark.anyio
async def test_import_failure_is_recorded_with_its_reason(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "broken.py").write_text(
        "import definitely_not_installed\n\n\nasync def never() -> str:\n    return 'x'\n",
        encoding="utf-8",
    )
    await anyio.Path(tools_dir / "fine.py").write_text("async def fine() -> str:\n    return 'ok'\n", encoding="utf-8")

    tr = await ToolRegistry.load(tools_dir)

    assert set(tr.tools) == {"fine"}
    assert "broken.py" in tr.load_failures
    assert "definitely_not_installed" in tr.load_failures["broken.py"]


@pytest.mark.anyio
async def test_overlapping_loads_of_one_dir_do_not_duplicate_the_path_entry() -> None:
    """Overlapping loaders on one dir keep exactly one entry, not two.

    Two Sessions on one agent pack overlap routinely: Gateway starts them together
    and every ``await py_file.read_bytes()`` is a yield point. Inserting
    unconditionally would leave the entry on ``sys.path`` twice for the overlap
    window — a longer path is cost paid on every import lookup, and it makes
    ``sys.path`` misleading to read while debugging.
    """
    entry = "/overlap-test-tools-dir"
    before = list(sys.path)
    counts_while_nested: list[int] = []

    async def outer() -> None:
        async with _tools_dir_on_sys_path(entry):
            await anyio.sleep(0.05)

    async def inner() -> None:
        await anyio.sleep(0.01)  # enter after `outer` has inserted
        async with _tools_dir_on_sys_path(entry):
            counts_while_nested.append(sys.path.count(entry))

    async with anyio.create_task_group() as tg:
        tg.start_soon(outer)
        tg.start_soon(inner)

    assert counts_while_nested == [1], "overlapping loads stacked duplicate sys.path entries"
    assert sys.path == before, "entry outlived both loaders"


@pytest.mark.anyio
async def test_first_loader_to_exit_does_not_strand_a_still_running_one() -> None:
    """The entry survives until the *last* overlapping loader leaves.

    Regression guard for a refcount that was once a boolean: with "remove only what
    I inserted", the loader that inserted could finish first and pull the entry out
    from under a second one still mid-scan, whose bare ``from _helper import ...``
    then raised ModuleNotFoundError. Timing is the reverse of the test above — here
    the *inserter* is short-lived and the follower outlives it.
    """
    entry = "/strand-test-tools-dir"
    before = list(sys.path)
    follower_saw_entry: list[bool] = []

    async def inserter() -> None:
        async with _tools_dir_on_sys_path(entry):
            await anyio.sleep(0.03)

    async def follower() -> None:
        await anyio.sleep(0.01)  # enter while `inserter` holds it
        async with _tools_dir_on_sys_path(entry):
            await anyio.sleep(0.06)  # outlive `inserter`
            follower_saw_entry.append(entry in sys.path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(inserter)
        tg.start_soon(follower)

    assert follower_saw_entry == [True], "an exiting loader stranded one that was still importing"
    assert sys.path == before


@pytest.mark.anyio
async def test_cancelled_load_still_removes_its_path_entry() -> None:
    """Cancellation must not leak the entry for the life of the process.

    Regression guard: an earlier version re-acquired the lock inside ``finally``.
    That ``await`` is a cancellation checkpoint, so a cancelled scan skipped the
    removal entirely and the entry stayed on ``sys.path`` forever. The cleanup must
    stay ``await``-free.
    """
    entry = "/cancelled-load-tools-dir"
    before = list(sys.path)

    async def victim() -> None:
        async with _tools_dir_on_sys_path(entry):
            await anyio.sleep(10)

    with anyio.move_on_after(0.05):
        await victim()

    assert entry not in sys.path, "cancelled load leaked its sys.path entry"
    assert sys.path == before


@pytest.mark.anyio
async def test_editing_a_helper_does_not_take_effect_until_restart(tmp_path: Path) -> None:
    """Documents a known limit: hot reload covers tool files, not their helpers.

    Helpers are cached in ``sys.modules`` under their bare name and nothing evicts
    them, so a re-imported tool file still binds the *old* helper. This test asserts
    the stale behaviour on purpose — if someone later makes helpers reload, this test
    should fail and be rewritten, not deleted.
    """
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    helper = anyio.Path(tools_dir / "_hot.py")
    await helper.write_text("VALUE = 'v1'\n", encoding="utf-8")
    await anyio.Path(tools_dir / "uses_hot.py").write_text(
        "from _hot import VALUE\n\n\nasync def read_hot() -> str:\n    return VALUE\n",
        encoding="utf-8",
    )
    registry = await ToolRegistry.load(tools_dir, "hot")
    func = registry.get("read_hot")
    assert func is not None
    assert await func() == "v1"

    await helper.write_text("VALUE = 'v2'\n", encoding="utf-8")
    await anyio.Path(tools_dir / "uses_hot.py").write_text(
        "from _hot import VALUE\n\n\nasync def read_hot() -> str:\n    return VALUE  # touched\n",
        encoding="utf-8",
    )
    await registry.refresh()

    refreshed = registry.get("read_hot")
    assert refreshed is not None
    assert await refreshed() == "v1", "helper unexpectedly reloaded — update this test and the docs"


@pytest.mark.anyio
async def test_tool_file_named_after_a_stdlib_module_shadows_it(tmp_path: Path) -> None:
    """Documents the sharpest hazard of putting ``tools/`` on ``sys.path``.

    A ``tools/<stdlib name>.py`` wins over the stdlib for any importer in the
    process while the entry is in front, and the result is cached in ``sys.modules``
    so it outlasts the load window. Pinned here so the constraint "do not name a
    tool after a stdlib module" is enforced by a failing test if it ever changes.
    """
    stdlib_name = "secrets"
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / f"{stdlib_name}.py").write_text(
        "SHADOW_MARKER = True\n\n\nasync def shadowing_tool() -> str:\n    return 'shadow'\n",
        encoding="utf-8",
    )
    had_it = sys.modules.pop(stdlib_name, None)
    try:
        entry = str(await anyio.Path(tools_dir).absolute())
        async with _tools_dir_on_sys_path(entry):
            shadowed = importlib.import_module(stdlib_name)
            assert getattr(shadowed, "SHADOW_MARKER", False) is True, "expected the tool file to win"
        # Still poisoned after the window, because sys.modules kept it.
        assert getattr(sys.modules[stdlib_name], "SHADOW_MARKER", False) is True
    finally:
        sys.modules.pop(stdlib_name, None)
        if had_it is not None:
            sys.modules[stdlib_name] = had_it


@pytest.mark.anyio
async def test_load_leaves_a_preexisting_path_entry_alone(tmp_path: Path) -> None:
    """An entry this load did not insert must survive the load, exactly once."""
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "t.py").write_text("async def t() -> str:\n    return 't'\n", encoding="utf-8")
    entry = str(await anyio.Path(tools_dir).absolute())
    sys.path.insert(0, entry)
    try:
        await ToolRegistry.load(tools_dir)
        assert sys.path.count(entry) == 1, "load duplicated or removed a pre-existing entry"
    finally:
        sys.path.remove(entry)


@pytest.mark.anyio
async def test_load_failures_empty_when_everything_imports(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "ok.py").write_text("async def ok() -> str:\n    return 'ok'\n", encoding="utf-8")

    tr = await ToolRegistry.load(tools_dir)

    assert tr.load_failures == {}


@pytest.mark.anyio
async def test_refresh_recomputes_load_failures(tmp_path: Path) -> None:
    """A fixed file must clear its failure, not leave a stale one behind."""
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    broken = anyio.Path(tools_dir / "later_fixed.py")
    await broken.write_text("import definitely_not_installed\n", encoding="utf-8")
    tr = await ToolRegistry.load(tools_dir)
    assert "later_fixed.py" in tr.load_failures

    await broken.write_text("async def now_works() -> str:\n    return 'ok'\n", encoding="utf-8")
    await tr.refresh()

    assert tr.load_failures == {}
    assert set(tr.tools) == {"now_works"}


@pytest.mark.anyio
async def test_refresh_evicts_the_module_it_supersedes(tmp_path: Path) -> None:
    """``sys.modules`` must not grow by one dead module per edit.

    The module name embeds the file's content hash, so each edit mints a new key.
    Before eviction, one file edited six times left seven modules resident for the
    life of the process — unbounded growth in a long-lived Gateway.
    """
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    target = anyio.Path(tools_dir / "evictme.py")
    await target.write_text("async def evictme() -> str:\n    return 'v0'\n", encoding="utf-8")
    registry = await ToolRegistry.load(tools_dir, "evict")

    def live() -> int:
        return len([k for k in sys.modules if k.startswith("psi_tool_evictme_evict")])

    for i in range(1, 5):
        await target.write_text(f"async def evictme() -> str:\n    return 'v{i}'\n", encoding="utf-8")
        await registry.refresh()
        assert live() == 1, f"module count grew to {live()} after edit {i}"

    func = registry.get("evictme")
    assert func is not None
    assert await func() == "v4", "eviction must not break the surviving tool"

    await target.unlink()
    await registry.refresh()
    assert live() == 0, "deleting a tool file should evict its module too"


@pytest.mark.anyio
async def test_the_path_entry_is_normalized_before_being_used_as_a_key() -> None:
    """The refcount key must be a resolved path, or one dir gets two counters.

    ``_load_from_dir`` passes ``await anyio.Path(...).resolve()`` precisely because
    the string is a refcount key: the same directory reached through a symlink, a
    ``.`` segment, or different case would otherwise take its own slot and put a
    second spelling of one directory on ``sys.path``. This pins that the helper is
    key-faithful — two *distinct* strings really do get two entries, which is why
    the caller must normalize rather than relying on the helper to do it.
    """
    plain = os.path.join("C:" + os.sep, "aliased", "tools")
    dotted = os.path.join(plain, "sub", "..")
    assert plain != dotted, "test needs two distinct spellings"
    before = list(sys.path)

    async with _tools_dir_on_sys_path(plain), _tools_dir_on_sys_path(dotted):
        assert _SYS_PATH_DEPTH.get(plain) == 1
        assert _SYS_PATH_DEPTH.get(dotted) == 1, "distinct strings are distinct keys — hence caller-side resolve()"

    assert _SYS_PATH_DEPTH == {}
    assert sys.path == before


@pytest.mark.anyio
async def test_loading_one_dir_twice_reuses_a_single_path_entry(tmp_path: Path) -> None:
    """Two Sessions on the same resolved directory share one entry and one counter."""
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "t.py").write_text("async def t() -> str:\n    return 't'\n", encoding="utf-8")
    before = list(sys.path)

    first = await ToolRegistry.load(tools_dir, "spell-a")
    second = await ToolRegistry.load(tools_dir, "spell-b")

    assert set(first.tools) == {"t"}
    assert set(second.tools) == {"t"}
    assert _SYS_PATH_DEPTH == {}, "refcount table should be empty once both loads finish"
    assert sys.path == before, "no spelling of the directory should be left behind"
