"""Scheduled tasks — data model, runner coroutine, and registry.

Tools are stored per-file internally via ``ScheduleEntry``, which
carries the hash and the ``Schedule`` for a single ``TASK.md`` file.
The public ``schedules`` list remains flat for backward compatibility.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
from croniter import croniter
from loguru import logger

from psi_agent._yaml import parse_yaml_header
from psi_agent.protocol import (
    REASONING_KIND_TOOL_CALL,
    REASONING_KIND_TOOL_RESULT,
)
from psi_agent.session.history_display import (
    KIND_SCHEDULE_DISPLAY,
    KIND_SCHEDULE_SILENT,
    with_kind,
)
from psi_agent.session.protocol import AgentChunk

if TYPE_CHECKING:
    from psi_agent.session.agent import SessionAgent

# fire: prompt (default) — inject TASK body as user message, LLM may call tools.
# fire: tool — Session invokes tool_name(**tool_args) directly (no LLM).
FIRE_PROMPT = "prompt"
FIRE_TOOL = "tool"

# Interval at which a scheduler Session rescans the schedules directory. 30s: the
# most a user waits for a freshly created reminder to take effect, while refresh()
# is hash-incremental so an idle cycle costs one directory stat.
_WATCH_INTERVAL_SECONDS = 30.0

# Wildcard in either activation list: this Session activates *every* schedule
# under the workspace.
ACTIVATE_ALL = "*"


@dataclass
class Schedule:
    """A scheduled task loaded from workspace/schedules/*/TASK.md."""

    name: str
    cron: str
    task_content: str
    # Finalized protocol: display | silent (default display for backward compat).
    visibility: str = "display"
    # When True, delete TASK.md and stop the runner after one successful fire.
    run_once: bool = False
    # Absolute path to TASK.md (set on load) — used for run_once cleanup.
    task_path: str = ""
    # prompt = LLM turn on task_content; tool = direct ToolRegistry call (刻意为之).
    fire: str = FIRE_PROMPT
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)


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

    **Activation is a property of (session x schedule), not of a session
    (刻意为之)**: the schedules directory belongs to the *workspace*, so every
    Session reads every entry (the ``schedules`` property and ``refresh()``'s
    add/update/remove counts are unaffected by activation), but **whether a
    runner starts** is decided per entry by the lists — two Sessions on the same
    workspace may activate disjoint subsets.

    Why per-entry rather than one boolean per Session: activation answers "which
    Session owns this task", and a single switch can only say "fire all / fire
    none" — it cannot express "entry A belongs to the scheduler Session, entry B
    to some user session". Feishu spawns one Session per ``open_id``, so a
    schedule must be activated by **exactly one** Session or the reminder gets
    multiplied by the number of live sessions.

    Why a blacklist on top of the whitelist: the whitelist is an **enumeration**,
    covering only the entries that exist at startup — a ``TASK.md`` discovered
    later by ``_watch_dir`` / ``refresh()`` is not in the list and would never
    fire. Expressing "everything except these few" (the scheduler Session's
    normal case) requires ``active_names={ACTIVATE_ALL}`` +
    ``deactive_names={excluded}``: the wildcard activates new entries
    automatically, the blacklist carves out the ones assigned elsewhere.

    List semantics: ``deactive_names`` wins (containing ``ACTIVATE_ALL`` means
    activate nothing); ``active_names`` of ``None`` / empty set activates nothing
    (the default for user Sessions — ``start_all`` starts no runner);
    ``{ACTIVATE_ALL}`` activates everything except the blacklist (the scheduler
    Session's default); a named set activates only those ``schedule.name`` s.
    """

    def __init__(
        self,
        *,
        files: dict[str, ScheduleEntry] | None = None,
        work_dir: Path | None = None,
        active_names: set[str] | None = None,
        deactive_names: set[str] | None = None,
    ) -> None:
        self._files: dict[str, ScheduleEntry] = dict(files or {})
        self._work_dir = work_dir
        self._active_names: set[str] = set(active_names or ())
        self._deactive_names: set[str] = set(deactive_names or ())
        self._agent: SessionAgent | None = None
        self._task_group: Any = None
        self._runner_scopes: dict[str, anyio.CancelScope] = {}

    @property
    def schedules(self) -> list[Schedule]:
        """Flat list of all registered schedules (activated or not)."""
        return [entry.schedule for entry in self._files.values()]

    @property
    def active_schedules(self) -> list[Schedule]:
        """Schedules this Session actually fires — those passing the lists."""
        return [s for s in self.schedules if self.is_active(s.name)]

    def is_active(self, name: str) -> bool:
        """Whether this Session fires the schedule named *name*.

        The blacklist wins over the whitelist; ``ACTIVATE_ALL`` means "every
        schedule" in either list.
        """
        if ACTIVATE_ALL in self._deactive_names or name in self._deactive_names:
            return False
        if ACTIVATE_ALL in self._active_names:
            return True
        return name in self._active_names

    # -- factory ----------------------------------------------------------------

    @classmethod
    async def load(
        cls,
        schedules_dir: Path,
        *,
        active_names: set[str] | None = None,
        deactive_names: set[str] | None = None,
    ) -> ScheduleRegistry:
        """Full initial load — scan *schedules_dir*.

        *active_names* / *deactive_names* decide which entries this Session fires
        (none by default — see the class docstring).
        """
        files = await cls._load_from_dir(schedules_dir)
        return cls(
            files=files,
            work_dir=schedules_dir,
            active_names=active_names,
            deactive_names=deactive_names,
        )

    # -- runner lifecycle -------------------------------------------------------

    def start_all(self, task_group: Any, agent: SessionAgent) -> None:
        """Start a runner for every **activated** schedule in *task_group*.

        Stores *agent* and *task_group* for use by ``refresh()``; when the
        whitelist is non-empty, also starts a perpetual ``_watch_dir`` coroutine
        that calls ``refresh()`` periodically. Entries that are not activated
        stay in ``schedules`` (readable, refreshable) but get no runner.

        Empty whitelist means nothing can ever be activated (the blacklist only
        subtracts), so no watcher is started and no directory is scanned in vain.
        """
        self._agent = agent
        self._task_group = task_group
        for entry in self._files.values():
            self._start_runner(entry.schedule)
        if self._work_dir is not None and self._active_names:
            task_group.start_soon(self._watch_dir)

    async def _watch_dir(self) -> None:
        """Periodic ``refresh()`` — the only way a scheduler Session notices
        ``TASK.md`` additions, edits and removals.

        刻意为之: the other two refresh points both live in ``SessionAgent.run()``
        (turn start, and after ``finish_reason=stop``), but **no channel is
        attached to a scheduler Session**, so it never has a turn. Without this
        watcher a schedule created through ``schedule_manage`` would never be
        loaded — only the ones present at spawn time would ever run.

        Polling rather than inotify/watchdog: ``refresh()`` is already
        hash-incremental (unchanged files are not reparsed), so one directory stat
        is negligible; and it adds no dependency and behaves the same on every
        platform (see the root AGENTS.md "minimal core" principle).

        The in-loop ``except Exception`` mirrors ``_run_one``: this coroutine is
        attached to the Session's task group via ``start_soon``, so any escaping
        exception would take the whole scheduler Session down with it. A single
        failed refresh only logs ERROR and is retried next cycle.
        ``CancelledError`` is a ``BaseException``, is not caught here, and
        propagates as usual.
        """
        logger.info(f"Schedule watcher started for {self._work_dir!r} (every {_WATCH_INTERVAL_SECONDS}s)")
        try:
            while True:
                try:
                    await anyio.sleep(_WATCH_INTERVAL_SECONDS)
                    result = await self.refresh()
                    if result and set(result.values()) != {"skipped"}:
                        logger.info(f"Schedule watcher applied changes: {result}")
                except Exception as e:
                    logger.error(f"Schedule watcher iteration failed for {self._work_dir!r}: {e!r}")
        finally:
            logger.info(f"Schedule watcher stopped for {self._work_dir!r}")

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
        """Start a perpetual runner coroutine for *schedule*, if activated here.

        A no-op when it is not activated (刻意为之): activation is a property of
        (session x schedule), so a non-activated entry stays readable in
        ``schedules`` while the right to fire it belongs to whichever Session did
        activate it.
        """
        if not self.is_active(schedule.name):
            logger.debug(f"Schedule {schedule.name!r} not active in this session; runner not started")
            return
        cancel_scope = anyio.CancelScope()
        self._runner_scopes[schedule.name] = cancel_scope
        self._task_group.start_soon(self._run_one, schedule, self._agent, cancel_scope, self)

    def _cancel_runner(self, name: str) -> None:
        """Cancel a running schedule by name, removing its scope."""
        scope = self._runner_scopes.pop(name, None)
        if scope is not None:
            scope.cancel()

    def _forget_schedule_file(self, task_path: str, name: str) -> None:
        """Drop an entry after run_once deleted its TASK.md (best-effort)."""
        self._files.pop(task_path, None)
        self._runner_scopes.pop(name, None)

    # -- runner coroutine (perpetual) -------------------------------------------

    @staticmethod
    def _schedule_tz() -> ZoneInfo | None:
        """Resolve the timezone cron schedules are anchored to.

        Reads the standard ``TZ`` env var, e.g. ``Asia/Shanghai``. Returns
        ``None`` when unset or invalid; the caller then falls back to
        machine-local wall time via a naive ``datetime.now()``, so no IANA
        data package (tzdata) is strictly required.
        """
        name = os.environ.get("TZ", "").strip()
        if not name:
            return None
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as e:
            logger.warning(f"Invalid TZ {name!r}, falling back to machine-local time: {e!r}")
            return None

    @staticmethod
    def _seconds_until_next(cron: str, *, now: datetime | None = None) -> float:
        """Seconds until the next cron fire in *local* wall time.

        刻意为之: ``once_at`` / TASK ``cron`` fields are local clock values
        (see workspace ``schedule_manage``). Passing a Unix timestamp into
        ``croniter`` makes it treat those fields as UTC, so one-shot reminders
        on non-UTC machines fire hours late (e.g. UTC+8 → +8h).

        When the standard ``TZ`` env var is set, anchor to that zone so cron
        fields mean wall time *there* (e.g. a UTC container with
        ``TZ=Asia/Shanghai`` fires ``0 9 * * *`` at Beijing 9am). When ``TZ``
        is unset/invalid, fall back to the machine's naive local clock —
        croniter reads the naive datetime's fields directly, so behavior is
        unchanged from the default.
        """
        if now is None:
            tz = ScheduleRegistry._schedule_tz()
            now = datetime.now(tz) if tz is not None else datetime.now()
        base = now
        nxt = croniter(cron, base).get_next(datetime)
        return max(0.0, (nxt - base).total_seconds())

    @staticmethod
    async def _run_one(
        schedule: Schedule,
        agent: SessionAgent,
        cancel_scope: anyio.CancelScope,
        registry: ScheduleRegistry,
    ) -> None:
        """Perpetual coroutine that fires a schedule on its cron interval.

        If ``schedule.run_once`` is True, deletes ``TASK.md`` after one successful
        fire and exits (刻意为之: one-shot reminders must not re-fire next year).
        """
        logger.info(f"Schedule runner started: {schedule.name!r} ({schedule.cron!r}, run_once={schedule.run_once})")

        try:
            with cancel_scope:
                while True:
                    try:
                        wait = ScheduleRegistry._seconds_until_next(schedule.cron)
                        logger.debug(f"Schedule {schedule.name!r} sleeping {wait:.1f}s until next fire")
                        await anyio.sleep(wait)

                        logger.info(f"Schedule triggered: {schedule.name!r}")
                        response_kind = (
                            KIND_SCHEDULE_DISPLAY if schedule.visibility == "display" else KIND_SCHEDULE_SILENT
                        )

                        # ``turn_lock`` rather than the raw lock: a schedule-only
                        # session (the 380k-token attendance task) would never
                        # compact otherwise, and nothing would fail loudly.
                        async with agent.turn_lock():
                            if schedule.fire == FIRE_TOOL:
                                pending_chunks = await ScheduleRegistry._fire_tool(schedule, agent, response_kind)
                            else:
                                pending_chunks = await ScheduleRegistry._fire_prompt(schedule, agent, response_kind)
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
                                    f"fire={schedule.fire!r}, "
                                    f"chunks={len(pending_chunks)}, not pending)"
                                )

                        if schedule.run_once:
                            await ScheduleRegistry._consume_run_once(schedule, registry)
                            break
                    except Exception as e:
                        logger.error(f"Error processing schedule {schedule.name!r}: {e!r}")
        finally:
            logger.info(f"Schedule runner stopped: {schedule.name!r}")

    @staticmethod
    async def _fire_prompt(
        schedule: Schedule,
        agent: SessionAgent,
        response_kind: str,
    ) -> list[AgentChunk]:
        """Default path: TASK body as user message → agent loop (LLM)."""
        user_msg = with_kind(
            {"role": "user", "content": schedule.task_content},
            KIND_SCHEDULE_SILENT,
        )
        pending_chunks: list[AgentChunk] = []
        async with aclosing(agent.run(user_msg, response_kind=response_kind)) as chunks:
            async for chunk in chunks:
                pending_chunks.append(chunk)
                logger.debug(f"Schedule chunk: content={chunk.content!r}, reasoning={chunk.reasoning!r}")
        return pending_chunks

    @staticmethod
    async def _fire_tool(
        schedule: Schedule,
        agent: SessionAgent,
        response_kind: str,
    ) -> list[AgentChunk]:
        """Direct tool invocation — no LLM.

        刻意为之: Feishu reminders and similar must not depend on the model
        voluntarily emitting tool_calls from a TASK prose/pseudocode body.
        """
        await agent.reload_tools()
        tool_name = schedule.tool_name.strip()
        args = dict(schedule.tool_args)
        logger.info(f"Schedule tool fire: {schedule.name!r} → {tool_name!r}({args!r})")

        chunks: list[AgentChunk] = []
        async with agent._conversation:
            agent._conversation.add(
                with_kind(
                    {
                        "role": "user",
                        "content": (
                            f"[schedule tool] {schedule.name}: call {tool_name}"
                            + (f"\n{schedule.task_content}" if schedule.task_content else "")
                        ),
                    },
                    KIND_SCHEDULE_SILENT,
                )
            )
            await agent._conversation.commit()

            func = agent._tool_registry.get(tool_name) if tool_name else None
            if func is None:
                result = f"Error: Tool {tool_name!r} not found"
                logger.error(f"Schedule {schedule.name!r}: {result}")
            else:
                try:
                    raw = await func(**args)
                    result = str(raw)
                    logger.info(f"Schedule tool result ({tool_name!r}): {result[:1000]!r}")
                except Exception as e:
                    result = f"Error executing tool {tool_name!r}: {e}"
                    logger.error(f"Schedule {schedule.name!r} tool error: {e!r}")

            chunks.append(
                AgentChunk(
                    reasoning=f"[Tool Call: {tool_name}({json.dumps(args, ensure_ascii=False)})]",
                    kind=REASONING_KIND_TOOL_CALL,
                )
            )
            chunks.append(
                AgentChunk(
                    reasoning=f"[Tool Result: {result[:1000]}]",
                    kind=REASONING_KIND_TOOL_RESULT,
                )
            )
            if schedule.visibility == "display":
                chunks.append(AgentChunk(content=result[:2000]))

            # Do not invent OpenAI tool_calls rows — they can confuse later AI turns.
            agent._conversation.add(
                with_kind(
                    {
                        "role": "assistant",
                        "content": (
                            f"[schedule tool {tool_name}] {result[:3500]}"
                            if schedule.visibility == "display"
                            else f"[schedule tool {tool_name}] ok"
                        ),
                    },
                    response_kind,
                )
            )
            await agent._conversation.commit()

        return chunks

    @staticmethod
    async def _consume_run_once(schedule: Schedule, registry: ScheduleRegistry) -> None:
        """Delete TASK.md for a run_once schedule after a successful fire."""
        path_str = schedule.task_path
        if not path_str:
            logger.warning(f"run_once schedule {schedule.name!r} has no task_path; cannot delete")
            return
        path = anyio.Path(path_str)
        with anyio.CancelScope(shield=True):
            try:
                if await path.exists():
                    await path.unlink()
                    parent = path.parent
                    with suppress(OSError):
                        await parent.rmdir()
                    logger.info(f"run_once schedule {schedule.name!r} removed {path_str!r}")
                else:
                    logger.warning(f"run_once schedule {schedule.name!r}: {path_str!r} already gone")
            except Exception as e:
                logger.error(f"run_once cleanup failed for {schedule.name!r}: {e!r}")
            finally:
                registry._forget_schedule_file(path_str, schedule.name)

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

                raw_once = header.get("run_once", False)
                if isinstance(raw_once, str):
                    run_once = raw_once.strip().casefold() in {"1", "true", "yes", "on"}
                else:
                    run_once = bool(raw_once)

                raw_fire = header.get("fire", FIRE_PROMPT)
                fire = str(raw_fire).strip().casefold() if isinstance(raw_fire, str) else FIRE_PROMPT
                if fire not in {FIRE_PROMPT, FIRE_TOOL}:
                    logger.warning(f"Invalid fire {raw_fire!r} in {task_file!r}, defaulting to {FIRE_PROMPT!r}")
                    fire = FIRE_PROMPT

                tool_name = ""
                tool_args: dict[str, Any] = {}
                if fire == FIRE_TOOL:
                    raw_tool = header.get("tool") or header.get("tool_name") or ""
                    tool_name = str(raw_tool).strip()
                    raw_args = header.get("tool_args", {})
                    if isinstance(raw_args, dict):
                        tool_args = dict(raw_args)
                    elif isinstance(raw_args, str) and raw_args.strip():
                        try:
                            parsed = json.loads(raw_args)
                        except json.JSONDecodeError as e:
                            logger.error(f"Invalid tool_args JSON for schedule {name!r} in {task_file!r}: {e!r}")
                            continue
                        if not isinstance(parsed, dict):
                            logger.error(
                                f"tool_args for schedule {name!r} must be a JSON object, got {type(parsed).__name__}"
                            )
                            continue
                        tool_args = parsed
                    else:
                        tool_args = {}
                    if not tool_name:
                        logger.error(f"fire=tool schedule {name!r} in {task_file!r} missing 'tool'; skipping")
                        continue

                schedule = Schedule(
                    name=str(name),
                    cron=str(cron),
                    task_content=body.strip(),
                    visibility=visibility,
                    run_once=run_once,
                    task_path=str_path,
                    fire=fire,
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
                files[str_path] = ScheduleEntry(file_hash=file_hash, schedule=schedule, fresh=True)
                logger.debug(
                    f"Loaded schedule: {name!r} (cron: {cron!r}, visibility: {visibility!r}, "
                    f"run_once={run_once}, fire={fire!r}" + (f", tool={tool_name!r}" if fire == FIRE_TOOL else "") + ")"
                )
            except Exception as e:
                logger.error(f"Failed to load schedule from {task_dir!r}: {e!r}")
                continue

        logger.info(f"Loaded {len(files)} schedule(s) from {schedules_dir!r}")
        return files
