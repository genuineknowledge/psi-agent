"""Scheduled tasks — data model, runner coroutine, and registry."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
from croniter import croniter
from loguru import logger

from psi_agent._yaml import parse_yaml_header
from psi_agent.session.protocol import AgentChunk

if TYPE_CHECKING:
    from psi_agent.session.agent import SessionAgent


@dataclass
class Schedule:
    """A scheduled task loaded from workspace/schedules/*/TASK.md."""

    name: str
    cron: str
    task_content: str


class ScheduleRegistry:
    """Owns the schedule list and its runtime lifecycle."""

    def __init__(self, *, schedules: list[Schedule] | None = None, work_dir: Path | None = None):
        self.schedules: list[Schedule] = list(schedules or [])
        self._work_dir = work_dir

    # -- factory ----------------------------------------------------------------

    @classmethod
    async def load(cls, schedules_dir: Path) -> ScheduleRegistry:
        """Full initial load — scan *schedules_dir*."""
        schedules = await cls._load_from_dir(schedules_dir)
        return cls(schedules=schedules, work_dir=schedules_dir)

    # -- runner lifecycle -------------------------------------------------------

    def start_all(self, task_group: Any, agent: SessionAgent) -> None:
        """Start a runner for every registered schedule in *task_group*."""
        for s in self.schedules:
            task_group.start_soon(self._run_one, s, agent)

    async def refresh(self, task_group: Any, agent: SessionAgent) -> list[Schedule]:
        """Incremental reload — start runners for new schedules only."""
        if self._work_dir is None:
            logger.warning("No work_dir set, cannot refresh schedules")
            return []

        new_scheds = await self._load_from_dir(self._work_dir)
        existing = {s.name for s in self.schedules}
        added: list[Schedule] = []
        for s in new_scheds:
            if s.name not in existing:
                self.schedules.append(s)
                task_group.start_soon(self._run_one, s, agent)
                added.append(s)
        if added:
            logger.info(f"Schedule refresh: added {[s.name for s in added]}")
        return added

    # -- runner coroutine (perpetual) -------------------------------------------

    @staticmethod
    async def _run_one(schedule: Schedule, agent: SessionAgent) -> None:
        """Perpetual coroutine that fires a schedule on its cron interval."""
        logger.info(f"Schedule runner started: {schedule.name} ({schedule.cron})")

        cron_iter = croniter(schedule.cron, time.time())

        while True:
            next_run = cron_iter.get_next()
            wait = max(0.0, next_run - time.time())
            await anyio.sleep(wait)

            try:
                logger.info(f"Schedule triggered: {schedule.name}")
                msg = {"role": "user", "content": schedule.task_content}

                async with agent._lock:
                    pending_chunks: list[AgentChunk] = []
                    async for chunk in agent.run(msg):
                        pending_chunks.append(chunk)
                    agent.set_pending_schedule_chunks(pending_chunks)
                    logger.info(f"Schedule {schedule.name} response stored ({len(pending_chunks)} chunks)")
            except Exception as e:
                logger.error(f"Error processing schedule {schedule.name}: {e}")

    # -- disk loading -----------------------------------------------------------

    @staticmethod
    async def _load_from_dir(schedules_dir: Path) -> list[Schedule]:
        schedules: list[Schedule] = []
        sched_anyio = anyio.Path(str(schedules_dir))

        if not await sched_anyio.is_dir():
            logger.warning(f"Schedules directory not found: {schedules_dir}")
            return schedules

        async for task_dir in sched_anyio.iterdir():
            task_dir_anyio = anyio.Path(str(task_dir))
            if not await task_dir_anyio.is_dir():
                continue
            task_file = task_dir_anyio / "TASK.md"
            if not await task_file.exists():
                continue

            content = await task_file.read_text()
            header, body = parse_yaml_header(content)
            if header is None:
                logger.warning(f"No valid YAML header in {task_file}, skipping")
                continue

            name = header.get("name")
            cron = header.get("cron")
            if not name or not cron:
                logger.warning(f"Missing 'name' or 'cron' in {task_file} header, skipping")
                continue

            try:
                croniter(cron)
            except (ValueError, Exception) as e:
                logger.error(f"Invalid cron expression for schedule {name}: {e}")
                continue

            schedule = Schedule(name=str(name), cron=str(cron), task_content=body.strip())
            schedules.append(schedule)
            logger.info(f"Loaded schedule: {name} (cron: {cron})")

        logger.info(f"Loaded {len(schedules)} schedule(s) from {schedules_dir}")
        return schedules
