"""Run a child process while keeping whatever it printed before it was cut off.

``anyio.run_process`` collects output by waiting for the process to exit, so a
``fail_after`` around it discards everything the command already produced. In
practice that is the worst moment to lose output: the agent gets one line
saying "timed out" with no indication of *where* it stalled, so the only move
available to it is to assume the limit was too low and run the same command
again with a bigger one. Real sessions show exactly that — the same command
retried at 60s, 90s, 100s, 120s and 180s, five failures that between them
carried no new information.

So read the pipes concurrently as the process runs and keep the bytes. On
timeout the partial output is reported alongside the limit that was hit, which
is usually enough to see which step never finished.
"""

from __future__ import annotations

import os
import signal
from contextlib import suppress
from dataclasses import dataclass, field

import anyio
import anyio.abc


@dataclass
class ProcResult:
    """Outcome of a run, including output captured before a timeout."""

    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    elapsed: float
    # Set when the process survived the kill signal; its output may still grow.
    orphaned: bool = False
    _unused: dict[str, str] = field(default_factory=dict, repr=False)


async def _drain(stream: anyio.abc.ByteReceiveStream | None, sink: bytearray) -> None:
    """Accumulate *stream* into *sink* until it closes."""
    if stream is None:
        return
    try:
        async for chunk in stream:
            sink.extend(chunk)
    except anyio.EndOfStream:
        pass
    except anyio.ClosedResourceError:
        # The process was killed on timeout while we were mid-read; whatever
        # made it into the sink is still valid and is what we report.
        pass


async def _terminate(process: anyio.abc.Process) -> bool:
    """Stop *process*, escalating to kill. Returns True if it outlived both.

    On POSIX the child is a process *group* leader (``start_new_session``), so
    the whole group is signalled — a shell that spawned children would
    otherwise leave them running and holding the pipes open. Windows has no
    process groups here, so ``terminate()`` is the best available.
    """
    if os.name != "nt":
        with anyio.CancelScope(shield=True):
            for sig, grace in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
                with suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(os.getpgid(process.pid), sig)
                with anyio.move_on_after(grace):
                    await process.wait()
                    return False
        return True

    with anyio.CancelScope(shield=True):
        with suppress(ProcessLookupError, OSError):
            process.terminate()
        with anyio.move_on_after(2.0):
            await process.wait()
            return False
        with suppress(ProcessLookupError, OSError):
            process.kill()
        with anyio.move_on_after(2.0):
            await process.wait()
            return False
    return True


async def run_capturing(
    command: list[str],
    *,
    timeout_seconds: float,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> ProcResult:
    """Run *command*, returning output even if the timeout fires.

    Args:
        command: argv list — never a shell string, so nothing is re-parsed.
        timeout_seconds: Wall-clock limit before the process is stopped.
        env: Full environment for the child.
        cwd: Working directory for the child.

    Returns:
        A ``ProcResult``; on timeout ``timed_out`` is set, ``returncode`` is
        whatever the kill produced (``None`` if it never died), and the output
        fields hold everything read up to that point.
    """
    out = bytearray()
    err = bytearray()
    started = anyio.current_time()
    timed_out = False
    orphaned = False
    returncode: int | None = None

    kwargs: dict[str, object] = {}
    if os.name != "nt":
        # Group leader so the whole tree can be signalled on timeout.
        kwargs["start_new_session"] = True

    process = await anyio.open_process(command, env=env, cwd=cwd, **kwargs)  # ty: ignore[invalid-argument-type]
    try:
        with anyio.move_on_after(timeout_seconds) as scope:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_drain, process.stdout, out)
                tg.start_soon(_drain, process.stderr, err)
                returncode = await process.wait()
        timed_out = bool(scope.cancelled_caught)
    finally:
        if process.returncode is None:
            orphaned = await _terminate(process)
        if process.returncode is not None:
            returncode = process.returncode
        # Closing releases the pipe handles; the bytes already read are ours.
        with anyio.CancelScope(shield=True):
            await process.aclose()

    return ProcResult(
        stdout=out.decode("utf-8", errors="replace"),
        stderr=err.decode("utf-8", errors="replace"),
        returncode=returncode,
        timed_out=timed_out,
        elapsed=anyio.current_time() - started,
        orphaned=orphaned,
    )
