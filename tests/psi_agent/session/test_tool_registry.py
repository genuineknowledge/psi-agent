from __future__ import annotations

import builtins
import sys
import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

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


@pytest.mark.anyio
async def test_load_skips_imported_async_helpers(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "_helper.py").write_text(
        "async def helper(value: str) -> str:\n    return value\n", encoding="utf-8"
    )
    await anyio.Path(tools_dir / "public.py").write_text(
        textwrap.dedent(
            """
            from _helper import helper

            async def public_tool(value: str) -> str:
                return await helper(value)
            """
        ),
        encoding="utf-8",
    )

    tr = await ToolRegistry.load(tools_dir)

    assert set(tr.tools) == {"public_tool"}
    assert tr.get("helper") is None


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
async def test_get_last_file_wins() -> None:
    """get() resolves a duplicate name to the last-registered file."""

    async def echo_a() -> str:
        return "a"

    async def echo_b() -> str:
        return "b"

    tr = ToolRegistry(
        files={
            "a.py": FileEntry(
                file_hash="h1",
                tools={"echo": ToolFunction.from_callable(echo_a)},
                funcs={"echo": echo_a},
            ),
            "b.py": FileEntry(
                file_hash="h2",
                tools={"echo": ToolFunction.from_callable(echo_b)},
                funcs={"echo": echo_b},
            ),
        }
    )
    func = tr.get("echo")
    assert func is not None
    assert await func() == "b"


@pytest.mark.anyio
async def test_duplicate_name_metadata_and_callable_come_from_same_file(tmp_path: Path) -> None:
    """A name defined in two files resolves to one layer, not two.

    ``tools`` (what the model sees) is built by overwriting per file, so
    the last-loaded file wins there.  ``get()`` (what actually runs) has
    to land on that same file — otherwise the model is shown one layer's
    description and schema while a different layer's body executes.
    """
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "a_official.py").write_text(
        textwrap.dedent("""\
        async def echo() -> str:
            \"\"\"OFFICIAL DOC.\"\"\"
            return 'OFFICIAL'
    """),
        encoding="utf-8",
    )
    await anyio.Path(tools_dir / "z_personal.py").write_text(
        textwrap.dedent("""\
        async def echo() -> str:
            \"\"\"PERSONAL DOC.\"\"\"
            return 'PERSONAL'
    """),
        encoding="utf-8",
    )
    tr = await ToolRegistry.load(tools_dir)

    func = tr.get("echo")
    assert func is not None
    # Both sides name the same layer: "PERSONAL DOC." ↔ "PERSONAL".
    assert tr.tools["echo"].description.split()[0] == await func()
    # And that layer is the last-loaded file, matching ``tools``' overwrite order.
    assert await func() == "PERSONAL"


@pytest.mark.anyio
async def test_get_reaches_tools_shadowed_by_a_later_file(tmp_path: Path) -> None:
    """Losing a name collision must not make a file's other tools unreachable."""
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "a_official.py").write_text(
        textwrap.dedent("""\
        async def echo() -> str:
            return 'OFFICIAL'
        async def official_only() -> str:
            return 'official_only'
    """),
        encoding="utf-8",
    )
    await anyio.Path(tools_dir / "z_personal.py").write_text(
        textwrap.dedent("""\
        async def echo() -> str:
            return 'PERSONAL'
        async def personal_only() -> str:
            return 'personal_only'
    """),
        encoding="utf-8",
    )
    tr = await ToolRegistry.load(tools_dir)

    for name in ("echo", "official_only", "personal_only"):
        assert tr.get(name) is not None, f"{name} became unreachable"
    official_only = tr.get("official_only")
    personal_only = tr.get("personal_only")
    assert official_only is not None and personal_only is not None
    assert await official_only() == "official_only"
    assert await personal_only() == "personal_only"


# ── bare-name private helper imports ─────────────────────────────────────────
#
# Tool files import same-directory private helpers by bare name
# (``from _helper import thing``).  That only resolves if the tools dir is on
# ``sys.path``, which used to depend on the process cwd and on some *other*
# tool file happening to insert the dir first — so the files sorting earliest
# in glob order silently failed to load with only an ERROR log.


async def _write_helper_workspace(tools_dir: Path, marker: str) -> None:
    """A tools dir whose public tool imports a private helper by bare name."""
    await anyio.Path(tools_dir).mkdir(parents=True)
    await anyio.Path(tools_dir / "_priv_helper.py").write_text(
        f"MARKER = {marker!r}\nSTATE: list[str] = []\n", encoding="utf-8"
    )
    # Name sorts before "_priv_helper" has any chance of being pre-imported,
    # and before any file that might insert the dir onto sys.path.
    await anyio.Path(tools_dir / "aaa_first.py").write_text(
        textwrap.dedent(
            """
            from _priv_helper import MARKER

            async def which_marker() -> str:
                return MARKER
            """
        ),
        encoding="utf-8",
    )


@pytest.mark.anyio
async def test_load_resolves_bare_private_import_from_unrelated_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bare-name helper imports resolve even when cwd is not the tools dir."""
    tools_dir = tmp_path / "ws" / "tools"
    await _write_helper_workspace(tools_dir, "from-ws")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv("PYTHONPATH", raising=False)

    tr = await ToolRegistry.load(tools_dir)

    assert set(tr.tools) == {"which_marker"}, "bare private import failed to resolve"
    func = tr.get("which_marker")
    assert func is not None
    assert await func() == "from-ws"


@pytest.mark.anyio
async def test_load_does_not_depend_on_glob_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file sorting *before* every sys.path-inserting file still loads.

    This is the exact shape of the original failure: files whose names sorted
    first were exec'd while the tools dir was not yet on ``sys.path``.
    """
    tools_dir = tmp_path / "tools"
    await _write_helper_workspace(tools_dir, "ordered")
    # zzz_last mimics the tools that carry their own sys.path prologue.
    await anyio.Path(tools_dir / "zzz_last.py").write_text(
        textwrap.dedent(
            """
            import sys
            from pathlib import Path

            TOOLS_DIR = Path(__file__).resolve().parent
            if str(TOOLS_DIR) not in sys.path:
                sys.path.insert(0, str(TOOLS_DIR))

            from _priv_helper import MARKER

            async def last_tool() -> str:
                return MARKER
            """
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path.parent)
    tr = await ToolRegistry.load(tools_dir)

    assert set(tr.tools) == {"which_marker", "last_tool"}


@pytest.mark.anyio
async def test_load_leaves_sys_path_unchanged(tmp_path: Path) -> None:
    """The tools dir is not left behind on ``sys.path`` after loading."""
    tools_dir = tmp_path / "tools"
    await _write_helper_workspace(tools_dir, "scoped")

    before = list(sys.path)
    await ToolRegistry.load(tools_dir)
    assert str(tools_dir) not in sys.path
    assert sys.path == before


@pytest.mark.anyio
async def test_two_workspaces_bind_their_own_private_helper(tmp_path: Path) -> None:
    """Same-named private helpers in two tools dirs must not cross-contaminate.

    Bare-name imports share one global ``sys.modules`` slot, so without
    per-dir scoping the second workspace silently reuses the first one's
    helper — a real hazard, since two workspaces ship
    ``_assignment_tool_common.py`` with different contents.
    """
    first = tmp_path / "ws_a" / "tools"
    second = tmp_path / "ws_b" / "tools"
    await _write_helper_workspace(first, "ws-a")
    await _write_helper_workspace(second, "ws-b")

    tr_a = await ToolRegistry.load(first, "a")
    tr_b = await ToolRegistry.load(second, "b")

    func_a, func_b = tr_a.get("which_marker"), tr_b.get("which_marker")
    assert func_a is not None and func_b is not None
    assert await func_a() == "ws-a"
    assert await func_b() == "ws-b", "second workspace bound the first workspace's helper"


async def _write_dotted_helper_workspace(tools_dir: Path, marker: str) -> None:
    """A tools dir whose public tool imports a private *package* submodule.

    The shape ``agents/feishu/tools/_feishu/`` ships (15 modules), as opposed to
    ``_priv_helper.py``'s bare name.
    """
    package = tools_dir / "_priv_pkg"
    await anyio.Path(package).mkdir(parents=True)
    await anyio.Path(package / "__init__.py").write_text("", encoding="utf-8")
    await anyio.Path(package / "sub.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    await anyio.Path(tools_dir / "aaa_first.py").write_text(
        textwrap.dedent(
            """
            from _priv_pkg.sub import MARKER

            async def which_marker() -> str:
                return MARKER
            """
        ),
        encoding="utf-8",
    )


@pytest.mark.anyio
async def test_two_workspaces_bind_their_own_dotted_private_helper(tmp_path: Path) -> None:
    """Same-named private *packages* in two tools dirs must not cross-contaminate.

    The dotted counterpart of
    ``test_two_workspaces_bind_their_own_private_helper``, and a defect this
    fixture caught: ``_stash_private_modules`` used to skip every module name
    containing a dot, so a private package's submodules were never stashed and
    stayed in ``sys.modules`` after the scope closed.  The second workspace's
    ``from _priv_pkg.sub import MARKER`` then read the *first* workspace's file.

    Nothing about layering is involved — the two dirs load one after the other,
    each with its own scope, which is how production loads two workspaces today.
    That is why this criterion belongs here rather than with the layered ones:
    those pass with the defect reinstated, because their import hook resolves
    private names before ``sys.modules`` is ever consulted.
    """
    first = tmp_path / "ws_a" / "tools"
    second = tmp_path / "ws_b" / "tools"
    await _write_dotted_helper_workspace(first, "ws-a")
    await _write_dotted_helper_workspace(second, "ws-b")

    tr_a = await ToolRegistry.load(first, "a")
    tr_b = await ToolRegistry.load(second, "b")

    func_a, func_b = tr_a.get("which_marker"), tr_b.get("which_marker")
    assert func_a is not None and func_b is not None
    assert await func_a() == "ws-a"
    assert await func_b() == "ws-b", "second workspace bound the first workspace's dotted private helper"


@pytest.mark.anyio
async def test_dotted_private_submodules_do_not_survive_the_scope(tmp_path: Path) -> None:
    """A private package's submodules leave ``sys.modules`` when the scope closes.

    The residue behind the cross-contamination above, asserted directly: a
    ``_priv_pkg.sub`` left behind is what the *next* load of an unrelated dir
    would bind.  Names are checked rather than values because the residue itself
    is the subject here.
    """
    tools_dir = tmp_path / "ws" / "tools"
    await _write_dotted_helper_workspace(tools_dir, "scoped")

    await ToolRegistry.load(tools_dir, "s1")

    leaked = sorted(name for name in sys.modules if name == "_priv_pkg" or name.startswith("_priv_pkg."))
    assert leaked == [], f"dotted private modules left in sys.modules: {leaked}"


@pytest.mark.anyio
async def test_refresh_preserves_private_helper_module_state(tmp_path: Path) -> None:
    """Module-level state in a private helper survives a refresh.

    Helpers such as ``_background_process_registry`` track live processes in
    module globals, so a refresh must not hand tools a fresh helper module.
    """
    tools_dir = tmp_path / "tools"
    await _write_helper_workspace(tools_dir, "stateful")
    await anyio.Path(tools_dir / "aaa_first.py").write_text(
        textwrap.dedent(
            """
            import _priv_helper

            async def remember(item: str) -> int:
                _priv_helper.STATE.append(item)
                return len(_priv_helper.STATE)
            """
        ),
        encoding="utf-8",
    )

    tr = await ToolRegistry.load(tools_dir)
    remember = tr.get("remember")
    assert remember is not None
    assert await remember("one") == 1

    # Edit the tool itself so refresh re-execs it and re-imports the helper.
    # A cached (unchanged) file would keep its existing module reference and
    # pass regardless, so the file has to actually change.
    await anyio.Path(tools_dir / "aaa_first.py").write_text(
        textwrap.dedent(
            """
            import _priv_helper

            async def remember(item: str) -> int:
                _priv_helper.STATE.append(item)
                return len(_priv_helper.STATE)

            async def extra() -> str:
                return 'extra'
            """
        ),
        encoding="utf-8",
    )
    await tr.refresh()

    remember_after = tr.get("remember")
    assert remember_after is not None
    assert await remember_after("two") == 2, "private helper state was reset by refresh"


# ── process-wide compiled-module cache ───────────────────────────────────────
#
# ``ToolRegistry.load()`` builds a fresh instance with ``old_files=None``, so the
# per-instance hash comparison never fires across sessions and every session
# re-compiled every tool file.  A process-wide cache keyed on
# ``(layer_id, file_hash)`` makes the second load reuse the module object.


@contextmanager
def _count_compiles(tools_dir: Path) -> Iterator[list[str]]:
    """Record every ``compile()`` whose filename lives under *tools_dir*."""
    calls: list[str] = []
    real_compile = builtins.compile
    root = str(Path(tools_dir).resolve())

    def counting_compile(source: Any, filename: Any, mode: str, *args: Any, **kwargs: Any) -> Any:
        try:
            under_tools = str(Path(str(filename)).resolve()).startswith(root)
        except OSError:
            under_tools = False
        if under_tools:
            calls.append(str(filename))
        return real_compile(source, filename, mode, *args, **kwargs)

    builtins.compile = counting_compile  # ty: ignore
    try:
        yield calls
    finally:
        builtins.compile = real_compile


@pytest.mark.anyio
async def test_second_load_of_same_dir_reuses_compiled_modules(tmp_path: Path) -> None:
    """A second ``load()`` of an unchanged dir compiles nothing.

    ``load()`` passes ``old_files=None``, so without a process-wide cache the
    second load re-compiles every file even though the bytes are identical.
    """
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    for name in ("a", "b", "c"):
        await anyio.Path(tools_dir / f"{name}.py").write_text(
            f"async def tool_{name}() -> str:\n    return {name!r}\n", encoding="utf-8"
        )

    with _count_compiles(tools_dir) as first:
        tr_first = await ToolRegistry.load(tools_dir, "session-one")
    with _count_compiles(tools_dir) as second:
        tr_second = await ToolRegistry.load(tools_dir, "session-two")

    assert len(first) == 3, f"first load should compile every file, compiled {first}"
    assert len(second) == 0, f"second load re-compiled {len(second)} file(s): {second}"
    assert set(tr_first.tools) == set(tr_second.tools) == {"tool_a", "tool_b", "tool_c"}
    reused = tr_second.get("tool_a")
    assert reused is not None
    assert await reused() == "a"


@pytest.mark.anyio
async def test_changed_file_is_recompiled_despite_cache(tmp_path: Path) -> None:
    """A content change (new hash) must miss the cache and re-compile."""
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "a.py").write_text("async def foo() -> str:\n    return 'first'\n", encoding="utf-8")

    tr = await ToolRegistry.load(tools_dir, "s")
    first = tr.get("foo")
    assert first is not None
    assert await first() == "first"

    await anyio.Path(tools_dir / "a.py").write_text("async def foo() -> str:\n    return 'second'\n", encoding="utf-8")

    with _count_compiles(tools_dir) as calls:
        tr_again = await ToolRegistry.load(tools_dir, "s")
    assert len(calls) == 1, "changed file was served from cache instead of re-compiled"
    again = tr_again.get("foo")
    assert again is not None
    assert await again() == "second"


@pytest.mark.anyio
async def test_cache_does_not_share_identical_files_across_dirs(tmp_path: Path) -> None:
    """Byte-identical files in two tools dirs get their own module.

    Same bytes mean the same ``file_hash``, so a cache keyed on hash alone
    would hand the second dir the first dir's module — and with it the first
    dir's private helper binding.
    """
    source = "import _priv_helper\n\nasync def which() -> str:\n    return _priv_helper.MARKER\n"
    first_dir, second_dir = tmp_path / "ws_a" / "tools", tmp_path / "ws_b" / "tools"
    for tools_dir, marker in ((first_dir, "ws-a"), (second_dir, "ws-b")):
        await anyio.Path(tools_dir).mkdir(parents=True)
        await anyio.Path(tools_dir / "_priv_helper.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
        await anyio.Path(tools_dir / "same.py").write_text(source, encoding="utf-8")

    tr_a = await ToolRegistry.load(first_dir, "a")
    with _count_compiles(second_dir) as calls:
        tr_b = await ToolRegistry.load(second_dir, "b")

    # Only ``same.py`` is under test; the private helper is compiled by
    # importlib on its own schedule and would blur the count.
    same_compiles = [call for call in calls if Path(call).name == "same.py"]
    assert len(same_compiles) == 1, "identical bytes in another dir were served from the first dir's cache"
    func_a, func_b = tr_a.get("which"), tr_b.get("which")
    assert func_a is not None and func_b is not None
    assert await func_a() == "ws-a"
    assert await func_b() == "ws-b", "second dir got the first dir's cached module"


# ── deterministic load order ─────────────────────────────────────────────────
#
# The scan used to iterate ``glob("*.py")`` raw, so load order was whatever the
# filesystem returned.  That made order a hidden input to other behaviour: 59
# tool files once failed to load because glob order put a file that carries its
# own ``sys.path`` preamble *after* the files depending on it, and one A1 probe
# criterion was structurally unable to fail because the first of three
# same-layer importers bound the submodule as an attribute on the parent for
# the other two.
#
# "Load twice, compare" cannot judge this: one filesystem returns one order, so
# such a test is green whether or not the code sorts.  The criteria below drive
# the load through a glob whose order is deliberately *not* the target order and
# assert on the exec order actually observed, which is the layer the fix is in.
# Reading the ``tools`` dict instead would prove nothing — it is a dict, and
# these files contribute the same entries in any order.


@contextmanager
def _shuffled_glob(order: str) -> Iterator[None]:
    """Make ``anyio.Path.glob`` yield in *order* — the opposite of the target.

    ``"reverse"`` yields reverse-sorted, ``"rotate"`` moves the last name to the
    front.  Both differ from sorted order for the inputs used here, so a raw
    scan visibly inherits this order and a sorting scan visibly does not.
    """
    real_glob = anyio.Path.glob

    def fake_glob(self: anyio.Path, pattern: str) -> Any:
        async def gen() -> Any:
            found = [p async for p in real_glob(self, pattern)]
            found.sort(key=lambda p: p.name, reverse=True)
            if order == "rotate":
                found = found[-1:] + found[:-1]
            for item in found:
                yield item

        return gen()

    anyio.Path.glob = fake_glob  # ty: ignore
    try:
        yield
    finally:
        anyio.Path.glob = real_glob


@contextmanager
def _record_exec_order() -> Iterator[list[str]]:
    """Record the tool-file basenames reaching ``compile()``, in exec order.

    ``compile()`` is the narrowest observation point for *load* order: it is
    called once per file actually exec'd, before that file's body runs.  Its
    ``filename`` argument is the tool path the scan passed in.
    """
    seen: list[str] = []
    real_compile = builtins.compile

    def recording_compile(source: Any, filename: Any, mode: str, *args: Any, **kwargs: Any) -> Any:
        name = Path(str(filename)).name
        if name.endswith(".py"):
            seen.append(name)
        return real_compile(source, filename, mode, *args, **kwargs)

    builtins.compile = recording_compile  # ty: ignore
    try:
        yield seen
    finally:
        builtins.compile = real_compile


async def _write_numbered_tools(tools_dir: Path, stems: tuple[str, ...]) -> None:
    """One trivially-loadable tool per stem, each contributing a distinct name."""
    await anyio.Path(tools_dir).mkdir(parents=True)
    for stem in stems:
        await anyio.Path(tools_dir / f"{stem}.py").write_text(
            f"async def tool_{stem}() -> str:\n    return {stem!r}\n", encoding="utf-8"
        )


@pytest.mark.anyio
@pytest.mark.parametrize("glob_order", ["reverse", "rotate"])
async def test_files_exec_in_sorted_order_not_glob_order(tmp_path: Path, glob_order: str) -> None:
    """Files exec in sorted-by-name order even when glob yields another order.

    Uses a unique session id so the process-wide module cache cannot serve
    these files and skip the ``compile()`` this criterion observes.
    """
    stems = ("alpha", "bravo", "charlie", "delta")
    tools_dir = tmp_path / "tools"
    await _write_numbered_tools(tools_dir, stems)

    with _shuffled_glob(glob_order), _record_exec_order() as order:
        tr = await ToolRegistry.load(tools_dir, f"order-{glob_order}-{tmp_path.name}")

    loaded = [name for name in order if Path(name).stem in stems]
    assert loaded == [f"{stem}.py" for stem in stems], (
        f"files exec'd in {loaded}, not sorted order — load order is inheriting glob order"
    )
    assert set(tr.tools) == {f"tool_{stem}" for stem in stems}


@pytest.mark.anyio
async def test_registry_file_keys_follow_sorted_order(tmp_path: Path) -> None:
    """``_files`` is keyed in sorted order, so ``get()``'s last-wins is defined.

    ``get()`` resolves duplicate names by walking ``_files`` in reverse
    insertion order.  That rule only names one winner if insertion order is
    itself fixed; under a raw glob the "last" file is whatever the filesystem
    happened to return last.
    """
    stems = ("alpha", "bravo", "charlie", "delta")
    tools_dir = tmp_path / "tools"
    await _write_numbered_tools(tools_dir, stems)

    with _shuffled_glob("reverse"):
        tr = await ToolRegistry.load(tools_dir, f"keys-{tmp_path.name}")

    keys = [Path(path).name for path in tr._files]
    assert keys == [f"{stem}.py" for stem in stems], f"_files keyed in {keys}, not sorted order"


@pytest.mark.anyio
async def test_duplicate_name_winner_is_the_last_in_sorted_order(tmp_path: Path) -> None:
    """A name in two files resolves to the sorted-last file, whatever glob says.

    This is the consequence users see: with glob order deciding, the same two
    files could resolve either way on a different filesystem.  Both files
    define ``pick``, so the winner is observable by calling it.
    """
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir(parents=True)
    for stem in ("a_early", "z_late"):
        await anyio.Path(tools_dir / f"{stem}.py").write_text(
            f'async def pick() -> str:\n    """From {stem}."""\n    return {stem!r}\n', encoding="utf-8"
        )

    with _shuffled_glob("reverse"):
        tr = await ToolRegistry.load(tools_dir, f"dupe-{tmp_path.name}")

    winner = tr.get("pick")
    assert winner is not None
    assert await winner() == "z_late", "duplicate-name winner followed glob order, not sorted order"
    # Metadata and callable have to name the same file, so the description is
    # checked too — a distinct docstring per file makes the source observable.
    assert tr.tools["pick"].description == "From z_late."


# ── load order on the real filesystem ────────────────────────────────────────
#
# The three criteria above stub ``glob`` so they can drive a known-bad order.
# That is the only way to observe the sorting layer, but it also means they say
# nothing about what any *actual* filesystem returns: they are equally green on
# NTFS, ext4 and APFS because none of those filesystems is consulted.
#
# The fix exists for a cross-filesystem difference, so at least one criterion
# has to let the real filesystem answer.  The two below do, by writing files in
# an order deliberately unlike sorted order and never touching ``glob``:
#
# * On a filesystem that returns creation order (ext4 for small directories),
#   the raw order here is reverse-sorted and the sorting layer is what makes
#   the assertion hold.
# * On a filesystem that hashes names (NTFS, ext4 with dir_index on larger
#   directories), the raw order is that hash order — also not sorted.
#
# Neither of those is asserted, because which one a runner has is not this
# repository's business.  What is asserted is the property the preamble case
# needs: whatever the filesystem says, the file that installs ``sys.path``
# precedes the file consuming it, and exec order is sorted order.
#
# ``scripts/check_tool_glob_order.py`` reports the raw-vs-sorted distance as a
# number for the record; these criteria judge the consequence.


@pytest.mark.anyio
async def test_real_filesystem_glob_does_not_decide_exec_order(tmp_path: Path) -> None:
    """Exec order is sorted order with the real filesystem in the loop.

    Files are *created* in reverse-sorted order so that creation order and
    sorted order disagree maximally.  A raw scan on a creation-order
    filesystem would then exec them backwards; a hashing filesystem yields
    some third order.  Sorted order is the only outcome consistent with both.
    """
    stems = ("alpha", "bravo", "charlie", "delta", "echo_", "foxtrot")
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir(parents=True)
    for stem in sorted(stems, reverse=True):
        await anyio.Path(tools_dir / f"{stem}.py").write_text(
            f"async def tool_{stem}() -> str:\n    return {stem!r}\n", encoding="utf-8"
        )

    with _record_exec_order() as order:
        tr = await ToolRegistry.load(tools_dir, f"realfs-{tmp_path.name}")

    loaded = [name for name in order if Path(name).stem in stems]
    assert loaded == [f"{stem}.py" for stem in sorted(stems)], (
        f"files exec'd in {loaded} on this filesystem, not sorted order"
    )
    assert set(tr.tools) == {f"tool_{stem}" for stem in stems}


@pytest.mark.anyio
async def test_real_filesystem_loads_preamble_file_before_its_consumer(tmp_path: Path) -> None:
    """The production failure shape, judged against the real filesystem.

    ``a_preamble.py`` is the file that puts the tools dir on ``sys.path``;
    ``z_consumer.py`` imports a private helper by bare name and can only load
    after it.  This is what took down 59 tool files.  Both files are created
    consumer-first, so on a creation-order filesystem a raw scan reaches the
    consumer while ``sys.path`` is still missing the dir.

    The verdict is read from the loaded tool set rather than from a log:
    a file that fails to load is skipped with an ERROR and contributes no
    tool, so a missing name *is* the failure.
    """
    tools_dir = tmp_path / "tools"
    await anyio.Path(tools_dir).mkdir(parents=True)

    # Created first, must load last.  Nothing else puts the dir on sys.path.
    await anyio.Path(tools_dir / "z_consumer.py").write_text(
        textwrap.dedent(
            """
            from _preamble_helper import MARKER

            async def consumer_marker() -> str:
                return MARKER
            """
        ),
        encoding="utf-8",
    )
    await anyio.Path(tools_dir / "_preamble_helper.py").write_text('MARKER = "via-preamble"\n', encoding="utf-8")
    await anyio.Path(tools_dir / "a_preamble.py").write_text(
        textwrap.dedent(
            """
            import sys
            from pathlib import Path

            _here = str(Path(__file__).resolve().parent)
            if _here not in sys.path:
                sys.path.insert(0, _here)

            async def preamble_marker() -> str:
                return "preamble"
            """
        ),
        encoding="utf-8",
    )

    tr = await ToolRegistry.load(tools_dir, f"preamble-{tmp_path.name}")

    consumer = tr.get("consumer_marker")
    assert consumer is not None, (
        "z_consumer.py did not load: its bare-name helper import ran before a_preamble.py "
        "put the tools dir on sys.path — this is the 59-file production failure"
    )
    assert await consumer() == "via-preamble"
