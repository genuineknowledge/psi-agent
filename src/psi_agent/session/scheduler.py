from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import anyio
from croniter import croniter
from loguru import logger

from psi_agent._yaml import parse_yaml_header


@dataclass
class Schedule:
    name: str
    cron: str
    task_content: str
    _cron_iter: croniter = field(init=False)
    _last_run: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self._cron_iter = croniter(self.cron, time.time())
        self._last_run = time.time()

    def get_next_run(self) -> float:
        return self._cron_iter.get_next()

    def get_prev_run(self) -> float:
        return self._cron_iter.get_prev()

    def should_run_now(self) -> bool:
        now = time.time()
        self._cron_iter = croniter(self.cron, now)
        prev_time = self._cron_iter.get_prev()
        self._cron_iter = croniter(self.cron, now)
        return prev_time > self._last_run

    def mark_run(self) -> None:
        self._last_run = time.time()

    def to_user_message(self) -> dict:
        return {
            "role": "user",
            "content": f"[Schedule Task: {self.name}]\n\n{self.task_content}",
        }


async def load_schedules_from_workspace(schedules_dir: Path) -> list[Schedule]:
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

        schedule = Schedule(name=str(name), cron=str(cron), task_content=body.strip())
        schedules.append(schedule)
        logger.info(f"Loaded schedule: {name} (cron: {cron})")

    logger.info(f"Loaded {len(schedules)} schedule(s) from {schedules_dir}")
    return schedules
