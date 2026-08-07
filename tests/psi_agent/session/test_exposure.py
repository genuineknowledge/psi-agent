from __future__ import annotations

from pathlib import Path

import pytest

from psi_agent.session.exposure import (
    ALLOW_MISMATCH_ENV,
    ExposureMismatchError,
    check_skill_exposure,
    check_tool_exposure,
    enforce,
    mismatch_allowed,
)

# ── check_tool_exposure ───────────────────────────────────────────────────────


def test_equal_sets_have_no_problems() -> None:
    assert check_tool_exposure({"read", "write"}, {"read", "write"}) == []


def test_both_sides_empty_is_fine() -> None:
    assert check_tool_exposure(set(), set()) == []


def test_advertised_but_not_registered_is_reported() -> None:
    problems = check_tool_exposure({"read", "ghost"}, {"read"})
    assert len(problems) == 1
    assert "ghost" in problems[0]
    assert "advertised in the prompt but not registered" in problems[0]


def test_registered_but_not_advertised_is_reported() -> None:
    problems = check_tool_exposure({"read"}, {"read", "hidden"})
    assert len(problems) == 1
    assert "hidden" in problems[0]
    assert "not told they exist" in problems[0]


def test_both_directions_reported_separately() -> None:
    problems = check_tool_exposure({"read", "ghost"}, {"read", "hidden"})
    assert len(problems) == 2
    assert any("ghost" in p for p in problems)
    assert any("hidden" in p for p in problems)


def test_load_failures_are_attached_to_the_missing_tools() -> None:
    problems = check_tool_exposure(
        {"read", "run_flow"},
        {"read"},
        load_failures={"run_flow.py": "ModuleNotFoundError(\"No module named 'antlr4'\")"},
    )
    assert len(problems) == 1
    assert "antlr4" in problems[0]
    assert "failed to import" in problems[0]


def test_load_failures_ignored_when_nothing_is_missing() -> None:
    problems = check_tool_exposure({"read"}, {"read"}, load_failures={"other.py": "boom"})
    assert problems == []


def test_long_lists_are_truncated_with_a_count() -> None:
    registered = {f"tool_{i:03d}" for i in range(30)}
    problems = check_tool_exposure(set(), registered)
    assert "+10 more" in problems[0]


# ── check_skill_exposure ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_skill_name_matching_its_directory_passes(tmp_path: Path) -> None:
    skill_md = tmp_path / "my-skill" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("# hi", encoding="utf-8")

    assert await check_skill_exposure([("my-skill", skill_md)]) == []


@pytest.mark.anyio
async def test_skill_name_disagreeing_with_directory_is_reported(tmp_path: Path) -> None:
    skill_md = tmp_path / "fusion-flow-legacy" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("---\nname: flow\n---\n", encoding="utf-8")

    problems = await check_skill_exposure([("flow", skill_md)])
    assert len(problems) == 1
    assert "skills/flow/SKILL.md" in problems[0]
    assert "fusion-flow-legacy" in problems[0]


@pytest.mark.anyio
async def test_missing_skill_file_is_reported(tmp_path: Path) -> None:
    problems = await check_skill_exposure([("gone", tmp_path / "gone" / "SKILL.md")])
    assert len(problems) == 1
    assert "missing" in problems[0]


@pytest.mark.anyio
async def test_string_paths_are_accepted(tmp_path: Path) -> None:
    skill_md = tmp_path / "ok" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("x", encoding="utf-8")

    assert await check_skill_exposure([("ok", str(skill_md))]) == []


@pytest.mark.anyio
async def test_no_entries_is_fine() -> None:
    assert await check_skill_exposure([]) == []


# ── enforce ───────────────────────────────────────────────────────────────────


def test_enforce_is_a_noop_without_problems() -> None:
    enforce([])


def test_enforce_raises_and_names_the_escape_hatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_MISMATCH_ENV, raising=False)

    with pytest.raises(ExposureMismatchError) as excinfo:
        enforce(["something is wrong"], context="Session startup")

    message = str(excinfo.value)
    assert "something is wrong" in message
    assert "Session startup" in message
    assert ALLOW_MISMATCH_ENV in message


def test_env_var_downgrades_the_raise_to_a_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOW_MISMATCH_ENV, "1")
    enforce(["tolerated"])


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " yes "])
def test_truthy_env_values_allow_mismatch(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ALLOW_MISMATCH_ENV, value)
    assert mismatch_allowed() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "maybe"])
def test_other_env_values_still_raise(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ALLOW_MISMATCH_ENV, value)
    assert mismatch_allowed() is False
    with pytest.raises(ExposureMismatchError):
        enforce(["still wrong"])
