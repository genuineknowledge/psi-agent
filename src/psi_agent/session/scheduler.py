from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from croniter import croniter
from loguru import logger

from psi_agent._yaml import parse_yaml_header

if TYPE_CHECKING:
    from psi_agent.session.agent import SessionAgent


@dataclass
class Schedule:
    """A scheduled task loaded from workspace/schedules/*/TASK.md."""

    name: str
    cron: str
    task_content: str


async def load_schedules_from_workspace(schedules_dir: Path) -> list[Schedule]:
    """Discover and load all schedules from ``workspace/schedules/*/TASK.md``.

    Each schedule is a ``TASK.md`` file with YAML front matter containing
    ``name`` and ``cron`` fields.  The markdown body becomes the task
    content injected as a user message when the schedule fires.

    Returns a list of ``Schedule`` config objects.  Cron expressions are
    validated at load time — invalid expressions are skipped with an error.

    The caller starts one ``run_one_schedule()`` coroutine per schedule.
    """
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

        # Validate the cron expression before accepting the schedule.
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


async def run_one_schedule(schedule: Schedule, agent: SessionAgent, lock: anyio.Lock) -> None:
    """Perpetual coroutine that fires a schedule on its cron interval.

    Maintains its own croniter — Schedule is pure configuration.
    Each instance runs in its own anyio task, started by the session.

    Flow: compute next cron tick → sleep until then → fire → repeat.
    If a previous run took longer than the interval, the next iteration
    starts immediately (``wait`` is capped at 0).
    """
    logger.info(f"Schedule runner started: {schedule.name} ({schedule.cron})")

    cron_iter = croniter(schedule.cron, time.time())

    while True:
        next_run = cron_iter.get_next()
        wait = max(0.0, next_run - time.time())
        await anyio.sleep(wait)

        try:
            trace_id = uuid.uuid4().hex[:8]
            with logger.contextualize(trace_id=trace_id):
                logger.info(f"Schedule triggered: {schedule.name}")
                msg = {"role": "user", "content": schedule.task_content}

                async with lock:
                    pending_chunks: list = []
                    async for chunk in agent.run(msg, trace_id=trace_id):
                        pending_chunks.append(chunk)
                agent.set_pending_schedule_chunks(pending_chunks)
                logger.info(f"Schedule {schedule.name} response stored ({len(pending_chunks)} chunks)")
        except Exception as e:
            logger.error(f"Error processing schedule {schedule.name}: {e}")
