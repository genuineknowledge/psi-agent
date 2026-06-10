"""Curator — periodic skill library maintenance for hermes-style-workspace.

``run_curator(workspace_dir, complete_fn)`` scans agent-created skills,
applies time-based status labels, then calls the LLM once for semantic
review and executes the recommended keep/patch/merge/archive operations.

Only skills with ``created_by: agent`` in their SKILL.md frontmatter are
touched; user-authored skills are left untouched.
"""

from __future__ import annotations

import json as _json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import anyio

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTIVE_DAYS = 30
STALE_DAYS = 90
DEFAULT_INTERVAL_HOURS = 168  # 7 days
DEFAULT_MIN_IDLE_HOURS = 2.0
CURATOR_STATE_FILENAME = ".curator_state.json"

_CURATOR_PROMPT_TEMPLATE = """\
You are a skill library curator. Below is a list of agent-created skills \
with their metadata and current status labels.

For each skill, decide one of:
  keep    — skill is healthy, no action needed
  patch   — skill needs content improvements (provide updated body)
  merge   — skill overlaps with another (provide target skill name)
  archive — skill is stale, low-quality, or superseded

Respond with a JSON array. Each element must have:
  {{
    "skill_name": "<name>",
    "action": "keep" | "patch" | "merge" | "archive",
    "reason": "<brief reason>",
    "patch_content": "<new body if action=patch, else null>",
    "merge_into": "<target skill name if action=merge, else null>"
  }}

Respond ONLY with the JSON array, no other text.

Skills to review:
{skills_summary}
"""

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

CompleteFn = Callable[[list[dict[str, Any]]], Awaitable[str]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter from SKILL.md content.

    Args:
        content: Raw SKILL.md file content.

    Returns:
        Tuple of (frontmatter_dict, body_text).
    """
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    fm_text = content[4:end]
    body = content[end + 4 :].lstrip("\n")
    fm: dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip("'\"")
    return fm, body


def _parse_iso_date(date_str: str) -> datetime | None:
    """Parse an ISO 8601 datetime string (UTC).

    Args:
        date_str: ISO 8601 string, e.g. ``2026-06-01T12:00:00Z``.

    Returns:
        Aware datetime in UTC, or None if parsing fails.
    """
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _label_status(fm: dict[str, str]) -> str:
    """Compute active/stale/archive-candidate label from frontmatter dates.

    Args:
        fm: Parsed frontmatter dict.

    Returns:
        One of ``"active"``, ``"stale"``, or ``"archive-candidate"``.
    """
    date_str = fm.get("updated_at") or fm.get("created_at") or ""
    dt = _parse_iso_date(date_str) if date_str else None
    if dt is None:
        return "stale"
    now = datetime.now(UTC)
    age_days = (now - dt).days
    if age_days < ACTIVE_DAYS:
        return "active"
    if age_days < STALE_DAYS:
        return "stale"
    return "archive-candidate"


async def _collect_agent_skills(
    skills_dir: anyio.Path,
) -> list[dict[str, Any]]:
    """Scan skills/ and return metadata for agent-created skills only.

    Args:
        skills_dir: Path to workspace/skills/ directory.

    Returns:
        List of dicts with keys: skill_name, fm, body, skill_md_path, status.
    """
    results: list[dict[str, Any]] = []
    if not await skills_dir.exists():
        return results

    async for skill_path in skills_dir.iterdir():
        if not await skill_path.is_dir():
            continue
        if skill_path.name.startswith("."):
            continue
        skill_md = skill_path / "SKILL.md"
        if not await skill_md.exists():
            continue
        try:
            raw = await skill_md.read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(raw)
            if fm.get("created_by") != "agent":
                continue
            status = _label_status(fm)
            results.append(
                {
                    "skill_name": skill_path.name,
                    "fm": fm,
                    "body": body,
                    "skill_md_path": skill_md,
                    "status": status,
                }
            )
        except Exception as exc:
            logger.debug("Curator: could not read skill %s: %s", skill_path.name, exc)

    return results


def _build_skills_summary(skills: list[dict[str, Any]]) -> str:
    """Format skill metadata into a human-readable summary for the LLM prompt.

    Args:
        skills: List of skill metadata dicts from ``_collect_agent_skills``.

    Returns:
        Formatted multi-line string.
    """
    lines: list[str] = []
    for s in skills:
        fm = s["fm"]
        name = s["skill_name"]
        desc = fm.get("description", "(no description)")
        cat = fm.get("category", "general")
        status = s["status"]
        updated = fm.get("updated_at") or fm.get("created_at") or "unknown"
        lines.append(
            f"- skill_name: {name}\n"
            f"  description: {desc}\n"
            f"  category: {cat}\n"
            f"  status: {status}\n"
            f"  last_updated: {updated}"
        )
    return "\n\n".join(lines)


async def _atomic_write(path: anyio.Path, content: str) -> None:
    """Write content to path atomically via a temp file + rename.

    Args:
        path: Destination file path.
        content: Text content to write.
    """
    tmp = path.parent / (path.name + ".tmp")
    await tmp.write_text(content, encoding="utf-8")
    await tmp.rename(path)


async def _apply_patch(skill: dict[str, Any], patch_content: str) -> str:
    """Patch a skill's body content, updating updated_at in frontmatter.

    Args:
        skill: Skill metadata dict.
        patch_content: New body text.

    Returns:
        Status message.
    """
    fm = dict(skill["fm"])
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    fm["updated_at"] = now
    fm_lines = ["---"] + [f"{k}: {v}" for k, v in fm.items()] + ["---"]
    new_content = "\n".join(fm_lines) + "\n\n" + patch_content
    await _atomic_write(skill["skill_md_path"], new_content)
    return f"patched {skill['skill_name']!r}"


async def _apply_archive(skill: dict[str, Any], archived_dir: anyio.Path) -> str:
    """Move a skill directory to .archived/.

    Args:
        skill: Skill metadata dict.
        archived_dir: Path to workspace/skills/.archived/.

    Returns:
        Status message.
    """
    src = skill["skill_md_path"].parent
    await archived_dir.mkdir(parents=True, exist_ok=True)
    dst = archived_dir / skill["skill_name"]
    if await dst.exists():
        suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        dst = archived_dir / f"{skill['skill_name']}-{suffix}"
    await src.rename(dst)
    return f"archived {skill['skill_name']!r}"


async def _apply_merge(
    source_skill: dict[str, Any],
    target_skill: dict[str, Any] | None,
    archived_dir: anyio.Path,
) -> str:
    """Merge source skill into target, then archive source.

    Args:
        source_skill: Skill to merge from and archive.
        target_skill: Skill to merge into (appends source body).
        archived_dir: Path to workspace/skills/.archived/.

    Returns:
        Status message.
    """
    if target_skill is None:
        return f"merge skipped: target not found for {source_skill['skill_name']!r}"

    # Append source body to target
    target_raw = await target_skill["skill_md_path"].read_text(encoding="utf-8")
    source_body = source_skill["body"]
    if source_body.strip():
        sep = f"\n\n<!-- merged from {source_skill['skill_name']} -->\n\n"
        new_target = target_raw.rstrip() + sep + source_body
        await _atomic_write(target_skill["skill_md_path"], new_target)

    # Archive source
    await _apply_archive(source_skill, archived_dir)
    return f"merged {source_skill['skill_name']!r} into {target_skill['skill_name']!r}"


# ---------------------------------------------------------------------------
# Curator state (last_run_at persistence)
# ---------------------------------------------------------------------------


async def _load_curator_state(skills_dir: anyio.Path) -> dict[str, str]:
    """Load curator state from .curator_state.json.

    Args:
        skills_dir: workspace/skills/ directory path.

    Returns:
        State dict with keys like ``last_run_at``.
    """
    path = skills_dir / CURATOR_STATE_FILENAME
    if not await path.exists():
        return {}
    try:
        raw = await path.read_text(encoding="utf-8")
        return _json.loads(raw)
    except Exception:
        return {}


async def _save_curator_state(skills_dir: anyio.Path, state: dict[str, str]) -> None:
    """Persist curator state to .curator_state.json.

    Args:
        skills_dir: workspace/skills/ directory path.
        state: State dict to persist.
    """
    try:
        await skills_dir.mkdir(parents=True, exist_ok=True)
        path = skills_dir / CURATOR_STATE_FILENAME
        tmp = path.parent / (path.name + ".tmp")
        await tmp.write_text(_json.dumps(state, ensure_ascii=False), encoding="utf-8")
        await tmp.rename(path)
    except Exception as exc:
        logger.debug("Curator: failed to save state: %s", exc)


async def should_run_now(
    skills_dir: anyio.Path,
    interval_hours: float = DEFAULT_INTERVAL_HOURS,
) -> bool:
    """Return True if the curator is due for a run.

    On first call (no ``last_run_at``), seeds the state with the current time
    and returns False — deferring the first real pass by one full interval,
    matching hermes-agent behaviour.

    Args:
        skills_dir: workspace/skills/ directory path.
        interval_hours: Minimum hours between curator runs.

    Returns:
        True if enough time has elapsed since last run.
    """
    from datetime import timedelta

    state = await _load_curator_state(skills_dir)
    last_str = state.get("last_run_at", "")
    last = _parse_iso_date(last_str) if last_str else None

    now = datetime.now(UTC)
    if last is None:
        # First run: seed and defer
        state["last_run_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        state["last_run_summary"] = "deferred first run — will run after one interval"
        await _save_curator_state(skills_dir, state)
        return False

    return (now - last) >= timedelta(hours=interval_hours)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def maybe_run_curator(
    workspace_dir: anyio.Path | str,
    complete_fn: CompleteFn,
    idle_for_seconds: float | None = None,
    interval_hours: float = DEFAULT_INTERVAL_HOURS,
    min_idle_hours: float = DEFAULT_MIN_IDLE_HOURS,
) -> str | None:
    """Run curator if all gates pass. Returns summary or None if skipped.

    Gates (both must pass, matching hermes-agent behaviour):
    1. ``should_run_now()`` — enough time has elapsed since last run
    2. ``idle_for_seconds >= min_idle_hours * 3600`` — agent is idle

    The idle gate is only enforced when ``idle_for_seconds`` is provided.
    Pass ``float('inf')`` to signal "fully idle" (e.g. on startup).

    Never raises; all failures return None.

    Args:
        workspace_dir: Workspace root path.
        complete_fn: LLM call function.
        idle_for_seconds: How long the agent has been idle in seconds.
            If None, idle gate is skipped.
        interval_hours: Hours between curator runs (default 168 = 7 days).
        min_idle_hours: Minimum idle hours required (default 2).

    Returns:
        Summary string if curator ran, None if skipped or failed.
    """
    try:
        ws = anyio.Path(str(workspace_dir))
        skills_dir = ws / "skills"

        if not await should_run_now(skills_dir, interval_hours):
            return None

        if idle_for_seconds is not None:
            min_idle_s = min_idle_hours * 3600.0
            if idle_for_seconds < min_idle_s:
                logger.debug(
                    "Curator: skipped — idle_for_seconds=%.0f < %.0f",
                    idle_for_seconds,
                    min_idle_s,
                )
                return None

        return await run_curator(workspace_dir, complete_fn, interval_hours=interval_hours)
    except Exception as exc:
        logger.debug("maybe_run_curator failed: %s", exc, exc_info=True)
        return None


async def run_curator(
    workspace_dir: anyio.Path | str,
    complete_fn: CompleteFn,
    interval_hours: float = DEFAULT_INTERVAL_HOURS,
) -> str:
    """Run the skill library curator unconditionally.

    Scans agent-created skills, applies time-based status labels, then
    calls ``complete_fn`` for semantic review, and executes recommended
    operations (keep/patch/merge/archive).

    Updates ``last_run_at`` in curator state on completion.
    All exceptions are caught; failures return an error summary string
    without raising.

    Args:
        workspace_dir: Path to the workspace root directory.
        complete_fn: Async function for single-turn LLM conversation.
            Signature: ``async (messages: list[dict]) -> str``
            Should return the assistant's reply as a plain string.
        interval_hours: Passed through for state reference only.

    Returns:
        Operation summary string (also written to
        ``workspace/skills/.curator_report.md``).
    """
    ws = anyio.Path(str(workspace_dir))
    skills_dir = ws / "skills"
    archived_dir = skills_dir / ".archived"

    try:
        skills = await _collect_agent_skills(skills_dir)
    except Exception as exc:
        msg = f"Curator: failed to scan skills: {exc}"
        logger.error(msg)
        return msg

    if not skills:
        summary = "Curator: no agent-created skills found. Nothing to do."
        logger.info(summary)
        await _write_report(skills_dir, summary)
        return summary

    skills_summary = _build_skills_summary(skills)
    prompt = _CURATOR_PROMPT_TEMPLATE.format(skills_summary=skills_summary)

    # Call LLM for semantic review
    try:
        reply = await complete_fn([{"role": "user", "content": prompt}])
    except Exception as exc:
        msg = f"Curator: LLM call failed: {exc}"
        logger.error(msg)
        await _write_report(skills_dir, msg)
        return msg

    # Parse JSON response
    try:
        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", reply).strip()
        decisions: list[dict[str, Any]] = __import__("json").loads(cleaned)
    except Exception as exc:
        msg = f"Curator: failed to parse LLM response as JSON: {exc}\nRaw reply: {reply[:500]}"
        logger.error(msg)
        await _write_report(skills_dir, msg)
        return msg

    # Build lookup map for merge targets
    skill_map = {s["skill_name"]: s for s in skills}

    # Execute decisions
    counts: dict[str, int] = {"keep": 0, "patch": 0, "merge": 0, "archive": 0, "error": 0}
    action_log: list[str] = []

    for decision in decisions:
        skill_name = decision.get("skill_name", "")
        action = decision.get("action", "keep")
        reason = decision.get("reason", "")
        skill = skill_map.get(skill_name)

        if skill is None:
            logger.debug("Curator: unknown skill in LLM response: %r", skill_name)
            counts["error"] += 1
            continue

        try:
            if action == "keep":
                counts["keep"] += 1
                action_log.append(f"keep    {skill_name}: {reason}")

            elif action == "patch":
                patch_content = decision.get("patch_content") or ""
                result = await _apply_patch(skill, patch_content)
                counts["patch"] += 1
                action_log.append(f"patch   {result}: {reason}")

            elif action == "merge":
                merge_into = decision.get("merge_into") or ""
                target = skill_map.get(merge_into)
                result = await _apply_merge(skill, target, archived_dir)
                counts["merge"] += 1
                action_log.append(f"merge   {result}: {reason}")

            elif action == "archive":
                result = await _apply_archive(skill, archived_dir)
                counts["archive"] += 1
                action_log.append(f"archive {result}: {reason}")

            else:
                logger.debug("Curator: unknown action %r for skill %r", action, skill_name)
                counts["error"] += 1

        except Exception as exc:
            logger.error("Curator: error executing %r on %r: %s", action, skill_name, exc)
            counts["error"] += 1
            action_log.append(f"error   {skill_name}: {exc}")

    # Build summary
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# Curator Report — {now_str}",
        "",
        f"Skills scanned: {len(skills)}",
        f"keep: {counts['keep']}  patch: {counts['patch']}  "
        f"merge: {counts['merge']}  archive: {counts['archive']}  "
        f"error: {counts['error']}",
        "",
        "## Actions",
        "",
    ] + action_log

    summary = "\n".join(lines)
    await _write_report(skills_dir, summary)

    # Persist last_run_at so should_run_now() defers the next pass
    state = await _load_curator_state(skills_dir)
    state["last_run_at"] = now_str
    state["last_run_summary"] = (
        f"keep={counts['keep']} patch={counts['patch']} "
        f"merge={counts['merge']} archive={counts['archive']} error={counts['error']}"
    )
    await _save_curator_state(skills_dir, state)

    logger.info(
        "Curator: done. keep=%d patch=%d merge=%d archive=%d error=%d",
        counts["keep"],
        counts["patch"],
        counts["merge"],
        counts["archive"],
        counts["error"],
    )
    return summary


async def _write_report(skills_dir: anyio.Path, content: str) -> None:
    """Write curator report to .curator_report.md.

    Args:
        skills_dir: workspace/skills/ directory path.
        content: Report content to write.
    """
    try:
        report_path = skills_dir / ".curator_report.md"
        await skills_dir.mkdir(parents=True, exist_ok=True)
        await _atomic_write(report_path, content)
    except Exception as exc:
        logger.debug("Curator: failed to write report: %s", exc)
