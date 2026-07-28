from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest

from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry

_WORKSPACE_DIR = Path(__file__).resolve().parents[3]
_RUNNER_PATH = _WORKSPACE_DIR / "tools" / "run_flow.py"
_CLARIFY_PATH = _WORKSPACE_DIR / "tools" / "clarify.py"


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return cast(Any, module)


run_flow_tool = _load_module("fusion_flow_run_flow_tool", _RUNNER_PATH)
clarify_tool = _load_module("fusion_flow_clarify_tool", _CLARIFY_PATH)


class _FakeSendStream:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    async def send(self, item: bytes) -> None:
        self.data.extend(item)

    async def aclose(self) -> None:
        self.closed = True


class _FakeReceiveStream:
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = list(chunks)

    async def receive(self, max_bytes: int = 65536) -> bytes:
        del max_bytes
        if self.chunks:
            return self.chunks.pop(0)
        raise anyio.EndOfStream

    async def aclose(self) -> None:
        return


class _FakeProcess:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", pid: int = 2_000_000_000) -> None:
        self.stdin = _FakeSendStream()
        self.stdout = _FakeReceiveStream(stdout)
        self.stderr = _FakeReceiveStream(stderr)
        self.pid = pid
        self.returncode: int | None = None
        self.killed = False
        self.closed = False

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def aclose(self) -> None:
        self.closed = True


class _BlockingFakeProcess(_FakeProcess):
    def __init__(self) -> None:
        super().__init__()
        self.exited = anyio.Event()

    async def wait(self) -> int:
        await self.exited.wait()
        assert self.returncode is not None
        return self.returncode

    def kill(self) -> None:
        super().kill()
        self.exited.set()


def _test_checkpoint(
    values: dict[str, object],
    *,
    completed_step_ids: tuple[str, ...] = (),
    completed_selection_ids: tuple[str, ...] = (),
) -> Any:
    return run_flow_tool.ExecutionCheckpoint(
        workflow_id="test-workflow",
        plan_digest="0" * 64,
        values=values,
        completed_step_ids=completed_step_ids,
        completed_selection_ids=completed_selection_ids,
    )


_ORDERED_RESOURCE_WORKFLOW = """
const ordered: Workflow;
const after_step: Step;
const before_step: Step;
const after_name: StepName;
const before_name: StepName;
const worker: Agent;
const gpu: Resource;
const request: Artifact;
const after_result: Artifact;
const before_result: Artifact;
const selected_result: Artifact;

workflow ordered {
    input_workflow(ordered) == [request];
    output_workflow(ordered) == [after_result, before_result, selected_result];
    max_concurrency(ordered) == 2;

    step_name(after_step) == after_name;
    step_instruction(after_step) == "after";
    step_executor(after_step) == worker;
    consumes(after_step) == [request];
    produces(after_step) == [after_result];

    step_name(before_step) == before_name;
    step_instruction(before_step) == "before";
    step_executor(before_step) == worker;
    consumes(before_step) == [request];
    produces(before_step) == [before_result];
    resource_requirement(before_step, gpu) == 1;

    depends_on(after_step, before_step) == True;
    selected_result == if(request = "go", before_result, after_result);
}
"""

_PROGRAM_WORKFLOW = (
    _ORDERED_RESOURCE_WORKFLOW.replace(
        "const worker: Agent;",
        "const worker: Program;",
    )
    .replace(
        "    resource_requirement(before_step, gpu) == 1;\n",
        "",
    )
    .replace(
        "    max_concurrency(ordered) == 2;",
        '    max_concurrency(ordered) == 2;\n    program_path(worker) == "./bin/worker";',
    )
)

_HUMAN_WORKFLOW = """
const review_flow: Workflow;
const draft_step: Step;
const review_step: Step;
const publish_step: Step;
const draft_name: StepName;
const review_name: StepName;
const publish_name: StepName;
const writer: Agent;
const reviewer: Human;
const request: Artifact;
const draft: Artifact;
const decision: Artifact;
const result: Artifact;

workflow review_flow {
    input_workflow(review_flow) == [request];
    output_workflow(review_flow) == [result];

    step_name(draft_step) == draft_name;
    step_instruction(draft_step) == "draft_proposal";
    step_executor(draft_step) == writer;
    consumes(draft_step) == [request];
    produces(draft_step) == [draft];

    step_name(review_step) == review_name;
    step_instruction(review_step) == "./instructions/review.txt";
    step_executor(review_step) == reviewer;
    consumes(review_step) == [draft];
    produces(review_step) == [decision];

    step_name(publish_step) == publish_name;
    step_instruction(publish_step) == "publish_reviewed_proposal";
    step_executor(publish_step) == writer;
    consumes(publish_step) == [decision];
    produces(publish_step) == [result];
}
"""

_STATUS_ARTIFACT_WORKFLOW = """
const status_flow: Workflow;
const status_step: Step;
const status_name: StepName;
const worker: Agent;
const request: Artifact;
const status: Artifact;

workflow status_flow {
    input_workflow(status_flow) == [request];
    output_workflow(status_flow) == [status];

    step_name(status_step) == status_name;
    step_instruction(status_step) == "report_status";
    step_executor(status_step) == worker;
    consumes(status_step) == [request];
    produces(status_step) == [status];
}
"""


def test_run_flow_exposes_start_and_human_resume_tools() -> None:
    public_async = {
        name
        for name, value in vars(run_flow_tool).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(value)
    }
    assert public_async == {"run_flow", "run_flow_resume"}

    tool = ToolFunction.from_callable(run_flow_tool.run_flow)
    assert set(tool.parameters["properties"]) == {
        "flow_path",
        "inputs_json",
        "resource_capacities_json",
    }
    assert tool.parameters["required"] == ["flow_path"]

    resume_tool = ToolFunction.from_callable(run_flow_tool.run_flow_resume)
    assert set(resume_tool.parameters["properties"]) == {
        "run_id",
        "request_id",
        "human_response_json",
    }
    assert resume_tool.parameters["required"] == [
        "run_id",
        "request_id",
        "human_response_json",
    ]


@pytest.mark.anyio
async def test_program_runner_executes_exact_argv_and_json_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = tmp_path / "bin" / "worker"
    worker.parent.mkdir()
    worker.write_text("#!/bin/sh\n", encoding="utf-8")
    worker.chmod(0o700)
    worker_inode = (await anyio.Path(worker).stat()).st_ino
    workspace_inode = (await anyio.Path(tmp_path).stat()).st_ino
    calls: list[dict[str, object]] = []
    process = _FakeProcess(stdout=b"completed\r\n")

    async def open_process(
        command: tuple[str, ...],
        *,
        stdin: int,
        stdout: int,
        stderr: int,
        cwd: Path | None,
        creationflags: int,
        start_new_session: bool,
        pass_fds: tuple[int, ...],
    ) -> _FakeProcess:
        calls.append(
            {
                "command": command,
                "stdin": stdin,
                "stdout": stdout,
                "stderr": stderr,
                "cwd": cwd,
                "creationflags": creationflags,
                "start_new_session": start_new_session,
                "pass_fds": pass_fds,
            }
        )
        assert command[:4] == (sys.executable, "-I", "-c", run_flow_tool._POSIX_EXEC_BOOTSTRAP)
        assert command[4:7] == (
            str(pass_fds[0]),
            str(pass_fds[1]),
            f"/proc/self/fd/{pass_fds[0]}",
        )
        assert json.loads(command[7]) == ["./bin/worker", "--mode", "strict"]
        assert os.fstat(pass_fds[0]).st_ino == worker_inode
        assert os.fstat(pass_fds[1]).st_ino == workspace_inode
        return process

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_find_posix_fd_root", lambda: "/proc/self/fd")
    monkeypatch.setattr(run_flow_tool.anyio, "open_process", open_process)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./bin/worker", "--mode", "strict"),
        stdin='{"instruction":"work","inputs":{"request":"go"}}\n',
        cwd=tmp_path,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    stdout = await run_flow_tool._run_program(invocation)

    assert stdout == "completed"
    assert len(calls) == 1
    assert calls[0]["cwd"] is None
    assert calls[0]["creationflags"] == 0
    assert calls[0]["start_new_session"] is True
    assert bytes(process.stdin.data) == b'{"instruction":"work","inputs":{"request":"go"}}\n'
    assert process.stdin.closed
    assert process.closed
    for descriptor in cast(tuple[int, ...], calls[0]["pass_fds"]):
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.anyio
@pytest.mark.skipif(
    os.name != "posix" or os.execve not in os.supports_fd,
    reason="requires POSIX file-descriptor execve",
)
async def test_program_runner_executes_native_binary_without_procfs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_source = anyio.Path("/bin/cat")
    if not await native_source.exists():
        pytest.skip("/bin/cat is unavailable")
    worker = anyio.Path(tmp_path / "bin" / "worker")
    await worker.parent.mkdir()
    await worker.write_bytes(await native_source.read_bytes())
    await worker.chmod(0o700)
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_find_posix_fd_root", lambda: None)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./bin/worker",),
        stdin='{"instruction":"native","inputs":{}}\n',
        cwd=tmp_path,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    assert await run_flow_tool._run_program(invocation) == '{"instruction":"native","inputs":{}}'


@pytest.mark.anyio
async def test_program_runner_rejects_shebang_before_spawn_without_fd_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = tmp_path / "worker"
    worker.write_text("#!/bin/sh\nprintf escaped\n", encoding="utf-8")
    worker.chmod(0o700)
    spawned = False

    async def open_process(*args: object, **kwargs: object) -> _FakeProcess:
        nonlocal spawned
        del args, kwargs
        spawned = True
        return _FakeProcess()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_find_posix_fd_root", lambda: None)
    monkeypatch.setattr(run_flow_tool.anyio, "open_process", open_process)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./worker",),
        stdin="{}\n",
        cwd=tmp_path,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    with pytest.raises(RuntimeError, match="shebang Program execution requires"):
        await run_flow_tool._run_program(invocation)

    assert not spawned


@pytest.mark.anyio
async def test_program_runner_pins_working_directory_before_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    work = workspace / "work"
    work.mkdir(parents=True)
    worker = work / "worker"
    worker.write_bytes(b"native-placeholder")
    worker.chmod(0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    work_inode = work.stat().st_ino
    process = _FakeProcess(stdout=b"pinned\n")

    async def open_process(
        command: tuple[str, ...],
        *,
        stdin: int,
        stdout: int,
        stderr: int,
        cwd: Path | None,
        creationflags: int,
        start_new_session: bool,
        pass_fds: tuple[int, ...],
    ) -> _FakeProcess:
        del command, stdin, stdout, stderr, creationflags, start_new_session
        pinned = workspace / "pinned"
        work.rename(pinned)
        work.symlink_to(outside, target_is_directory=True)
        assert cwd is None
        assert os.fstat(pass_fds[1]).st_ino == work_inode
        assert os.fstat(pass_fds[1]).st_ino == pinned.stat().st_ino
        return process

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", workspace)
    monkeypatch.setattr(run_flow_tool.anyio, "open_process", open_process)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./worker",),
        stdin="{}\n",
        cwd=work,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    assert await run_flow_tool._run_program(invocation) == "pinned"


@pytest.mark.anyio
async def test_program_runner_executes_opened_inode_when_path_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = tmp_path / "worker"
    worker.write_text("#!/bin/sh\nprintf safe\n", encoding="utf-8")
    worker.chmod(0o700)
    safe_inode = worker.stat().st_ino
    outside = tmp_path.parent / "outside-worker"
    outside.write_text("#!/bin/sh\nprintf escaped\n", encoding="utf-8")
    outside.chmod(0o700)
    process = _FakeProcess(stdout=b"safe\n")

    async def open_process(
        command: tuple[str, ...],
        *,
        stdin: int,
        stdout: int,
        stderr: int,
        cwd: Path | None,
        creationflags: int,
        start_new_session: bool,
        pass_fds: tuple[int, ...],
    ) -> _FakeProcess:
        del stdin, stdout, stderr, cwd, creationflags, start_new_session
        worker.unlink()
        worker.symlink_to(outside)
        assert os.fstat(pass_fds[0]).st_ino == safe_inode
        assert command[4] == str(pass_fds[0])
        assert command[6] == f"/proc/self/fd/{pass_fds[0]}"
        return process

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_find_posix_fd_root", lambda: "/proc/self/fd")
    monkeypatch.setattr(run_flow_tool.anyio, "open_process", open_process)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./worker",),
        stdin="{}\n",
        cwd=tmp_path,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    assert await run_flow_tool._run_program(invocation) == "safe"


@pytest.mark.anyio
async def test_program_runner_preserves_windows_argv_after_pinning_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = tmp_path / "worker.exe"

    def open_windows_executable(
        workspace: Path,
        candidate: Path,
    ) -> tuple[Path, int]:
        assert workspace == tmp_path
        assert candidate == worker
        return candidate, 123

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_PROGRAM_PLATFORM", "win32")
    monkeypatch.setattr(run_flow_tool, "_open_windows_executable", open_windows_executable)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./worker.exe", "--mode", "strict"),
        stdin="{}\n",
        cwd=tmp_path,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    prepared = await run_flow_tool._prepare_program(invocation)

    assert prepared.command == (str(worker), "--mode", "strict")
    assert prepared.cwd == tmp_path
    assert prepared.retained_handle == 123


@pytest.mark.anyio
@pytest.mark.parametrize("program_path", ["../outside", "/tmp/outside"])
async def test_program_runner_rejects_executables_outside_workspace(
    program_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", workspace)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=(program_path,),
        stdin="{}\n",
        cwd=workspace,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    with pytest.raises(ValueError, match="inside the workspace"):
        await run_flow_tool._run_program(invocation)


@pytest.mark.anyio
async def test_program_runner_rejects_symlink_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("#!/bin/sh\n", encoding="utf-8")
    (workspace / "worker").symlink_to(outside)
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", workspace)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./worker",),
        stdin="{}\n",
        cwd=workspace,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    with pytest.raises(ValueError, match="inside the workspace"):
        await run_flow_tool._run_program(invocation)


@pytest.mark.anyio
@pytest.mark.parametrize("configured", ["", "0", "-1", "+1", "1.5", " 1"])
async def test_program_runner_rejects_invalid_output_limit_environment(
    configured: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned = False

    async def open_process(*args: object, **kwargs: object) -> _FakeProcess:
        nonlocal spawned
        del args, kwargs
        spawned = True
        return _FakeProcess()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setenv("PSI_FUSION_FLOW_PROGRAM_STDOUT_LIMIT_BYTES", configured)
    monkeypatch.setattr(run_flow_tool.anyio, "open_process", open_process)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./worker",),
        stdin="{}\n",
        cwd=tmp_path,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    with pytest.raises(ValueError, match="PSI_FUSION_FLOW_PROGRAM_STDOUT_LIMIT_BYTES must be a positive integer"):
        await run_flow_tool._run_program(invocation)

    assert not spawned


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("stream_name", "limit_environment_variable"),
    [
        ("stdout", "PSI_FUSION_FLOW_PROGRAM_STDOUT_LIMIT_BYTES"),
        ("stderr", "PSI_FUSION_FLOW_PROGRAM_STDERR_LIMIT_BYTES"),
    ],
)
async def test_program_runner_terminates_tree_when_output_exceeds_limit(
    stream_name: str,
    limit_environment_variable: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = tmp_path / "worker"
    worker.write_bytes(b"native-placeholder")
    worker.chmod(0o700)
    process = _FakeProcess(
        stdout=b"12345" if stream_name == "stdout" else b"",
        stderr=b"12345" if stream_name == "stderr" else b"",
    )

    async def open_process(*args: object, **kwargs: object) -> _FakeProcess:
        del args, kwargs
        return process

    tree_terminations = 0

    async def terminate_process_tree(
        target: _FakeProcess,
        windows_job: int | None,
    ) -> None:
        nonlocal tree_terminations
        del windows_job
        assert target is process
        tree_terminations += 1
        target.kill()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setenv(limit_environment_variable, "4")
    monkeypatch.setattr(run_flow_tool.anyio, "open_process", open_process)
    monkeypatch.setattr(run_flow_tool, "_terminate_process_tree", terminate_process_tree)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./worker",),
        stdin="{}\n",
        cwd=tmp_path,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    with pytest.raises(RuntimeError, match=rf"{stream_name} exceeded the 4-byte limit"):
        await run_flow_tool._run_program(invocation)

    assert tree_terminations >= 1
    assert process.killed


@pytest.mark.anyio
async def test_program_runner_obeys_external_cancellation_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = tmp_path / "worker"
    worker.write_bytes(b"native-placeholder")
    worker.chmod(0o700)
    process = _BlockingFakeProcess()
    executable_fds: tuple[int, ...] = ()
    spawn_returned = False

    async def open_process(*args: object, **kwargs: object) -> _BlockingFakeProcess:
        nonlocal executable_fds, spawn_returned
        del args
        executable_fds = cast(tuple[int, ...], kwargs["pass_fds"])
        await anyio.sleep(0.1)
        spawn_returned = True
        return process

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool.anyio, "open_process", open_process)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./worker",),
        stdin="{}\n",
        cwd=tmp_path,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    with anyio.move_on_after(0.05) as cancel_scope:
        await run_flow_tool._run_program(invocation)

    assert cancel_scope.cancel_called
    assert spawn_returned
    assert process.killed
    assert process.closed
    for descriptor in executable_fds:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.anyio
async def test_program_runner_cleans_up_when_windows_job_attachment_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = tmp_path / "worker"
    worker.write_bytes(b"native-placeholder")
    worker.chmod(0o700)
    process = _BlockingFakeProcess()
    executable_fds: tuple[int, ...] = ()
    tree_terminations = 0

    async def open_process(*args: object, **kwargs: object) -> _BlockingFakeProcess:
        nonlocal executable_fds
        del args
        executable_fds = cast(tuple[int, ...], kwargs["pass_fds"])
        return process

    def attach_windows_job(target: _BlockingFakeProcess) -> None:
        assert target is process
        raise RuntimeError("job attachment failed")

    async def terminate_process_tree(
        target: _BlockingFakeProcess,
        windows_job: int | None,
    ) -> None:
        nonlocal tree_terminations
        assert target is process
        assert windows_job is None
        tree_terminations += 1
        target.kill()
        await target.wait()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool.anyio, "open_process", open_process)
    monkeypatch.setattr(run_flow_tool, "_attach_windows_job", attach_windows_job)
    monkeypatch.setattr(run_flow_tool, "_terminate_process_tree", terminate_process_tree)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./worker",),
        stdin="{}\n",
        cwd=tmp_path,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    with pytest.raises(RuntimeError, match="job attachment failed"):
        await run_flow_tool._run_program(invocation)

    assert tree_terminations == 1
    assert process.killed
    assert process.closed
    for descriptor in executable_fds:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.anyio
async def test_program_tree_cleanup_kills_descendants_that_inherit_pipes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "descendant-finished"
    descendant = (
        "import pathlib,sys,time;time.sleep(0.3);pathlib.Path(sys.argv[1]).write_text('escaped', encoding='utf-8')"
    )
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}, sys.argv[1]]);"
        "print('ready', flush=True);"
        "time.sleep(60)"
    )
    process = await anyio.open_process(
        (sys.executable, "-c", parent, str(marker)),
        stdout=run_flow_tool.subprocess.PIPE,
        stderr=run_flow_tool.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert b"ready" in await process.stdout.receive()
    monkeypatch.setattr(run_flow_tool, "_PROGRAM_TERMINATION_GRACE_SECONDS", 0.05)

    await run_flow_tool._terminate_process_tree(process, None)
    await process.aclose()
    await anyio.sleep(0.4)

    assert not marker.exists()


@pytest.mark.anyio
async def test_windows_job_termination_failure_closes_kill_on_close_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    def terminate_job_object(handle: int, exit_code: int) -> int:
        calls.append(("terminate", handle))
        assert exit_code == 1
        return 0

    def close_handle(handle: int) -> int:
        calls.append(("close", handle))
        return 1

    class FakeCtypes:
        @staticmethod
        def get_last_error() -> int:
            return 5

    process = _BlockingFakeProcess()
    job = run_flow_tool._WindowsJob(123)
    monkeypatch.setattr(run_flow_tool.sys, "platform", "win32")
    monkeypatch.setattr(
        run_flow_tool,
        "_kernel32",
        SimpleNamespace(
            TerminateJobObject=terminate_job_object,
            CloseHandle=close_handle,
        ),
        raising=False,
    )
    monkeypatch.setattr(run_flow_tool, "ctypes", FakeCtypes(), raising=False)

    with pytest.raises(OSError, match="cannot terminate Windows Program Job Object"):
        await run_flow_tool._terminate_process_tree(process, job)

    assert calls == [("terminate", 123), ("close", 123)]
    assert job.handle is None
    assert process.killed


@pytest.mark.anyio
async def test_run_flow_executes_once_with_dependencies_and_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flows = anyio.Path(tmp_path / "flows")
    await flows.mkdir()
    await (flows / "ordered.workflow").write_text(_ORDERED_RESOURCE_WORKFLOW, encoding="utf-8")
    prompts: list[str] = []

    class FakeConversation:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = [
                {"role": "system", "content": "step system prompt"},
            ]

    class FakeAgent:
        def __init__(self, conversation: FakeConversation) -> None:
            self.conversation = conversation

        async def run(
            self,
            user_message: dict[str, object],
            extra_params: dict[str, object] | None = None,
        ) -> Any:
            del extra_params
            prompt = cast(str, user_message["content"])
            prompts.append(prompt)
            if "Step: before_step\n" in prompt:
                content = '{"before_result": "BEFORE"}'
            elif "Step: after_step\n" in prompt:
                content = '{"after_result": "AFTER"}'
            else:
                raise AssertionError(f"unexpected prompt: {prompt}")
            self.conversation.messages.extend(
                [
                    user_message,
                    {"role": "assistant", "content": content},
                ]
            )
            if False:
                yield None

    agents: list[FakeAgent] = []

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> tuple[FakeAgent, FakeConversation]:
        assert ai_socket == "http://ai.example"
        assert "run_flow" not in tool_registry.tools
        conversation = FakeConversation()
        agent = FakeAgent(conversation)
        agents.append(agent)
        return agent, conversation

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)
    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")

    result = await run_flow_tool.run_flow(
        "flows/ordered.workflow",
        '{"request": "go"}',
        '{"gpu": ["cuda:0"]}',
    )

    assert json.loads(result) == {
        "after_result": "AFTER",
        "before_result": "BEFORE",
        "selected_result": "BEFORE",
    }
    assert ["Step: before_step\n" in prompt for prompt in prompts] == [True, False]
    assert 'Reserved resources: {"gpu": ["cuda:0"]}' in prompts[0]
    assert "Reserved resources: {}" in prompts[1]
    assert all(f"Workspace root: {tmp_path}\n" in prompt for prompt in prompts)
    assert len(agents) == 2


@pytest.mark.anyio
async def test_run_flow_executes_program_without_creating_agent_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "flows" / "program.workflow")
    await flow_path.parent.mkdir()
    await flow_path.write_text(_PROGRAM_WORKFLOW, encoding="utf-8")
    created = False
    loaded_tools = False
    calls: list[dict[str, object]] = []

    async def create_step_agent(ai_socket: str, tool_registry: ToolRegistry) -> None:
        nonlocal created
        del ai_socket, tool_registry
        created = True

    async def load_step_tools() -> ToolRegistry:
        nonlocal loaded_tools
        loaded_tools = True
        return ToolRegistry()

    async def execute_program(invocation: Any) -> str:
        calls.append(
            {
                "name": invocation.name,
                "argv": invocation.argv,
                "stdin": invocation.stdin,
                "cwd": invocation.cwd,
                "binding_name": invocation.binding_name,
            }
        )
        return "BEFORE" if invocation.binding_name == "before_step" else "AFTER"

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)
    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)
    monkeypatch.setattr(run_flow_tool, "_load_step_tools", load_step_tools)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")
    monkeypatch.setattr(run_flow_tool, "_run_program", execute_program)

    result = await run_flow_tool.run_flow(
        "flows/program.workflow",
        '{"request": "go"}',
    )

    assert json.loads(result) == {
        "after_result": "AFTER",
        "before_result": "BEFORE",
        "selected_result": "BEFORE",
    }
    assert not created
    assert not loaded_tools
    assert {call["binding_name"] for call in calls} == {
        "after_step",
        "before_step",
    }
    assert all(call["name"] == "worker" for call in calls)
    assert all(call["argv"] == ("./bin/worker",) for call in calls)
    assert all(call["cwd"] == tmp_path for call in calls)
    assert {json.loads(cast(str, call["stdin"]))["instruction"] for call in calls} == {
        "after",
        "before",
    }


@pytest.mark.anyio
async def test_status_artifact_cannot_collide_with_human_control_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "flows" / "status.workflow")
    await flow_path.parent.mkdir()
    await flow_path.write_text(_STATUS_ARTIFACT_WORKFLOW, encoding="utf-8")

    async def load_step_tools() -> ToolRegistry:
        return ToolRegistry()

    async def complete_agent_step(
        prompt: str,
        context: Any,
        *,
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> dict[str, object]:
        del prompt, tool_registry
        assert context.step_id == "status_step"
        assert ai_socket == "http://ai.example"
        return {"status": "waiting_for_human"}

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_load_step_tools", load_step_tools)
    monkeypatch.setattr(run_flow_tool, "_complete_agent_step", complete_agent_step)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")

    result = json.loads(
        await run_flow_tool.run_flow(
            "flows/status.workflow",
            '{"request": "report"}',
        )
    )

    assert result == {"status": "waiting_for_human"}
    assert run_flow_tool._HUMAN_CONTROL_KEY not in result


@pytest.mark.anyio
@pytest.mark.parametrize(
    "human_response",
    [
        "Approve",
        "Please add a concrete rollback section.",
    ],
    ids=["approval-choice", "free-text"],
)
async def test_human_step_waits_via_clarify_and_resumes_from_checkpoint(
    human_response: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "flows" / "review.workflow")
    await flow_path.parent.mkdir(parents=True)
    await flow_path.write_text(_HUMAN_WORKFLOW, encoding="utf-8")
    agent_calls: list[str] = []
    agent_inputs: dict[str, dict[str, object]] = {}
    preparation_prompts: list[str] = []

    async def load_step_tools() -> ToolRegistry:
        return ToolRegistry()

    async def complete_agent_step(
        prompt: str,
        context: Any,
        *,
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> dict[str, object]:
        del prompt, tool_registry
        assert ai_socket == "http://ai.example"
        agent_calls.append(context.step_id)
        agent_inputs[context.step_id] = dict(context.inputs)
        if context.step_id == "draft_step":
            return {"draft": "proposal-v2"}
        if context.step_id == "publish_step":
            return {"result": {"human_response": context.inputs["decision"]}}
        raise AssertionError(f"unexpected Agent step: {context.step_id}")

    async def prepare_human_step(
        prompt: str,
        context: Any,
        *,
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> str:
        del tool_registry
        assert ai_socket == "http://ai.example"
        assert context.step_id == "review_step"
        preparation_prompts.append(prompt)
        return json.dumps(
            {
                "question": "Approve the proposal or type requested changes?",
                "options": ["Approve", "Reject"],
                "recommended": 1,
                "default": "",
            }
        )

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)
    monkeypatch.setattr(run_flow_tool, "_load_step_tools", load_step_tools)
    monkeypatch.setattr(run_flow_tool, "_complete_agent_step", complete_agent_step)
    monkeypatch.setattr(run_flow_tool, "_prepare_human_step", prepare_human_step)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")

    waiting = json.loads(
        await run_flow_tool.run_flow(
            "flows/review.workflow",
            '{"request": "write a launch plan"}',
        )
    )
    assert set(waiting) == {run_flow_tool._HUMAN_CONTROL_KEY}
    control = waiting[run_flow_tool._HUMAN_CONTROL_KEY]

    assert control["status"] == "waiting_for_human"
    assert set(control["request"]) == {
        "request_id",
        "step_id",
        "question",
        "options",
        "recommended",
        "default",
        "output_artifact_ids",
    }
    assert control["request"]["step_id"] == "review_step"
    assert control["request"]["output_artifact_ids"] == ["decision"]
    assert agent_calls == ["draft_step"]
    assert len(preparation_prompts) == 1
    assert "Instruction or reference: ./instructions/review.txt" in preparation_prompts[0]
    assert '"draft": "proposal-v2"' in preparation_prompts[0]

    formatted = await clarify_tool.clarify(
        control["request"]["question"],
        control["request"]["options"],
        control["request"]["recommended"],
        control["request"]["default"],
    )
    assert "Approve (recommended)" in formatted
    assert "Other — type your own answer" in formatted

    store = run_flow_tool._job_store()
    persisted = await store.load(control["run_id"])
    assert persisted.status == "waiting_for_human"
    assert persisted.checkpoint is not None
    assert persisted.checkpoint.completed_step_ids == ("draft_step",)
    assert persisted.checkpoint.values == {
        "request": "write a launch plan",
        "draft": "proposal-v2",
    }

    request_id = control["request"]["request_id"]
    with pytest.raises(ValueError, match="does not match the active Human request"):
        await run_flow_tool.run_flow_resume(
            control["run_id"],
            "0" * 32,
            json.dumps(human_response),
        )
    result = await run_flow_tool.run_flow_resume(
        control["run_id"],
        request_id,
        json.dumps(human_response),
    )

    assert json.loads(result) == {
        "result": {"human_response": human_response},
    }
    assert agent_calls == ["draft_step", "publish_step"]
    assert agent_inputs["publish_step"] == {"decision": human_response}
    assert len(preparation_prompts) == 1

    completed = await store.load(control["run_id"])
    assert completed.status == "completed"
    assert completed.prepared_request is None
    assert completed.human_responses == {request_id: human_response}
    assert completed.checkpoint is not None
    assert completed.checkpoint.completed_step_ids == (
        "draft_step",
        "publish_step",
        "review_step",
    )

    assert (
        await run_flow_tool.run_flow_resume(
            control["run_id"],
            request_id,
            json.dumps(human_response),
        )
        == result
    )
    with pytest.raises(ValueError, match="different response"):
        await run_flow_tool.run_flow_resume(
            control["run_id"],
            request_id,
            '"different"',
        )


def test_human_response_checkpoint_supports_zero_and_multiple_outputs() -> None:
    checkpoint = _test_checkpoint({"request": "review"})
    gate = run_flow_tool.HumanRequestSpec.create(
        step_id="gate_step",
        question="Continue?",
        output_artifact_ids=(),
    )
    gated = run_flow_tool._checkpoint_human_response(checkpoint, gate, "Approve")

    assert gated.values == {"request": "review"}
    assert gated.completed_step_ids == ("gate_step",)

    review = run_flow_tool.HumanRequestSpec.create(
        step_id="review_step",
        question="Decide and comment.",
        output_artifact_ids=("decision", "comment"),
    )
    reviewed = run_flow_tool._checkpoint_human_response(
        checkpoint,
        review,
        {"decision": "Approve", "comment": "Ship it."},
    )

    assert reviewed.values == {
        "request": "review",
        "decision": "Approve",
        "comment": "Ship it.",
    }
    assert reviewed.completed_step_ids == ("review_step",)


@pytest.mark.anyio
async def test_invalid_multi_output_human_response_remains_correctable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "human response contract"
    flow_path = anyio.Path(tmp_path / "flows" / "review.workflow")
    await flow_path.parent.mkdir()
    await flow_path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")
    store = run_flow_tool._job_store()
    run = await store.create(
        flow_path="flows/review.workflow",
        flow_source=source,
        inputs={"request": "review"},
        checkpoint=_test_checkpoint({"request": "review"}),
    )
    request = run_flow_tool.HumanRequestSpec.create(
        step_id="review_step",
        question="Decide and comment.",
        output_artifact_ids=("decision", "comment"),
    )
    waiting = replace(
        run,
        status="waiting_for_human",
        prepared_request=request,
    )
    async with store.acquire(run.run_id) as lease:
        await lease.save(waiting)

    resumed_runs: list[Any] = []

    async def execute_persisted_run(
        flow_source: str,
        resumed: Any,
        lease: Any,
        *,
        ai_socket: str,
    ) -> str:
        del lease
        assert flow_source == source
        assert ai_socket == "http://ai.example"
        resumed_runs.append(resumed)
        return '{"accepted": true}'

    monkeypatch.setattr(run_flow_tool, "_execute_persisted_run", execute_persisted_run)

    with pytest.raises(ValueError, match="must receive a JSON object"):
        await run_flow_tool.run_flow_resume(
            run.run_id,
            request.request_id,
            '"Approve"',
        )

    still_waiting = await store.load(run.run_id)
    assert still_waiting == waiting

    result = await run_flow_tool.run_flow_resume(
        run.run_id,
        request.request_id,
        '{"decision": "Approve", "comment": "Ship it."}',
    )

    assert json.loads(result) == {"accepted": True}
    assert len(resumed_runs) == 1
    resumed = resumed_runs[0]
    assert resumed.status == "running"
    assert resumed.prepared_request is None
    assert resumed.human_responses == {
        request.request_id: {
            "decision": "Approve",
            "comment": "Ship it.",
        }
    }
    assert resumed.checkpoint.values == {
        "request": "review",
        "decision": "Approve",
        "comment": "Ship it.",
    }
    assert resumed.checkpoint.completed_step_ids == ("review_step",)


@pytest.mark.anyio
async def test_retrying_previous_human_response_replays_current_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "two human requests"
    flow_path = anyio.Path(tmp_path / "flows" / "review.workflow")
    await flow_path.parent.mkdir()
    await flow_path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")
    store = run_flow_tool._job_store()
    run = await store.create(
        flow_path="flows/review.workflow",
        flow_source=source,
        inputs={"request": "review"},
        checkpoint=_test_checkpoint(
            {"request": "review", "first_decision": True},
            completed_step_ids=("first_review",),
        ),
    )
    previous = run_flow_tool.HumanRequestSpec.create(
        step_id="first_review",
        question="First approval?",
        output_artifact_ids=("first_decision",),
    )
    current = run_flow_tool.HumanRequestSpec.create(
        step_id="second_review",
        question="Second approval?",
        output_artifact_ids=("second_decision",),
    )
    waiting = replace(
        run,
        status="waiting_for_human",
        prepared_request=current,
        human_responses={previous.request_id: True},
    )
    async with store.acquire(run.run_id) as lease:
        await lease.save(waiting)

    replayed = json.loads(
        await run_flow_tool.run_flow_resume(
            run.run_id,
            previous.request_id,
            "true",
        )
    )
    assert set(replayed) == {run_flow_tool._HUMAN_CONTROL_KEY}
    replayed_control = replayed[run_flow_tool._HUMAN_CONTROL_KEY]

    assert replayed_control["status"] == "waiting_for_human"
    assert replayed_control["request"]["request_id"] == current.request_id
    assert await store.load(run.run_id) == waiting

    with pytest.raises(ValueError, match="different response"):
        await run_flow_tool.run_flow_resume(
            run.run_id,
            previous.request_id,
            "1",
        )


@pytest.mark.anyio
async def test_cancelled_resume_keeps_checkpoint_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "cancelled resume"
    flow_path = anyio.Path(tmp_path / "flows" / "review.workflow")
    await flow_path.parent.mkdir()
    await flow_path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    store = run_flow_tool._job_store()
    run = await store.create(
        flow_path="flows/review.workflow",
        flow_source=source,
        inputs={"request": "review"},
        checkpoint=_test_checkpoint(
            {"request": "review", "decision": "Approve"},
            completed_step_ids=("review_step",),
        ),
    )
    request = run_flow_tool.HumanRequestSpec.create(
        step_id="review_step",
        question="Approve?",
        output_artifact_ids=("decision",),
    )
    running = replace(
        run,
        human_responses={request.request_id: "Approve"},
    )
    async with store.acquire(run.run_id) as lease:
        await lease.save(running)

    started = anyio.Event()

    async def execute_workflow(flow_source: str, **kwargs: Any) -> dict[str, object]:
        del kwargs
        assert flow_source == source
        started.set()
        await anyio.sleep_forever()
        raise AssertionError("sleep_forever returned unexpectedly")

    monkeypatch.setattr(run_flow_tool, "_execute_workflow", execute_workflow)

    async with store.acquire(run.run_id) as lease:

        async def execute_persisted() -> None:
            await run_flow_tool._execute_persisted_run(
                source,
                await lease.load(),
                lease,
                ai_socket="http://ai.example",
            )

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(execute_persisted)
            await started.wait()
            task_group.cancel_scope.cancel()

    persisted = await store.load(run.run_id)
    assert persisted.status == "running"
    assert persisted.checkpoint == running.checkpoint
    assert persisted.human_responses == {request.request_id: "Approve"}


@pytest.mark.anyio
async def test_human_resume_rejects_changed_workflow_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "flows" / "changed.workflow")
    await flow_path.parent.mkdir()
    original_source = "original source"
    await flow_path.write_text(original_source, encoding="utf-8")
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")
    store = run_flow_tool._job_store()
    run = await store.create(
        flow_path="flows/changed.workflow",
        flow_source=original_source,
        inputs={"request": "review"},
        checkpoint=_test_checkpoint({"request": "review"}),
    )
    request = run_flow_tool.HumanRequestSpec.create(
        step_id="review_step",
        question="Approve?",
        output_artifact_ids=("decision",),
        options=("Approve", "Reject"),
    )
    waiting = replace(
        run,
        status="waiting_for_human",
        prepared_request=request,
    )
    async with store.acquire(run.run_id) as lease:
        await lease.save(waiting)
    await flow_path.write_text("changed source", encoding="utf-8")

    with pytest.raises(ValueError, match="workflow source changed"):
        await run_flow_tool.run_flow_resume(
            run.run_id,
            request.request_id,
            '"Approve"',
        )

    failed = await store.load(run.run_id)
    assert failed.status == "failed"
    assert failed.error == "workflow source changed after the Human request was prepared"


@pytest.mark.anyio
async def test_step_agent_uses_in_memory_history_and_explicit_system_prompt() -> None:
    agent, conversation = await run_flow_tool._create_step_agent(
        "http://ai.example",
        ToolRegistry(),
    )

    assert agent._conversation is conversation
    assert agent._ai_client.ai_socket == "http://ai.example"
    assert conversation.messages == [
        {"role": "system", "content": run_flow_tool._STEP_SYSTEM_PROMPT},
    ]
    assert conversation._path is None


@pytest.mark.anyio
async def test_step_tool_snapshot_filters_run_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def echo(message: str) -> str:
        return message

    async def nested_run_flow(flow_path: str) -> str:
        return flow_path

    async def nested_run_flow_resume(
        run_id: str,
        request_id: str,
        human_response_json: str,
    ) -> str:
        return f"{run_id}:{request_id}:{human_response_json}"

    async def legacy_flow_run(flow_path: str) -> str:
        return flow_path

    async def clarify(question: str) -> str:
        return question

    loads = 0
    refreshes = 0

    class FakeToolRegistry(ToolRegistry):
        @classmethod
        async def load(
            cls,
            tools_dir: Path,
            session_id: str = "",
        ) -> ToolRegistry:
            nonlocal loads
            del cls, tools_dir, session_id
            loads += 1
            return source

        async def refresh(self) -> dict[str, str]:
            nonlocal refreshes
            refreshes += 1
            return {}

    source = FakeToolRegistry(
        files={
            "__test__": FileEntry(
                file_hash="",
                tools={
                    "echo": ToolFunction.from_callable(echo),
                    "flow_run": ToolFunction.from_callable(legacy_flow_run),
                    "run_flow": ToolFunction.from_callable(nested_run_flow),
                    "run_flow_resume": ToolFunction.from_callable(nested_run_flow_resume),
                    "clarify": ToolFunction.from_callable(clarify),
                },
                funcs={
                    "echo": echo,
                    "flow_run": legacy_flow_run,
                    "run_flow": nested_run_flow,
                    "run_flow_resume": nested_run_flow_resume,
                    "clarify": clarify,
                },
            )
        }
    )

    monkeypatch.setattr(run_flow_tool, "ToolRegistry", FakeToolRegistry)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)

    snapshot = await run_flow_tool._load_step_tools()
    refreshed_snapshot = await run_flow_tool._load_step_tools()

    assert set(snapshot.tools) == {"echo"}
    assert snapshot.get("echo") is echo
    assert snapshot.get("flow_run") is None
    assert snapshot.get("run_flow") is None
    assert snapshot.get("run_flow_resume") is None
    assert snapshot.get("clarify") is None
    assert set(refreshed_snapshot.tools) == {"echo"}
    assert loads == 1
    assert refreshes == 1


@pytest.mark.anyio
async def test_step_file_tools_bind_relative_paths_to_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    launcher = tmp_path / "launcher"
    await anyio.Path(workspace).mkdir()
    await anyio.Path(launcher).mkdir()
    received: dict[str, str] = {}

    async def read(file_path: str) -> str:
        received["read"] = file_path
        return await anyio.Path(file_path).read_text(encoding="utf-8")

    async def write(file_path: str, content: str) -> str:
        received["write"] = file_path
        path = anyio.Path(file_path)
        await path.parent.mkdir(parents=True, exist_ok=True)
        await path.write_text(content, encoding="utf-8")
        return file_path

    async def edit(file_path: str, old_string: str, new_string: str) -> str:
        received["edit"] = file_path
        path = anyio.Path(file_path)
        content = await path.read_text(encoding="utf-8")
        await path.write_text(content.replace(old_string, new_string, 1), encoding="utf-8")
        return file_path

    source = ToolRegistry(
        files={
            "__test__": FileEntry(
                file_hash="",
                tools={
                    "edit": ToolFunction.from_callable(edit),
                    "read": ToolFunction.from_callable(read),
                    "write": ToolFunction.from_callable(write),
                },
                funcs={"edit": edit, "read": read, "write": write},
            )
        }
    )

    class FakeToolRegistry(ToolRegistry):
        @classmethod
        async def load(
            cls,
            tools_dir: Path,
            session_id: str = "",
        ) -> ToolRegistry:
            del cls, tools_dir, session_id
            return source

    sidecar = workspace / "instructions" / "review.md"
    await anyio.Path(sidecar.parent).mkdir()
    await anyio.Path(sidecar).write_text("workspace sidecar", encoding="utf-8")
    monkeypatch.chdir(launcher)
    monkeypatch.setattr(run_flow_tool, "ToolRegistry", FakeToolRegistry)
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", workspace)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)

    snapshot = await run_flow_tool._load_step_tools()
    edit_tool = snapshot.get("edit")
    read_tool = snapshot.get("read")
    write_tool = snapshot.get("write")
    assert edit_tool is not None
    assert read_tool is not None
    assert write_tool is not None

    assert await read_tool(file_path="instructions/review.md") == "workspace sidecar"
    await write_tool(
        file_path="flows/workflows/child/child.workflow",
        content="workflow child {}\n",
    )
    await edit_tool(
        file_path="flows/workflows/child/child.workflow",
        old_string="child",
        new_string="saved_child",
    )

    child = workspace / "flows" / "workflows" / "child" / "child.workflow"
    assert await anyio.Path(child).read_text(encoding="utf-8") == "workflow saved_child {}\n"
    assert received == {
        "edit": str(child),
        "read": str(sidecar),
        "write": str(child),
    }
    assert not await anyio.Path(launcher / "flows").exists()


@pytest.mark.anyio
async def test_human_preparer_reads_through_workspace_bound_step_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    instructions = workspace / "instructions"
    await anyio.Path(instructions).mkdir(parents=True)
    sidecar = instructions / "review.txt"
    await anyio.Path(sidecar).write_text("first\nsecond\n", encoding="utf-8")
    received: dict[str, object] = {}

    async def read(file_path: str, offset: int = 0, limit: int = 0) -> str:
        received.update(file_path=file_path, offset=offset, limit=limit)
        content = await anyio.Path(file_path).read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        selected = lines[offset:] if limit == 0 else lines[offset : offset + limit]
        return "".join(selected)

    source = ToolRegistry(
        files={
            "__test__": FileEntry(
                file_hash="",
                tools={"read": ToolFunction.from_callable(read)},
                funcs={"read": read},
            )
        }
    )

    class FakeToolRegistry(ToolRegistry):
        @classmethod
        async def load(
            cls,
            tools_dir: Path,
            session_id: str = "",
        ) -> ToolRegistry:
            del cls, tools_dir, session_id
            return source

    monkeypatch.setattr(run_flow_tool, "ToolRegistry", FakeToolRegistry)
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", workspace)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)

    step_tools = await run_flow_tool._load_step_tools()
    human_tools = run_flow_tool._build_human_preparer_tools(step_tools)
    human_read = human_tools.get("read")

    assert human_read is not None
    assert await human_read("instructions/review.txt", 1, 1) == "second\n"
    assert received == {
        "file_path": str(sidecar),
        "offset": 1,
        "limit": 1,
    }


@pytest.mark.anyio
async def test_human_preparer_tools_are_read_only_and_workspace_confined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    instructions = workspace / "instructions"
    instructions.mkdir()
    (instructions / "review.txt").write_text("Check rollback details.", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (instructions / "escape.txt").symlink_to(outside)

    async def read(file_path: str, offset: int = 0, limit: int = 0) -> str:
        content = await anyio.Path(file_path).read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        selected = lines[offset:] if limit == 0 else lines[offset : offset + limit]
        return "".join(selected)

    async def write(file_path: str, content: str) -> str:
        await anyio.Path(file_path).write_text(content, encoding="utf-8")
        return "ok"

    source = ToolRegistry(
        files={
            "__test__": FileEntry(
                file_hash="",
                tools={
                    "read": ToolFunction.from_callable(read),
                    "write": ToolFunction.from_callable(write),
                },
                funcs={
                    "read": read,
                    "write": write,
                },
            )
        }
    )
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", workspace)

    snapshot = run_flow_tool._build_human_preparer_tools(source)
    human_read = snapshot.get("read")

    assert set(snapshot.tools) == {"read"}
    assert snapshot.get("write") is None
    assert human_read is not None
    assert await human_read("instructions/review.txt") == "Check rollback details."
    with pytest.raises(ValueError, match="only files inside the workspace"):
        await human_read("../outside.txt")
    with pytest.raises(ValueError, match="only files inside the workspace"):
        await human_read(str(outside))
    with pytest.raises(ValueError, match="only files inside the workspace"):
        await human_read("instructions/escape.txt")


@pytest.mark.anyio
async def test_run_flow_rejects_paths_outside_flows_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)

    with pytest.raises(ValueError, match="workspace flows directory"):
        await run_flow_tool._read_flow_source("../outside.workflow")
    with pytest.raises(ValueError, match="workspace flows directory"):
        await run_flow_tool._read_flow_source("other/example.workflow")
    with pytest.raises(ValueError, match="relative to the workspace"):
        await run_flow_tool._read_flow_source(str(tmp_path / "flows" / "example.workflow"))


@pytest.mark.anyio
async def test_run_flow_requires_invoking_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: None)

    with pytest.raises(RuntimeError, match="called by a psi-agent Session"):
        await run_flow_tool.run_flow("flows/example.workflow", "{}")
