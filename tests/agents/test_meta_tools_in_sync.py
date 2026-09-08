"""The meta tools exist once per agent pack and must stay identical.

``agents/desktop/tools/`` and ``agents/feishu/tools/`` each carry their own copy
of the tool-discovery tools. As of 2026-09-08 all five copies are byte-identical
and git shows both sides were only ever touched by 30e0f28a, so the duplication
is a straight copy rather than a fork.

No shared layer is extracted: the tools read pack-local paths, so hoisting them
would turn into ``if product_line ==`` branching inside code that is otherwise
pack-agnostic. Instead the "keep both copies in step" rule lives here, where a
one-sided edit turns CI red instead of drifting silently -- which is how the
``prompt_sections`` copies did drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

_PACKS = {
    "desktop": _REPO_ROOT / "agents" / "desktop" / "tools",
    "feishu": _REPO_ROOT / "agents" / "feishu" / "tools",
}

# Every meta tool that must exist in both packs with identical content.
# A NEW META TOOL MUST BE ADDED TO THIS LIST -- a file missing from here is not
# compared at all, so the copies could drift without any test going red.
META_TOOL_FILES = (
    "_tool_index.py",
    "tool_describe.py",
    "tool_search.py",
    "tool_search_code.py",
    "skill_manage.py",
)


def _read_lines(path: Path) -> list[str]:
    """Read as lines with newlines dropped.

    The repo is checked out CRLF on Windows and LF elsewhere, and single files
    do slip in with the other ending. Comparing raw bytes reports nearly every
    file as different for that reason alone, so line endings are normalised away
    before anything is compared.
    """
    return path.read_bytes().decode("utf-8").splitlines()


@pytest.mark.parametrize("filename", META_TOOL_FILES)
def test_meta_tool_exists_in_both_packs(filename: str) -> None:
    """A one-sided file is drift too, so name the side that is missing it."""
    missing = [pack for pack, root in _PACKS.items() if not (root / filename).is_file()]

    assert not missing, (
        f"{filename} is missing from: {', '.join(missing)}. "
        f"Present in: {', '.join(p for p in _PACKS if p not in missing) or 'neither pack'}. "
        "A meta tool added to one pack only is exactly the drift this test guards; "
        "copy it to the other pack, or drop the name from META_TOOL_FILES if the "
        "tool is gone from both."
    )


@pytest.mark.parametrize("filename", META_TOOL_FILES)
def test_meta_tool_content_matches_across_packs(filename: str) -> None:
    """Compare content, and point at the first differing line rather than dumping a diff."""
    desktop = _PACKS["desktop"] / filename
    feishu = _PACKS["feishu"] / filename
    if not (desktop.is_file() and feishu.is_file()):
        pytest.skip(f"{filename} is one-sided; test_meta_tool_exists_in_both_packs reports it")

    desktop_lines = _read_lines(desktop)
    feishu_lines = _read_lines(feishu)
    if desktop_lines == feishu_lines:
        return

    # strict=False on purpose: unequal lengths are handled after the loop, where
    # the message can say which side has the extra lines.
    for index, (left, right) in enumerate(zip(desktop_lines, feishu_lines, strict=False), start=1):
        if left != right:
            pytest.fail(
                f"{filename} differs between the two packs, first at line {index}:\n"
                f"  agents/desktop/tools/{filename}:{index}: {left!r}\n"
                f"  agents/feishu/tools/{filename}:{index}: {right!r}"
            )

    shorter, longer = ("desktop", "feishu") if len(desktop_lines) < len(feishu_lines) else ("feishu", "desktop")
    common = min(len(desktop_lines), len(feishu_lines))
    extra = max(len(desktop_lines), len(feishu_lines))
    pytest.fail(
        f"{filename} differs between the two packs: the first {common} lines match, "
        f"but {longer} has {extra - common} extra line(s) from line {common + 1} "
        f"while {shorter} ends at line {common}. "
        f"First extra line in {longer}: "
        f"{(feishu_lines if longer == 'feishu' else desktop_lines)[common]!r}"
    )
