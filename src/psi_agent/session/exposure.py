"""Startup consistency checks between what the prompt advertises and what runs.

Tool names reach the model through **two independent pipelines**:

- the *prompt* side, where the workspace's ``systems/system.py`` renders a
  ``## Tooling`` section from its own scan of ``tools/``;
- the *exec* side, where ``ToolRegistry`` imports those files and registers the
  async functions it finds.

Nothing forced the two to agree, and they historically did not. A tool could
register fine and never appear in the prompt (the model never learns it exists —
"we built that feature and it never gets used"), or the prompt could advertise a
name that resolves to no callable at all (the model calls it and gets an error).
Both failures were silent.

The same split exists for skills: the prompt tells the model to read
``skills/<name>/SKILL.md``, so ``<name>`` has to be a real directory. Taking it
from the skill's frontmatter instead handed the model a path that did not exist
(and broke ``skill_manage``, which resolves by directory too). haitun now takes
the index name from the directory and lets frontmatter supply metadata only, so
this check is mostly a regression guard on that — plus a real check that every
indexed ``SKILL.md`` is still readable.

These checks run at Session startup and raise ``ExposureMismatchError`` rather than
logging and continuing. A mismatch means the model has been told something untrue
about its own capabilities, which is not a condition to discover from a log line
three weeks later. ``PSI_ALLOW_EXPOSURE_MISMATCH=1`` downgrades the raise to an
error log for operators who need to boot a known-broken workspace anyway; the
exception message names that variable so nobody has to find it here.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

import anyio
from loguru import logger

SkillEntries = Iterable[tuple[str, str | Path]]
"""``(skill name, path to its SKILL.md)`` pairs, as the index used them."""

ALLOW_MISMATCH_ENV = "PSI_ALLOW_EXPOSURE_MISMATCH"
"""Set to ``1``/``true``/``yes`` to log instead of raising."""

_NAMES_PREVIEWED = 20
"""How many names a problem line lists before collapsing to a count."""


class ExposureMismatchError(RuntimeError):
    """The prompt and the runtime disagree about tools or skills."""


def _bullets(names: Iterable[str]) -> str:
    """Render a sorted, comma-joined preview, collapsing the tail to a count."""
    items = sorted(names)
    shown = ", ".join(items[:_NAMES_PREVIEWED])
    if len(items) <= _NAMES_PREVIEWED:
        return shown
    return f"{shown}, … (+{len(items) - _NAMES_PREVIEWED} more)"


def check_tool_exposure(
    advertised: set[str],
    registered: set[str],
    *,
    load_failures: dict[str, str] | None = None,
) -> list[str]:
    """Compare the two tool-name pipelines; return one line per problem.

    *advertised* is what the prompt side derived on its own; *registered* is what
    ``ToolRegistry`` actually loaded. The sets must be equal.

    *load_failures* maps a tool file name to the reason it failed to import. Names
    that are advertised but missing **because their file did not load** are
    reported as their own class of problem, with the reason attached — that
    distinguishes "you forgot to install a dependency" from "the prompt lists a
    name that no function ever had", which need very different fixes.

    Args:
        advertised: Tool names the prompt side believes exist.
        registered: Tool names the exec side can actually dispatch.
        load_failures: File name → import error text, from the registry.

    Returns:
        Human-readable problem lines; empty when the two sides agree.
    """
    problems: list[str] = []

    missing = advertised - registered
    if missing:
        failures = load_failures or {}
        if failures:
            items = sorted(failures.items())[:_NAMES_PREVIEWED]
            reasons = "; ".join(f"{name}: {reason}" for name, reason in items)
            if len(failures) > _NAMES_PREVIEWED:
                reasons += f"; … (+{len(failures) - _NAMES_PREVIEWED} more files)"
            problems.append(
                f"{len(missing)} tool(s) advertised in the prompt but not registered, "
                f"and {len(failures)} tool file(s) failed to import — most likely the same cause. "
                f"Fix: resolve the import errors below, then restart the Session; "
                f"the names come back on their own. "
                f"Advertised-only: {_bullets(missing)}. Import failures: {reasons}"
            )
        else:
            problems.append(
                f"{len(missing)} tool(s) advertised in the prompt but not registered — "
                f"the model will be told to call names that dispatch to nothing. "
                f"These are usually *file* names where a *function* name was meant "
                f"(a file may define many tools): have the prompt builder take the "
                f"registry's list via its 'tool_names' argument instead of scanning "
                f"'tools/' itself. Advertised-only: {_bullets(missing)}"
            )

    extra = registered - advertised
    if extra:
        problems.append(
            f"{len(extra)} tool(s) registered but not advertised in the prompt — "
            f"the model is not told they exist, so a working feature never gets used. "
            f"Fix: widen the prompt side's own scan (the 'advertised_tool_names' hook) "
            f"to cover them — e.g. names an '@mcp' declaration expands to, or async "
            f"functions re-exported from a '_helper' module. "
            f"Registered-only: {_bullets(extra)}"
        )

    return problems


async def check_skill_exposure(entries: SkillEntries) -> list[str]:
    """Verify each indexed skill name resolves to a readable ``SKILL.md``.

    *entries* is a sequence of ``(name, skill_md_path)`` pairs — the names the
    prompt puts in ``<available_skills>`` paired with the file each was read from.

    Two things have to hold for the prompt's ``skills/<name>/SKILL.md`` instruction
    to work: the file must exist, and the directory holding it must be named
    exactly ``name``. An index that derives the name from frontmatter rather than
    from the directory is how this breaks — the index shows one thing, the path
    needs another, and the model's read fails.

    Args:
        entries: Sequence of ``(name, path)`` pairs from the skills index.

    Returns:
        Human-readable problem lines; empty when every name resolves.
    """
    problems: list[str] = []

    for name, raw_path in entries:
        path = anyio.Path(str(raw_path))

        if not await path.is_file():
            problems.append(f"skill {name!r} is indexed but its SKILL.md is missing: {path}")
            continue

        dir_name = path.parent.name
        if dir_name != name:
            problems.append(
                f"skill is indexed as {name!r} but lives in directory {dir_name!r} — "
                f"the prompt tells the model to read 'skills/{name}/SKILL.md', which does not exist. "
                f"Fix the index to use the directory name; do not rename the skill to match, since "
                f"runtime skills such as fusion-flow are packaged upstream and overwritten ({path})"
            )

    return problems


def mismatch_allowed() -> bool:
    """True when the operator opted into booting with a known mismatch."""
    return os.environ.get(ALLOW_MISMATCH_ENV, "").strip().lower() in ("1", "true", "yes")


def enforce(problems: list[str], *, context: str = "") -> None:
    """Raise ``ExposureMismatchError`` for *problems*, or log when opted out.

    Args:
        problems: Problem lines from the ``check_*`` functions; empty is a no-op.
        context: Short label naming what was checked, used in the message.

    Raises:
        ExposureMismatchError: When *problems* is non-empty and the escape hatch is off.
    """
    if not problems:
        return

    where = f" ({context})" if context else ""
    body = "\n".join(f"  - {p}" for p in problems)
    message = (
        f"Prompt/runtime exposure mismatch{where}:\n{body}\n"
        f"The model has been told something untrue about its own tools or skills. "
        f"Set {ALLOW_MISMATCH_ENV}=1 to downgrade this to a log line and start anyway."
    )

    if mismatch_allowed():
        logger.error(f"{message} [continuing: {ALLOW_MISMATCH_ENV} is set]")
        return

    raise ExposureMismatchError(message)
