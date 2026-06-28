from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from psi_agent.session.scheduler import Schedule, load_schedules_from_workspace, run_one_schedule

if TYPE_CHECKING:
    from psi_agent.session.agent import SessionAgent


class ScheduleRegistry:
    """Owns the schedule list and its runtime lifecycle.

    ``schedules`` is public so that ``Session.run()`` can iterate
    initial schedules if needed.
    """

    def __init__(self, *, schedules: list[Schedule] | None = None, work_dir: Path | None = None):
        self.schedules: list[Schedule] = list(schedules or [])
        self._work_dir = work_dir

    @classmethod
    async def load(cls, schedules_dir: Path) -> ScheduleRegistry:
        """Full initial load — scan *schedules_dir*."""
        schedules = await load_schedules_from_workspace(schedules_dir)
        return cls(schedules=schedules, work_dir=schedules_dir)

    def start_all(self, task_group: Any, agent: SessionAgent) -> None:
        """Start a runner for every registered schedule in *task_group*."""
        for s in self.schedules:
            task_group.start_soon(run_one_schedule, s, agent)

    async def refresh(self, task_group: Any, agent: SessionAgent) -> list[Schedule]:
        """Incremental reload — start runners for new schedules only.

        Schedules are de-duplicated by name; already-running schedules
        are not restarted.
        """
        if self._work_dir is None:
            logger.warning("No work_dir set, cannot refresh schedules")
            return []

        new_scheds = await load_schedules_from_workspace(self._work_dir)
        existing = {s.name for s in self.schedules}
        added: list[Schedule] = []
        for s in new_scheds:
            if s.name not in existing:
                self.schedules.append(s)
                task_group.start_soon(run_one_schedule, s, agent)
                added.append(s)
        if added:
            logger.info(f"Schedule refresh: added {[s.name for s in added]}")
        return added
