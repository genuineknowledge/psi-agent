"""Scheduled tasks — data model, runner coroutine, and registry.

Tools are stored per-file internally via ``ScheduleEntry``, which
carries the hash and the ``Schedule`` for a single ``TASK.md`` file.
The public ``schedules`` list remains flat for backward compatibility.
"""

from __future__ import annotations

import hashlib
import os
import time
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
from croniter import croniter
from loguru import logger

from psi_agent._yaml import parse_yaml_header
from psi_agent.session.history_display import (
    KIND_SCHEDULE_DISPLAY,
    KIND_SCHEDULE_SILENT,
    with_kind,
)
from psi_agent.session.protocol import AgentChunk

if TYPE_CHECKING:
    from psi_agent.session.agent import SessionAgent


@dataclass
class Schedule:
    """A scheduled task loaded from workspace/schedules/*/TASK.md."""

    name: str
    cron: str
    task_content: str
    # Finalized protocol: display | silent (default display for backward compat).
    visibility: str = "display"


# ── ScheduleEntry — per-file storage unit ─────────────────────────────────────


@dataclass
class ScheduleEntry:
    """Per-file schedule storage — hash, schedule data, and import status.

    ``fresh`` is ``True`` when the file was actually parsed during
    this refresh round; ``False`` when the entry was copied from a
    previous state (hash matched, file skipped).
    """

    file_hash: str
    schedule: Schedule
    fresh: bool = False


# ── ScheduleRegistry — loading, state, incremental refresh ───────────────────


class ScheduleRegistry:
    """Owns the schedule list and its runtime lifecycle.

    Schedules are stored per-file as ``{file_path: ScheduleEntry}``.
    Each schedule gets a ``CancelScope`` for per-schedule cancellation
    on update or removal.
    """

    def __init__(self, *, files: dict[str, ScheduleEntry] | None = None, work_dir: Path | None = None) -> None:
        self._files: dict[str, ScheduleEntry] = dict(files or {})
        self._work_dir = work_dir
        self._agent: SessionAgent | None = None
        self._task_group: Any = None
        self._runner_scopes: dict[str, anyio.CancelScope] = {}

    @property
    def schedules(self) -> list[Schedule]:
        """Flat list of all registered schedules."""
        return [entry.schedule for entry in self._files.values()]

    # -- factory ----------------------------------------------------------------

    @classmethod
    async def load(cls, schedules_dir: Path) -> ScheduleRegistry:
        """Full initial load — scan *schedules_dir*."""
        files = await cls._load_from_dir(schedules_dir)
        return cls(files=files, work_dir=schedules_dir)

    # -- runner lifecycle -------------------------------------------------------

    def start_all(self, task_group: Any, agent: SessionAgent) -> None:
        """Start a runner for every registered schedule in *task_group*.
        Stores *agent* and *task_group* for use by ``refresh()``."""
        self._agent = agent
        self._task_group = task_group
        for entry in self._files.values():
            self._start_runner(entry.schedule)

    async def refresh(self) -> dict[str, str]:
        """Incremental reload — adds, updates, removes schedules.

        Returns a dict mapping schedule name to ``'added'``,
        ``'updated'``, ``'removed'``, or ``'skipped'``.  Errors are
        caught and logged; the caller always gets a dict back (empty on
        failure).
        """
        try:
            return await self._do_refresh()
        except Exception:
            logger.warning("Failed to refresh schedules")
            return {}

    async def _do_refresh(self) -> dict[str, str]:
        if self._work_dir is None:
            logger.warning("No work_dir set, cannot refresh schedules")
            return {}
        if self._task_group is None:
            logger.warning("No task group set, cannot start/restart runners")
            return {}

        logger.debug("Starting schedule refresh")
        new_files = await self._load_from_dir(self._work_dir, self._files)
        result: dict[str, str] = {}

        # removed — files in old but not on disk any more
        for path in list(self._files):
            if path not in new_files:
                name = self._files[path].schedule.name
                self._cancel_runner(name)
                result[name] = "removed"
                del self._files[path]

        # added / updated / skipped — per file
        for path, new_entry in new_files.items():
            old_entry = self._files.get(path)
            name = new_entry.schedule.name
            if old_entry is None:
                self._start_runner(new_entry.schedule)
                result[name] = "added"
                self._files[path] = new_entry
            elif not new_entry.fresh:
                result[name] = "skipped"
            else:
                self._cancel_runner(name)
                self._start_runner(new_entry.schedule)
                result[name] = "updated"
                self._files[path] = new_entry

        logger.info(f"Schedule refresh complete: {result or 'no changes'}")
        return result

    # -- runner management ------------------------------------------------------

    def _start_runner(self, schedule: Schedule) -> None:
        """Start a perpetual runner coroutine for *schedule*."""
        cancel_scope = anyio.CancelScope()
        self._runner_scopes[schedule.name] = cancel_scope
        self._task_group.start_soon(self._run_one, schedule, self._agent, cancel_scope)

    def _cancel_runner(self, name: str) -> None:
        """Cancel a running schedule by name, removing its scope."""
        scope = self._runner_scopes.pop(name, None)
        if scope is not None:
            scope.cancel()

    # -- runner coroutine (perpetual) -------------------------------------------

    @staticmethod
    def _schedule_tz() -> ZoneInfo | None:
        """Resolve the timezone cron schedules are anchored to.

        Reads the standard ``TZ`` env var, e.g. ``Asia/Shanghai``. Returns
        ``None`` when unset or invalid; the caller then falls back to the
        system's local timezone via ``astimezone()``, so no IANA data
        package (tzdata) is strictly required.
        """
        name = os.environ.get("TZ", "").strip()
        if not name:
            return None
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as e:
            logger.warning(f"Invalid TZ {name!r}, falling back to system local time: {e!r}")
            return None

    @staticmethod
    async def _run_one(schedule: Schedule, agent: SessionAgent, cancel_scope: anyio.CancelScope) -> None:
        """Perpetual coroutine that fires a schedule on its cron interval."""
        logger.info(f"Schedule runner started: {schedule.name!r} ({schedule.cron!r})")

        # Anchor cron to TZ so "0 9 * * *" means 9am *local* time, not 9am
        # UTC. When TZ is unset/invalid, fall back to the system's local
        # timezone via astimezone() — still tz-aware, so cron stays local.
        tz = ScheduleRegistry._schedule_tz()
        base = datetime.now(tz) if tz is not None else datetime.now().astimezone()
        cron_iter = croniter(schedule.cron, base)

        try:
            with cancel_scope:
                while True:
                    try:
                        next_run = cron_iter.get_next(float)
                        wait = max(0.0, next_run - time.time())
                        await anyio.sleep(wait)

                        logger.info(f"Schedule triggered: {schedule.name!r}")
                        user_msg = with_kind(
                            {"role": "user", "content": schedule.task_content},
                            KIND_SCHEDULE_SILENT,
                        )
                        response_kind = (
                            KIND_SCHEDULE_DISPLAY if schedule.visibility == "display" else KIND_SCHEDULE_SILENT
                        )

                        async with agent._lock:
                            pending_chunks: list[AgentChunk] = []
                            async with aclosing(agent.run(user_msg, response_kind=response_kind)) as chunks:
                                async for chunk in chunks:
                                    pending_chunks.append(chunk)
                                    logger.debug(
                                        f"Schedule chunk: content={chunk.content!r}, reasoning={chunk.reasoning!r}"
                                    )
                                # silent → never push into the next Channel turn
                                if schedule.visibility == "display" and pending_chunks:
                                    agent.set_pending_schedule_chunks(pending_chunks)
                                    logger.info(
                                        f"Schedule {schedule.name!r} response stored "
                                        f"({len(pending_chunks)} chunks, visibility=display)"
                                    )
                                else:
                                    logger.info(
                                        f"Schedule {schedule.name!r} completed "
                                        f"(visibility={schedule.visibility!r}, "
                                        f"chunks={len(pending_chunks)}, not pending)"
                                    )
                    except Exception as e:
                        logger.error(f"Error processing schedule {schedule.name!r}: {e!r}")
        finally:
            logger.info(f"Schedule runner stopped: {schedule.name!r}")

    # -- disk loading -----------------------------------------------------------

    @staticmethod
    async def _load_from_dir(
        schedules_dir: Path,
        old_files: dict[str, ScheduleEntry] | None = None,
    ) -> dict[str, ScheduleEntry]:
        """Scan and parse all schedule ``TASK.md`` files.

        If *old_files* is provided, files whose hash matches the stored
        value are preserved (copied from *old_files* with ``fresh=False``)
        instead of re-parsed.

        Returns ``{file_path: ScheduleEntry}`` for all current files.
        """
        files: dict[str, ScheduleEntry] = {}
        sched_anyio = anyio.Path(str(schedules_dir))

        try:
            sched_dir_exists = await sched_anyio.is_dir()
        except Exception as e:
            logger.warning(f"Cannot access schedules directory {schedules_dir!r}: {e!r}")
            return files
        if not sched_dir_exists:
            logger.warning(f"Schedules directory not found: {schedules_dir!r}")
            return files

        async for task_dir in sched_anyio.iterdir():
            try:
                task_dir_anyio = anyio.Path(str(task_dir))
                if not await task_dir_anyio.is_dir():
                    continue
                task_file = task_dir_anyio / "TASK.md"
                if not await task_file.exists():
                    continue

                content = await task_file.read_text(encoding="utf-8")
                file_hash = hashlib.sha256(content.encode()).hexdigest()
                str_path = str(task_file)

                if old_files is not None and str_path in old_files and old_files[str_path].file_hash == file_hash:
                    logger.debug(f"Skipping unchanged file: {task_file!r}")
                    old = old_files[str_path]
                    files[str_path] = ScheduleEntry(file_hash=old.file_hash, schedule=old.schedule, fresh=False)
                    continue

                header, body = parse_yaml_header(content)
                if header is None:
                    logger.warning(f"No valid YAML header in {task_file!r}, skipping")
                    continue

                name = header.get("name")
                cron = header.get("cron")
                if not name or not cron:
                    logger.warning(f"Missing 'name' or 'cron' in {task_file!r} header, skipping")
                    continue

                try:
                    croniter(cron)
                except (ValueError, Exception) as e:
                    logger.error(f"Invalid cron expression for schedule {name!r}: {e!r}")
                    continue

                raw_visibility = header.get("visibility", "display")
                visibility = str(raw_visibility).strip().casefold() if isinstance(raw_visibility, str) else "display"
                if visibility not in {"display", "silent"}:
                    logger.warning(f"Invalid visibility {raw_visibility!r} in {task_file!r}, defaulting to 'display'")
                    visibility = "display"

                schedule = Schedule(
                    name=str(name),
                    cron=str(cron),
                    task_content=body.strip(),
                    visibility=visibility,
                )
                files[str_path] = ScheduleEntry(file_hash=file_hash, schedule=schedule, fresh=True)
                logger.debug(f"Loaded schedule: {name!r} (cron: {cron!r}, visibility: {visibility!r})")
            except Exception as e:
                logger.error(f"Failed to load schedule from {task_dir!r}: {e!r}")
                continue

        logger.info(f"Loaded {len(files)} schedule(s) from {schedules_dir!r}")
        return files
