"""Tool names reach the prompt from the registry, and the two sides are checked.

Two pipelines used to produce tool names independently: the workspace scanned
``tools/`` to write the ``## Tooling`` section, while ``ToolRegistry`` imported
those files and registered the async functions inside. Nothing made them agree,
and they didn't — the prompt named files (``browser``) while the registry
dispatched functions (``browser_click``), so the prompt advertised names that
resolved to nothing and omitted the ones that worked.

These tests pin both halves of the fix: the builder is handed the registry's own
list when it asks for one, and a disagreement between the two sides stops the
Session instead of being logged and forgotten.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from psi_agent.session.conversation import Conversation
from psi_agent.session.exposure import ALLOW_MISMATCH_ENV, ExposureMismatchError
from psi_agent.session.system_prompt import SystemPrompt

# ── tool_names injection ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_builder_declaring_tool_names_receives_them(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    async def builder(user_message: dict[str, Any] | None = None, *, tool_names: list[str] | None = None) -> str:
        seen["tool_names"] = tool_names
        return "prompt"

    conversation = await Conversation.from_workspace(tmp_path, "s1", appdata_root=str(tmp_path))
    await SystemPrompt(builder=builder).ensure(conversation, None, tool_names=["read", "write"])

    assert seen["tool_names"] == ["read", "write"]


@pytest.mark.anyio
async def test_builder_without_the_parameter_is_called_unchanged(tmp_path: Path) -> None:
    """A workspace that predates the argument keeps working."""
    calls: list[dict[str, Any] | None] = []

    async def builder(user_message: dict[str, Any] | None = None) -> str:
        calls.append(user_message)
        return "prompt"

    conversation = await Conversation.from_workspace(tmp_path, "s2", appdata_root=str(tmp_path))
    await SystemPrompt(builder=builder).ensure(conversation, {"content": "hi"}, tool_names=["read"])

    assert calls == [{"content": "hi"}]
    assert conversation.messages[0]["content"] == "prompt"


@pytest.mark.anyio
async def test_zero_arg_builder_still_supported(tmp_path: Path) -> None:
    async def builder() -> str:
        return "bare"

    conversation = await Conversation.from_workspace(tmp_path, "s3", appdata_root=str(tmp_path))
    await SystemPrompt(builder=builder).ensure(conversation, {"content": "hi"}, tool_names=["read"])

    assert conversation.messages[0]["content"] == "bare"


@pytest.mark.anyio
async def test_kwargs_builder_receives_tool_names(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    async def builder(user_message: dict[str, Any] | None = None, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "prompt"

    conversation = await Conversation.from_workspace(tmp_path, "s4", appdata_root=str(tmp_path))
    await SystemPrompt(builder=builder).ensure(conversation, None, tool_names=["edit"])

    assert seen["tool_names"] == ["edit"]


@pytest.mark.anyio
async def test_no_tool_names_means_the_argument_is_omitted(tmp_path: Path) -> None:
    """Omitted, not passed as None — the builder's own default must win."""
    seen: dict[str, Any] = {}

    async def builder(*, tool_names: list[str] | None = None) -> str:
        seen["tool_names"] = tool_names
        return "prompt"

    conversation = await Conversation.from_workspace(tmp_path, "s5", appdata_root=str(tmp_path))
    await SystemPrompt(builder=builder).ensure(conversation)

    assert seen["tool_names"] is None


@pytest.mark.anyio
async def test_tool_names_passed_on_rebuild_too(tmp_path: Path) -> None:
    seen: list[list[str] | None] = []

    async def builder(user_message: dict[str, Any] | None = None, *, tool_names: list[str] | None = None) -> str:
        seen.append(tool_names)
        return f"prompt {len(seen)}"

    async def checker() -> bool:
        return True

    conversation = await Conversation.from_workspace(tmp_path, "s6", appdata_root=str(tmp_path))
    sp = SystemPrompt(builder=builder, checker=checker)
    await sp.ensure(conversation, None, tool_names=["read"])
    conversation.add({"role": "user", "content": "hi"})
    await sp.ensure(conversation, None, tool_names=["read", "write"])

    assert seen == [["read"], ["read", "write"]]


# ── check_exposure ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_check_skipped_when_workspace_exposes_no_hooks() -> None:
    await SystemPrompt().check_exposure(registered={"read"})


@pytest.mark.anyio
async def test_matching_sides_pass() -> None:
    async def advertised_tool_names() -> list[str]:
        return ["read", "write"]

    sp = SystemPrompt(advertised_tools_fn=advertised_tool_names)

    await sp.check_exposure(registered={"read", "write"})


@pytest.mark.anyio
async def test_registered_tool_missing_from_the_prompt_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_MISMATCH_ENV, raising=False)

    async def advertised_tool_names() -> list[str]:
        return ["read"]

    sp = SystemPrompt(advertised_tools_fn=advertised_tool_names)

    with pytest.raises(ExposureMismatchError, match="feishu_doc_read"):
        await sp.check_exposure(registered={"read", "feishu_doc_read"})


@pytest.mark.anyio
async def test_advertised_tool_that_does_not_exist_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_MISMATCH_ENV, raising=False)

    async def advertised_tool_names() -> list[str]:
        return ["read", "browser"]

    sp = SystemPrompt(advertised_tools_fn=advertised_tool_names)

    with pytest.raises(ExposureMismatchError, match="browser"):
        await sp.check_exposure(registered={"read"})


@pytest.mark.anyio
async def test_skill_indexed_under_a_name_that_is_not_its_directory_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ALLOW_MISMATCH_ENV, raising=False)
    skill_md = tmp_path / "fusion-flow-legacy" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("---\nname: flow\n---\n", encoding="utf-8")

    async def indexed_skill_entries() -> list[tuple[str, str]]:
        return [("flow", str(skill_md))]

    sp = SystemPrompt(indexed_skills_fn=indexed_skill_entries)

    with pytest.raises(ExposureMismatchError, match=re.escape("skills/flow/SKILL.md")):
        await sp.check_exposure(registered=set())


@pytest.mark.anyio
async def test_a_hook_that_raises_does_not_block_startup() -> None:
    """A broken hook must not be a worse failure than the one it looks for."""

    async def advertised_tool_names() -> list[str]:
        raise RuntimeError("scan exploded")

    sp = SystemPrompt(advertised_tools_fn=advertised_tool_names)

    await sp.check_exposure(registered={"read"})


@pytest.mark.anyio
async def test_load_failure_reason_reaches_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_MISMATCH_ENV, raising=False)

    async def advertised_tool_names() -> list[str]:
        return ["run_flow"]

    sp = SystemPrompt(advertised_tools_fn=advertised_tool_names)

    with pytest.raises(ExposureMismatchError, match="antlr4"):
        await sp.check_exposure(
            registered=set(),
            load_failures={"run_flow.py": "ModuleNotFoundError(\"No module named 'antlr4'\")"},
        )


@pytest.mark.anyio
async def test_env_var_lets_a_known_mismatch_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOW_MISMATCH_ENV, "1")

    async def advertised_tool_names() -> list[str]:
        return ["read"]

    sp = SystemPrompt(advertised_tools_fn=advertised_tool_names)

    await sp.check_exposure(registered={"read", "unlisted"})


@pytest.mark.anyio
@pytest.mark.parametrize("bad", [None, 42, "read", {"read": 1}])
async def test_a_hook_returning_a_non_sequence_is_skipped_not_crashed(
    bad: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed hook must degrade like every other optional workspace hook.

    Iterating whatever the hook returned used to raise a bare ``TypeError`` out of
    ``SessionAgent.create`` — a broken safety net taking down startup harder than the
    thing it was watching for. A ``str`` and a ``dict`` are iterable but still wrong,
    so the guard checks for list/tuple rather than merely for iterability.
    """
    monkeypatch.delenv(ALLOW_MISMATCH_ENV, raising=False)

    async def advertised_tool_names() -> object:
        return bad

    sp = SystemPrompt(advertised_tools_fn=advertised_tool_names)

    await sp.check_exposure(registered={"read"})


@pytest.mark.anyio
async def test_success_log_names_only_what_was_actually_checked(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With only the skills hook defined, the log must not claim tools were checked."""
    monkeypatch.delenv(ALLOW_MISMATCH_ENV, raising=False)
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="INFO")

    async def indexed_skill_entries() -> list[tuple[str, str]]:
        return []

    try:
        await SystemPrompt(indexed_skills_fn=indexed_skill_entries).check_exposure(registered={"a", "b", "c"})
    finally:
        logger.remove(sink_id)

    assert messages, "expected a success log line"
    assert "skill(s)" in messages[-1]
    assert "tool(s)" not in messages[-1], "claimed a tool check that never ran"
