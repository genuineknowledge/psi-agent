from __future__ import annotations

import copy
import hashlib
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

from psi_agent.session.protocol import AiDelta
from psi_agent.session.runtime_context import path_scope
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


def _program_invocation(
    script: Path,
    *,
    stdin: str = '{"instruction":"work","inputs":{"request":"go"}}\n',
    instruction: str = "Run the declared program.",
    output_ids: tuple[str, ...] = ("result",),
    logical_args: tuple[str, ...] = (),
) -> Any:
    return run_flow_tool.ProgramInvocation(
        name="worker",
        argv=(str(script), *logical_args),
        stdin=stdin,
        cwd=script.parent,
        binding_name="work_step",
        dispatch=SimpleNamespace(
            resource_lease=SimpleNamespace(grants=()),
        ),
        instruction=instruction,
        inputs={"request": "go"},
        output_ids=output_ids,
    )


def _tool_registry(*functions: Any) -> ToolRegistry:
    metadata = {function.__name__: ToolFunction.from_callable(function) for function in functions}
    return ToolRegistry(
        files={
            "__test__": FileEntry(
                file_hash="",
                tools=metadata,
                funcs={function.__name__: function for function in functions},
            )
        }
    )


def _install_program_agent_driver(
    monkeypatch: pytest.MonkeyPatch,
    drive: Any,
    captured: dict[str, object] | None = None,
) -> None:
    capture = captured if captured is not None else {}

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
        *,
        system_prompt: str,
    ) -> tuple[Any, Any]:
        capture["ai_socket"] = ai_socket
        capture["system_prompt"] = system_prompt
        capture["tool_names"] = set(tool_registry.tools)
        return (
            SimpleNamespace(tool_registry=tool_registry),
            SimpleNamespace(messages=[]),
        )

    async def complete_step_agent(
        agent: Any,
        conversation: Any,
        message: str,
        *,
        stop_when: Any = None,
    ) -> str:
        del conversation
        capture["message"] = message
        await drive(agent.tool_registry)
        if stop_when is not None:
            assert stop_when()
        return ""

    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)
    monkeypatch.setattr(run_flow_tool, "_complete_step_agent", complete_step_agent)


_ORDERED_RESOURCE_WORKFLOW = """
const ordered: Workflow;
const after_step: Step;
const before_step: Step;
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

    step_name(after_step) == "After";
    step_instruction(after_step) == "after";
    step_executor(after_step) == worker;
    consumes(after_step) == [request];
    produces(after_step) == [after_result];

    step_name(before_step) == "Before";
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
const writer: Agent;
const reviewer: Human;
const request: Artifact;
const draft: Artifact;
const decision: Artifact;
const result: Artifact;

workflow review_flow {
    input_workflow(review_flow) == [request];
    output_workflow(review_flow) == [result];

    step_name(draft_step) == "Draft";
    step_instruction(draft_step) == "draft_proposal";
    step_executor(draft_step) == writer;
    consumes(draft_step) == [request];
    produces(draft_step) == [draft];

    step_name(review_step) == "Review";
    step_instruction(review_step) == "./instructions/review.md";
    step_executor(review_step) == reviewer;
    consumes(review_step) == [draft];
    produces(review_step) == [decision];

    step_name(publish_step) == "Publish";
    step_instruction(publish_step) == "publish_reviewed_proposal";
    step_executor(publish_step) == writer;
    consumes(publish_step) == [decision];
    produces(publish_step) == [result];
}
"""

_STATUS_ARTIFACT_WORKFLOW = """
const status_flow: Workflow;
const status_step: Step;
const worker: Agent;
const request: Artifact;
const status: Artifact;

workflow status_flow {
    input_workflow(status_flow) == [request];
    output_workflow(status_flow) == [status];

    step_name(status_step) == "Status";
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
async def test_execute_program_command_runs_non_executable_python_with_exact_io(
    tmp_path: Path,
) -> None:
    script = tmp_path / "worker.py"
    script.write_text(
        "import json\n"
        "import sys\n"
        "payload = {\n"
        '    "argv": sys.argv[1:],\n'
        '    "stdin": sys.stdin.buffer.read().decode("utf-8"),\n'
        "}\n"
        "sys.stdout.buffer.write(\n"
        '    json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\\r\\n"\n'
        ")\n",
        encoding="utf-8",
    )
    script.chmod(0o600)
    stdin = '{"instruction":"work","inputs":{"request":"gø"}}\r\n'
    invocation = _program_invocation(script, stdin=stdin)
    argv = (sys.executable, str(script), "--mode", "two words")

    result = await run_flow_tool._execute_program_command(
        invocation,
        argv,
        stdin=stdin,
    )

    assert script.stat().st_mode & 0o111 == 0
    assert result.argv == argv
    assert result.exit_code == 0
    assert result.stderr == b""
    assert result.stdout.endswith(b"\r\n")
    assert json.loads(result.stdout.removesuffix(b"\r\n")) == {
        "argv": ["--mode", "two words"],
        "stdin": stdin,
    }


@pytest.mark.anyio
async def test_complete_program_step_uses_dedicated_prompt_and_tool_allow_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "worker.py"
    script.write_text("print('unused')\n", encoding="utf-8")
    captured: dict[str, object] = {}
    process_calls: list[tuple[tuple[str, ...], str]] = []

    async def bash(command: str) -> str:
        return command

    async def find_files(pattern: str) -> str:
        return pattern

    async def list_dir(path: str) -> str:
        return path

    async def powershell(command: str, cwd: str = "") -> str:
        return f"{cwd}:{command}"

    async def read(file_path: str) -> str:
        return file_path

    async def write(file_path: str, content: str) -> str:
        return f"{file_path}:{content}"

    async def edit(file_path: str, old_string: str, new_string: str) -> str:
        return f"{file_path}:{old_string}:{new_string}"

    async def run_flow(flow_path: str) -> str:
        return flow_path

    async def clarify(question: str) -> str:
        return question

    async def execute_program_command(
        invocation: Any,
        argv: tuple[str, ...],
        *,
        stdin: str,
    ) -> Any:
        assert invocation.cwd == tmp_path
        process_calls.append((argv, stdin))
        return run_flow_tool._ProgramProcessResult(
            argv=argv,
            exit_code=0,
            stdout=b"verbatim\r\n",
            stderr=b"",
        )

    async def drive(tool_registry: ToolRegistry) -> None:
        assert set(tool_registry.tools) == {
            *run_flow_tool._PROGRAM_AGENT_TOOLS,
            "compile_program",
            "execute_program",
            "submit_program_result",
        }
        assert tool_registry.get("write") is None
        assert tool_registry.get("edit") is None
        assert tool_registry.get("run_flow") is None
        assert tool_registry.get("clarify") is None
        execute = tool_registry.get("execute_program")
        submit = tool_registry.get("submit_program_result")
        assert execute is not None
        assert submit is not None
        attempt = json.loads(
            await execute(
                runtime=sys.executable,
            )
        )
        assert attempt == {
            "argv": [sys.executable, str(script), "--mode", "two words"],
            "error": None,
            "exit_code": 0,
            "stderr": "",
            "stderr_base64": None,
            "stdout": "verbatim\r\n",
            "stdout_base64": None,
        }
        await submit()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_execute_program_command", execute_program_command)
    _install_program_agent_driver(monkeypatch, drive, captured)
    invocation = _program_invocation(script, logical_args=("--mode", "two words"))

    outputs = await run_flow_tool._complete_program_step(
        invocation,
        ai_socket="http://ai.example",
        tool_registry=_tool_registry(
            bash,
            find_files,
            list_dir,
            powershell,
            read,
            write,
            edit,
            run_flow,
            clarify,
        ),
    )

    assert outputs == {"result": "verbatim\r\n"}
    assert captured["ai_socket"] == "http://ai.example"
    assert captured["system_prompt"] == run_flow_tool._PROGRAM_SYSTEM_PROMPT
    message = cast(str, captured["message"])
    prefix = "Execute this exact Program contract:\n"
    assert message.startswith(prefix)
    contract = json.loads(message.removeprefix(prefix))
    assert contract == {
        "contract_version": 1,
        "cwd": str(tmp_path),
        "executor_id": "worker",
        "input_artifacts": {"request": "go"},
        "logical_argv": [str(script), "--mode", "two words"],
        "output_artifact_ids": ["result"],
        "output_mode": "stdout_verbatim",
        "repair_authorized": False,
        "reserved_resources": {},
        "script_path": str(script),
        "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "stdin_utf8": invocation.stdin,
        "step_id": "work_step",
        "step_instruction": "Run the declared program.",
        "workspace_root": str(tmp_path),
    }
    assert process_calls == [
        (
            (sys.executable, str(script), "--mode", "two words"),
            invocation.stdin,
        )
    ]


@pytest.mark.anyio
async def test_complete_program_step_captures_nonzero_exit_as_error_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "worker.py"
    script.write_text("raise SystemExit(7)\n", encoding="utf-8")

    async def execute_program_command(
        invocation: Any,
        argv: tuple[str, ...],
        *,
        stdin: str,
    ) -> Any:
        del invocation, stdin
        return run_flow_tool._ProgramProcessResult(
            argv=argv,
            exit_code=7,
            stdout=b"partial\r\n",
            stderr=b"boom\r\n",
        )

    async def drive(tool_registry: ToolRegistry) -> None:
        execute = tool_registry.get("execute_program")
        submit = tool_registry.get("submit_program_result")
        assert execute is not None
        assert submit is not None
        await execute(runtime=sys.executable)
        await submit()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_execute_program_command", execute_program_command)
    _install_program_agent_driver(monkeypatch, drive)

    outputs = await run_flow_tool._complete_program_step(
        _program_invocation(script),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    error = cast(dict[str, Any], outputs["result"])[run_flow_tool._PROGRAM_ERROR_KEY]
    assert error["phase"] == "execution"
    assert error["kind"] == "nonzero_exit"
    assert error["message"] == "Program exited with code 7."
    assert error["attempts"] == [
        {
            "argv": [sys.executable, str(script)],
            "error": None,
            "exit_code": 7,
            "stderr": "boom\r\n",
            "stderr_base64": None,
            "stdout": "partial\r\n",
            "stdout_base64": None,
        }
    ]


@pytest.mark.anyio
async def test_program_fidelity_host_builds_argv_and_rejects_inline_or_arbitrary_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "worker.py"
    other_script = tmp_path / "other.py"
    script.write_text("print('real')\n", encoding="utf-8")
    other_script.write_text("print('fake')\n", encoding="utf-8")
    process_called = False

    async def execute_program_command(*args: object, **kwargs: object) -> Any:
        nonlocal process_called
        del args, kwargs
        process_called = True
        raise AssertionError("unprovenanced command reached the process boundary")

    async def drive(tool_registry: ToolRegistry) -> None:
        execute_metadata = tool_registry.tools["execute_program"]
        assert "argv" not in execute_metadata.parameters["properties"]
        assert "program_args" not in execute_metadata.parameters["properties"]
        execute = tool_registry.get("execute_program")
        submit = tool_registry.get("submit_program_result")
        assert execute is not None
        assert submit is not None
        attempt = json.loads(await execute(runtime=f"{sys.executable} -c"))
        assert "selected runtime" in attempt["error"]
        attempt = json.loads(await execute(runtime="printf"))
        assert "general-purpose command" in attempt["error"]
        await submit()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_execute_program_command", execute_program_command)
    _install_program_agent_driver(monkeypatch, drive)

    outputs = await run_flow_tool._complete_program_step(
        _program_invocation(script),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert not process_called
    error = cast(dict[str, Any], outputs["result"])[run_flow_tool._PROGRAM_ERROR_KEY]
    assert error["kind"] == "execution_error"
    assert len(error["attempts"]) == 2
    assert str(other_script) not in json.dumps(error)


@pytest.mark.parametrize(
    "runtime",
    (
        "file",
        "find",
        "find.exe",
        "findstr",
        "findstr.exe",
        "head",
        "/usr/bin/head",
        "more",
        "more.com",
        "mv",
        "rm",
        "sort",
        "sort.exe",
        "tail",
        "touch",
        "unlink",
        "wc",
        "where",
        "where.exe",
        "xargs",
        "xcopy",
        "xcopy.exe",
    ),
)
@pytest.mark.anyio
async def test_program_fidelity_rejects_common_non_interpreter_commands(
    runtime: str,
    tmp_path: Path,
) -> None:
    script = tmp_path / "worker.py"
    script.write_text("print('real')\n", encoding="utf-8")

    argv, error = await run_flow_tool._build_interpreted_program_argv(
        runtime,
        cwd=tmp_path,
        script=script,
        logical_args=(),
    )

    assert argv == ()
    assert error == "The selected runtime is a general-purpose command, not a language interpreter."


@pytest.mark.anyio
async def test_program_fidelity_preserves_launched_failure_and_forbids_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "worker.py"
    script.write_text("raise SystemExit(2)\n", encoding="utf-8")
    process_calls = 0

    async def execute_program_command(
        invocation: Any,
        argv: tuple[str, ...],
        *,
        stdin: str,
    ) -> Any:
        nonlocal process_calls
        del invocation, stdin
        process_calls += 1
        return run_flow_tool._ProgramProcessResult(
            argv=argv,
            exit_code=2,
            stdout=b"",
            stderr=b"invalid input\n",
        )

    async def drive(tool_registry: ToolRegistry) -> None:
        execute = tool_registry.get("execute_program")
        submit = tool_registry.get("submit_program_result")
        assert execute is not None
        assert submit is not None
        first = json.loads(await execute(runtime=sys.executable))
        assert first["exit_code"] == 2
        second = json.loads(await execute(runtime=sys.executable))
        assert second["exit_code"] is None
        assert "only one launched Program attempt" in second["error"]
        await submit()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_execute_program_command", execute_program_command)
    _install_program_agent_driver(monkeypatch, drive)

    outputs = await run_flow_tool._complete_program_step(
        _program_invocation(script),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert process_calls == 1
    error = cast(dict[str, Any], outputs["result"])[run_flow_tool._PROGRAM_ERROR_KEY]
    assert error["kind"] == "execution_error"
    assert [attempt["exit_code"] for attempt in error["attempts"]] == [2, None]
    assert error["attempts"][0]["stderr"] == "invalid input\n"


@pytest.mark.anyio
async def test_program_compiled_launch_is_bound_to_source_artifact_and_logical_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "worker.c"
    artifact = tmp_path / "worker-bin"
    script.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    process_calls: list[tuple[str, ...]] = []

    async def execute_program_command(
        invocation: Any,
        argv: tuple[str, ...],
        *,
        stdin: str,
    ) -> Any:
        del invocation, stdin
        process_calls.append(argv)
        if argv[0] == "test-compiler":
            artifact.write_bytes(b"compiled")
            return run_flow_tool._ProgramProcessResult(argv=argv, exit_code=0, stdout=b"", stderr=b"")
        return run_flow_tool._ProgramProcessResult(
            argv=argv,
            exit_code=0,
            stdout=b"compiled result\n",
            stderr=b"",
        )

    async def drive(tool_registry: ToolRegistry) -> None:
        compile_program = tool_registry.get("compile_program")
        execute = tool_registry.get("execute_program")
        submit = tool_registry.get("submit_program_result")
        assert compile_program is not None
        assert execute is not None
        assert submit is not None
        compilation = json.loads(
            await compile_program(
                compile_argv=["test-compiler", str(script), "-o", str(artifact)],
                execute_argv=[str(artifact)],
                artifact_paths=[str(artifact)],
            )
        )
        assert compilation["registered"] is True
        await execute(compiled_launch_argv=[str(artifact)])
        await submit()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_execute_program_command", execute_program_command)
    _install_program_agent_driver(monkeypatch, drive)

    outputs = await run_flow_tool._complete_program_step(
        _program_invocation(script, logical_args=("--mode", "exact")),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert outputs == {"result": "compiled result\n"}
    assert process_calls == [
        ("test-compiler", str(script), "-o", str(artifact)),
        (str(artifact), "--mode", "exact"),
    ]


@pytest.mark.anyio
async def test_complete_program_step_broadcasts_multi_output_format_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "worker.py"
    script.write_text("print('not-json')\n", encoding="utf-8")

    async def execute_program_command(
        invocation: Any,
        argv: tuple[str, ...],
        *,
        stdin: str,
    ) -> Any:
        del invocation, stdin
        return run_flow_tool._ProgramProcessResult(
            argv=argv,
            exit_code=0,
            stdout=b"not-json\r\n",
            stderr=b"",
        )

    async def drive(tool_registry: ToolRegistry) -> None:
        execute = tool_registry.get("execute_program")
        submit = tool_registry.get("submit_program_result")
        assert execute is not None
        assert submit is not None
        await execute(runtime=sys.executable)
        await submit()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_execute_program_command", execute_program_command)
    _install_program_agent_driver(monkeypatch, drive)

    outputs = await run_flow_tool._complete_program_step(
        _program_invocation(script, output_ids=("left", "right")),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert set(outputs) == {"left", "right"}
    assert outputs["left"] == outputs["right"]
    error = cast(dict[str, Any], outputs["left"])[run_flow_tool._PROGRAM_ERROR_KEY]
    assert error["phase"] == "output_format"
    assert error["kind"] == "invalid_output_contract"
    assert "strict JSON object" in error["message"]
    assert error["attempts"][0]["stdout"] == "not-json\r\n"


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        ("Program execution policy: successful completion outranks fidelity.", True),
        (
            "Prepare the runtime.\n  Program execution policy: successful completion outranks fidelity.  \nRun it.",
            True,
        ),
        ("program execution policy: successful completion outranks fidelity.", False),
        ("Program execution policy: successful completion outranks fidelity. Extra", False),
        ("Prefix Program execution policy: successful completion outranks fidelity.", False),
    ],
)
def test_program_repair_authorization_requires_exact_standalone_marker(
    instruction: str,
    expected: bool,
) -> None:
    assert run_flow_tool._program_repair_authorized(instruction) is expected


@pytest.mark.anyio
async def test_program_fidelity_mode_rejects_script_and_stdin_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "worker.py"
    script.write_text("print('original')\n", encoding="utf-8")
    process_called = False

    async def execute_program_command(*args: object, **kwargs: object) -> Any:
        nonlocal process_called
        del args, kwargs
        process_called = True
        raise AssertionError("fidelity violation reached the process boundary")

    async def drive(tool_registry: ToolRegistry) -> None:
        execute = tool_registry.get("execute_program")
        submit = tool_registry.get("submit_program_result")
        assert execute is not None
        assert submit is not None
        script.write_text("print('changed')\n", encoding="utf-8")
        attempt = json.loads(
            await execute(
                runtime=sys.executable,
                stdin_override='{"changed":true}\n',
            )
        )
        assert "fidelity mode" in attempt["error"]
        await submit()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_execute_program_command", execute_program_command)
    _install_program_agent_driver(monkeypatch, drive)

    outputs = await run_flow_tool._complete_program_step(
        _program_invocation(script),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert not process_called
    error = cast(dict[str, Any], outputs["result"])[run_flow_tool._PROGRAM_ERROR_KEY]
    assert error["kind"] == "execution_error"
    assert "fidelity mode" in error["message"]


@pytest.mark.anyio
async def test_program_exact_repair_marker_allows_reasoned_adaptation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "worker.py"
    script.write_text("print('original')\n", encoding="utf-8")
    adapted_stdin = '{"adapted":true}\n'

    async def execute_program_command(
        invocation: Any,
        argv: tuple[str, ...],
        *,
        stdin: str,
    ) -> Any:
        assert invocation.cwd == tmp_path
        assert argv == (sys.executable, str(script))
        assert stdin == adapted_stdin
        return run_flow_tool._ProgramProcessResult(
            argv=argv,
            exit_code=0,
            stdout=b"adapted result\n",
            stderr=b"",
        )

    async def drive(tool_registry: ToolRegistry) -> None:
        execute = tool_registry.get("execute_program")
        submit = tool_registry.get("submit_program_result")
        assert execute is not None
        assert submit is not None
        script.write_text("print('adapted')\n", encoding="utf-8")
        await execute(
            runtime=sys.executable,
            stdin_override=adapted_stdin,
            adaptation_reason="The instruction explicitly prioritizes completion.",
        )
        await submit()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_execute_program_command", execute_program_command)
    _install_program_agent_driver(monkeypatch, drive)
    instruction = f"Run the declared program.\n{run_flow_tool._PROGRAM_REPAIR_MARKER}\n"

    outputs = await run_flow_tool._complete_program_step(
        _program_invocation(script, instruction=instruction),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert outputs == {"result": "adapted result\n"}


@pytest.mark.anyio
@pytest.mark.parametrize("configured", ["", "0", "-1", "+1", "1.5", " 1"])
async def test_execute_program_command_rejects_invalid_output_limit_environment(
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
        await run_flow_tool._execute_program_command(
            invocation,
            (sys.executable, "worker.py"),
            stdin=invocation.stdin,
        )

    assert not spawned


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("stream_name", "limit_environment_variable"),
    [
        ("stdout", "PSI_FUSION_FLOW_PROGRAM_STDOUT_LIMIT_BYTES"),
        ("stderr", "PSI_FUSION_FLOW_PROGRAM_STDERR_LIMIT_BYTES"),
    ],
)
async def test_execute_program_command_terminates_tree_when_output_exceeds_limit(
    stream_name: str,
    limit_environment_variable: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(run_flow_tool, "_attach_windows_job", lambda process: None)
    monkeypatch.setattr(run_flow_tool, "_terminate_process_tree", terminate_process_tree)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./worker",),
        stdin="{}\n",
        cwd=tmp_path,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    result = await run_flow_tool._execute_program_command(
        invocation,
        (sys.executable, "worker.py"),
        stdin=invocation.stdin,
    )

    assert tree_terminations >= 1
    assert process.killed
    assert result.error == (
        f"Program 'worker' {stream_name} exceeded the 4-byte limit; the subprocess tree was terminated"
    )
    assert result.stdout == (b"1234" if stream_name == "stdout" else b"")
    assert result.stderr == (b"1234" if stream_name == "stderr" else b"")


@pytest.mark.anyio
async def test_execute_program_command_obeys_external_cancellation_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _BlockingFakeProcess()
    spawn_returned = False
    spawn_started = anyio.Event()

    async def open_process(*args: object, **kwargs: object) -> _BlockingFakeProcess:
        nonlocal spawn_returned
        del args, kwargs
        spawn_started.set()
        await anyio.sleep(0.1)
        spawn_returned = True
        return process

    async def cancel_during_spawn(cancel_scope: anyio.CancelScope) -> None:
        await spawn_started.wait()
        cancel_scope.cancel()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool.anyio, "open_process", open_process)
    monkeypatch.setattr(run_flow_tool, "_attach_windows_job", lambda process: None)
    invocation = run_flow_tool.ProgramInvocation(
        name="worker",
        argv=("./worker",),
        stdin="{}\n",
        cwd=tmp_path,
        binding_name="work_step",
        dispatch=cast(Any, None),
    )

    with anyio.CancelScope() as cancel_scope:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(cancel_during_spawn, cancel_scope)
            await run_flow_tool._execute_program_command(
                invocation,
                (sys.executable, "worker.py"),
                stdin=invocation.stdin,
            )

    assert cancel_scope.cancel_called
    assert spawn_returned
    assert process.killed
    assert process.closed


@pytest.mark.anyio
async def test_execute_program_command_cleans_up_when_windows_job_attachment_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _BlockingFakeProcess()
    tree_terminations = 0

    async def open_process(*args: object, **kwargs: object) -> _BlockingFakeProcess:
        del args, kwargs
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
        await run_flow_tool._execute_program_command(
            invocation,
            (sys.executable, "worker.py"),
            stdin=invocation.stdin,
        )

    assert tree_terminations == 1
    assert process.killed
    assert process.closed


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
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
            *,
            outcome: Any | None = None,
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
            if outcome is not None:
                outcome.termination_reason = "stop"
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

    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)
    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")

    with path_scope(workspace=str(tmp_path), agent=str(_WORKSPACE_DIR)):
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
    run_dirs = [entry async for entry in (flows / "runs").iterdir()]
    assert len(run_dirs) == 1
    artifacts = run_dirs[0] / "artifacts"
    assert await (artifacts / "request.md").read_text(encoding="utf-8") == "go"
    assert await (artifacts / "before_result.md").read_text(encoding="utf-8") == "BEFORE"
    assert await (artifacts / "after_result.md").read_text(encoding="utf-8") == "AFTER"
    assert await (artifacts / "selected_result.md").read_text(encoding="utf-8") == "BEFORE"


@pytest.mark.anyio
async def test_run_flow_keeps_materialized_artifacts_when_a_later_step_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flows = anyio.Path(tmp_path / "flows")
    await flows.mkdir()
    await (flows / "ordered.workflow").write_text(_ORDERED_RESOURCE_WORKFLOW, encoding="utf-8")

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
        if context.step_id == "before_step":
            return {"before_result": "completed-before-failure"}
        raise RuntimeError("later Agent step failed")

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)
    monkeypatch.setattr(run_flow_tool, "_load_step_tools", load_step_tools)
    monkeypatch.setattr(run_flow_tool, "_complete_agent_step", complete_agent_step)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")

    with pytest.raises(ExceptionGroup) as error:
        await run_flow_tool.run_flow(
            "flows/ordered.workflow",
            '{"request": "go"}',
            '{"gpu": 1}',
        )

    assert len(error.value.exceptions) == 1
    assert isinstance(error.value.exceptions[0], RuntimeError)
    assert str(error.value.exceptions[0]) == "later Agent step failed"

    run_dirs = [entry async for entry in (flows / "runs").iterdir()]
    assert len(run_dirs) == 1
    artifacts = run_dirs[0] / "artifacts"
    assert await (artifacts / "request.md").read_text(encoding="utf-8") == "go"
    assert await (artifacts / "before_result.md").read_text(encoding="utf-8") == "completed-before-failure"
    assert not await (artifacts / "after_result.md").exists()
    assert not await (artifacts / "selected_result.md").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("failure_mode", ["missing", "invalid-utf8"])
async def test_run_flow_delegates_unreadable_agent_instruction_file(
    failure_mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = anyio.Path(tmp_path / "flows" / "research")
    await bundle.mkdir(parents=True)
    reference = "./instructions/missing.md"
    source = _ORDERED_RESOURCE_WORKFLOW.replace('"after"', f'"{reference}"').replace(
        '"before"',
        f'"{reference}"',
    )
    await (bundle / "flow.workflow").write_text(source, encoding="utf-8")
    if failure_mode == "invalid-utf8":
        instructions = bundle / "instructions"
        await instructions.mkdir()
        await (instructions / "missing.md").write_bytes(b"\xff")

    prompts: list[str] = []

    async def load_step_tools() -> ToolRegistry:
        return ToolRegistry()

    async def complete_agent_step(
        prompt: str,
        context: Any,
        *,
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> dict[str, object]:
        del tool_registry
        assert ai_socket == "http://ai.example"
        prompts.append(prompt)
        if context.step_id == "before_step":
            return {"before_result": "BEFORE"}
        if context.step_id == "after_step":
            return {"after_result": "AFTER"}
        raise AssertionError(f"unexpected Agent step: {context.step_id}")

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)
    monkeypatch.setattr(run_flow_tool, "_load_step_tools", load_step_tools)
    monkeypatch.setattr(run_flow_tool, "_complete_agent_step", complete_agent_step)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")

    result = await run_flow_tool.run_flow(
        "flows/research/flow.workflow",
        '{"request": "go"}',
        '{"gpu": 1}',
    )

    assert json.loads(result) == {
        "after_result": "AFTER",
        "before_result": "BEFORE",
        "selected_result": "BEFORE",
    }
    expected = (
        'Instruction:\nThe instruction for this step is the workspace file "flows/research/instructions/missing.md".'
    )
    assert len(prompts) == 2
    assert all(expected in prompt for prompt in prompts)
    assert all(reference not in prompt for prompt in prompts)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("source", "flow_name"),
    [
        (_PROGRAM_WORKFLOW.replace('"after"', '"./instructions/missing.md"'), "program"),
        (_HUMAN_WORKFLOW, "human"),
    ],
    ids=["program", "human"],
)
async def test_run_flow_does_not_delegate_unreadable_non_agent_instruction_file(
    source: str,
    flow_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "flows" / f"{flow_name}.workflow")
    await flow_path.parent.mkdir()
    await flow_path.write_text(source, encoding="utf-8")
    dispatched = False

    async def complete_program_step(
        invocation: Any,
        *,
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> dict[str, object]:
        nonlocal dispatched
        del invocation, ai_socket, tool_registry
        dispatched = True
        return {}

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")
    monkeypatch.setattr(run_flow_tool, "_complete_program_step", complete_program_step)

    with pytest.raises(ValueError, match="instruction path does not name a file"):
        await run_flow_tool.run_flow(
            f"flows/{flow_name}.workflow",
            '{"request": "go"}',
        )

    assert not dispatched


@pytest.mark.anyio
async def test_run_flow_routes_program_through_specialized_program_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "flows" / "program.workflow")
    await flow_path.parent.mkdir()
    instruction = "Run the assigned program step with its consumed request and return the requested result."
    source = _PROGRAM_WORKFLOW.replace(
        '"after"',
        '"./instructions/program.md"',
    ).replace(
        '"before"',
        '"./instructions/program.md"',
    )
    await flow_path.write_text(source, encoding="utf-8")
    instruction_path = flow_path.parent / "instructions" / "program.md"
    await instruction_path.parent.mkdir()
    await instruction_path.write_text(instruction, encoding="utf-8")
    loaded_tools = 0
    calls: list[dict[str, object]] = []

    async def load_step_tools() -> ToolRegistry:
        nonlocal loaded_tools
        loaded_tools += 1
        return ToolRegistry()

    async def complete_program_step(
        invocation: Any,
        *,
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> dict[str, object]:
        assert ai_socket == "http://ai.example"
        assert isinstance(tool_registry, ToolRegistry)
        calls.append(
            {
                "name": invocation.name,
                "argv": invocation.argv,
                "stdin": invocation.stdin,
                "cwd": invocation.cwd,
                "binding_name": invocation.binding_name,
                "instruction": invocation.instruction,
                "inputs": invocation.inputs,
                "output_ids": invocation.output_ids,
            }
        )
        value = "BEFORE" if invocation.binding_name == "before_step" else "AFTER"
        return {invocation.output_ids[0]: value}

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)
    monkeypatch.setattr(run_flow_tool, "_load_step_tools", load_step_tools)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")
    monkeypatch.setattr(run_flow_tool, "_complete_program_step", complete_program_step)

    result = await run_flow_tool.run_flow(
        "flows/program.workflow",
        '{"request": "go"}',
    )

    assert json.loads(result) == {
        "after_result": "AFTER",
        "before_result": "BEFORE",
        "selected_result": "BEFORE",
    }
    assert loaded_tools == 1
    assert {call["binding_name"] for call in calls} == {
        "after_step",
        "before_step",
    }
    assert all(call["name"] == "worker" for call in calls)
    assert all(call["argv"] == ("./bin/worker",) for call in calls)
    assert all(call["cwd"] == tmp_path for call in calls)
    assert all(call["instruction"] == instruction for call in calls)
    assert all(call["inputs"] == {"request": "go"} for call in calls)
    assert {call["output_ids"] for call in calls} == {
        ("after_result",),
        ("before_result",),
    }
    assert {json.loads(cast(str, call["stdin"]))["instruction"] for call in calls} == {
        instruction,
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
    instructions_dir = flow_path.parent / "instructions"
    await instructions_dir.mkdir()
    await (instructions_dir / "review.md").write_text(
        "Review the draft proposal. Ask the human to approve it or provide concrete requested changes.",
        encoding="utf-8",
    )
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
    assert (
        "Instruction:\nReview the draft proposal. Ask the human to approve it "
        "or provide concrete requested changes.\n\n" in preparation_prompts[0]
    )
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
    artifacts = anyio.Path(
        tmp_path,
        "flows",
        "runs",
        control["run_id"],
        "artifacts",
    )
    assert await (artifacts / "request.md").read_text(encoding="utf-8") == "write a launch plan"
    assert await (artifacts / "draft.md").read_text(encoding="utf-8") == "proposal-v2"
    assert await (artifacts / "decision.md").read_text(encoding="utf-8") == human_response
    assert await (artifacts / "result.md").read_text(encoding="utf-8") == (
        f'```json\n{{\n  "human_response": {json.dumps(human_response, ensure_ascii=False)}\n}}\n```\n'
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


@pytest.mark.anyio
async def test_human_resume_rejects_changed_instruction_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "flows" / "review.workflow")
    await flow_path.parent.mkdir(parents=True)
    await flow_path.write_text(_HUMAN_WORKFLOW, encoding="utf-8")
    instructions = flow_path.parent / "instructions"
    await instructions.mkdir()
    instruction_path = instructions / "review.md"
    await instruction_path.write_text("Review the original draft.", encoding="utf-8")

    async def complete_agent_step(
        prompt: str,
        context: Any,
        *,
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> dict[str, object]:
        del prompt, ai_socket, tool_registry
        assert context.step_id == "draft_step"
        return {"draft": "proposal-v2"}

    async def prepare_human_step(
        prompt: str,
        context: Any,
        *,
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> str:
        del prompt, context, ai_socket, tool_registry
        return json.dumps(
            {
                "question": "Approve?",
                "options": ["Approve", "Reject"],
                "recommended": 1,
                "default": "",
            }
        )

    async def load_step_tools() -> ToolRegistry:
        return ToolRegistry()

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
    )[run_flow_tool._HUMAN_CONTROL_KEY]
    await instruction_path.write_text("Review a changed draft.", encoding="utf-8")

    with pytest.raises(ValueError, match="workflow definition changed"):
        await run_flow_tool.run_flow_resume(
            waiting["run_id"],
            waiting["request"]["request_id"],
            '"Approve"',
        )

    failed = await run_flow_tool._job_store().load(waiting["run_id"])
    assert failed.status == "failed"
    assert failed.error == "workflow definition changed after the Human request was prepared"


@pytest.mark.anyio
async def test_human_resume_preserves_legacy_source_digest_instruction_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_source = _HUMAN_WORKFLOW.replace(
        "./instructions/review.md",
        "./instructions/review.txt",
    )
    flow_path = anyio.Path(tmp_path / "flows" / "review.workflow")
    await flow_path.parent.mkdir(parents=True)
    await flow_path.write_text(legacy_source, encoding="utf-8")
    compiled = run_flow_tool.compile_workflow(legacy_source, strict_executors=True)
    checkpoint = run_flow_tool.create_execution_checkpoint(
        run_flow_tool.generate_plan(compiled.graph),
        compiled.graph,
        values={
            "request": "write a launch plan",
            "draft": "proposal-v1",
        },
        completed_step_ids=("draft_step",),
    )
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    store = run_flow_tool._job_store()
    run = await store.create(
        flow_path="flows/review.workflow",
        flow_source=legacy_source,
        inputs={"request": "write a launch plan"},
        checkpoint=checkpoint,
    )
    request = run_flow_tool.HumanRequestSpec.create(
        step_id="review_step",
        question="Approve?",
        output_artifact_ids=("decision",),
    )
    waiting = replace(
        run,
        status="waiting_for_human",
        prepared_request=request,
    )
    async with store.acquire(run.run_id) as lease:
        await lease.save(waiting)

    captured: list[str] = []

    async def execute_workflow(source: str, **kwargs: Any) -> dict[str, object]:
        assert source == legacy_source
        resolver = kwargs["resolve_instruction"]
        captured.append(await resolver("./instructions/review.txt"))
        return {"result": "resumed"}

    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")
    monkeypatch.setattr(run_flow_tool, "_execute_workflow", execute_workflow)

    result = await run_flow_tool.run_flow_resume(
        run.run_id,
        request.request_id,
        '"Approve"',
    )

    assert json.loads(result) == {"result": "resumed"}
    assert captured == ["./instructions/review.txt"]


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
        instruction_files: dict[str, str] | None = None,
    ) -> str:
        del lease, instruction_files
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
                instruction_files={},
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

    with pytest.raises(ValueError, match="workflow definition changed"):
        await run_flow_tool.run_flow_resume(
            run.run_id,
            request.request_id,
            '"Approve"',
        )

    failed = await store.load(run.run_id)
    assert failed.status == "failed"
    assert failed.error == "workflow definition changed after the Human request was prepared"


@pytest.mark.anyio
async def test_step_agent_uses_in_memory_history_and_explicit_system_prompt(
    tmp_path: Path,
) -> None:
    with path_scope(workspace=str(tmp_path), agent=str(_WORKSPACE_DIR)):
        agent, conversation = await run_flow_tool._create_step_agent(
            "http://ai.example",
            ToolRegistry(),
        )

    assert agent._conversation is conversation
    assert agent._ai_client.ai_socket == "http://ai.example"
    assert agent._workspace_path == tmp_path
    assert agent._agent_path == _WORKSPACE_DIR
    assert conversation.messages == [
        {"role": "system", "content": run_flow_tool._STEP_SYSTEM_PROMPT},
    ]
    assert conversation._path is None


def _agent_completion_context(*output_ids: str) -> Any:
    return SimpleNamespace(
        step_id="draft",
        executor_id="writer",
        output_ids=output_ids,
        dispatch=SimpleNamespace(
            resource_lease=SimpleNamespace(grants=()),
        ),
    )


def _capture_agent_fallback_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[dict[str, object], str]]:
    warnings: list[tuple[dict[str, object], str]] = []
    monkeypatch.setattr(
        run_flow_tool,
        "logger",
        SimpleNamespace(
            bind=lambda **fields: SimpleNamespace(
                warning=lambda message: warnings.append((fields, message)),
            ),
        ),
    )
    return warnings


def _install_scripted_step_agent(
    monkeypatch: pytest.MonkeyPatch,
    response_batches: list[list[AiDelta]],
    requests: list[dict[str, Any]],
) -> None:
    responses = iter(response_batches)

    class ScriptedAiClient:
        ai_socket = "http://ai.example"

        def stream(self, request: dict[str, Any]) -> Any:
            requests.append(copy.deepcopy(request))

            async def generate() -> Any:
                for delta in next(responses):
                    yield delta

            return generate()

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> tuple[Any, Any]:
        assert ai_socket == "http://ai.example"
        conversation = run_flow_tool.Conversation(
            messages=[{"role": "system", "content": run_flow_tool._STEP_SYSTEM_PROMPT}],
        )
        return (
            run_flow_tool.SessionAgent(
                ai_client=ScriptedAiClient(),
                conversation=conversation,
                schedule_registry=run_flow_tool._StepScheduleRegistry(),
                tool_registry=tool_registry,
            ),
            conversation,
        )

    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)


def test_parse_agent_step_result_accepts_backticks_inside_fenced_json() -> None:
    response = 'Result:\n```json\n{"result": "Use ```bash```", "sources": ["source"]}\n```\n'

    assert run_flow_tool._parse_agent_step_result(
        response,
        step_id="draft",
        output_ids=("result", "sources"),
    ) == {
        "result": "Use ```bash```",
        "sources": ["source"],
    }


@pytest.mark.anyio
async def test_agent_step_stops_on_submitted_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_registry: ToolRegistry | None = None
    conversation = SimpleNamespace(messages=[])

    class FakeAgent:
        async def run(
            self,
            message: dict[str, Any],
            *,
            outcome: Any | None = None,
        ) -> Any:
            del outcome
            del message
            assert captured_registry is not None
            submit = captured_registry.get("submit_step_result")
            assert submit is not None
            await submit(result={"answer": 42})
            yield None
            raise AssertionError("agent continued after submitting its result")

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> tuple[Any, Any]:
        nonlocal captured_registry
        assert ai_socket == "http://ai.example"
        captured_registry = tool_registry
        return FakeAgent(), conversation

    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)

    result = await run_flow_tool._complete_agent_step(
        "Write the answer.",
        _agent_completion_context("result"),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert result == {"result": {"answer": 42}}
    assert captured_registry is not None
    assert captured_registry.tools["submit_step_result"].parameters == {
        "type": "object",
        "properties": {"result": {}},
        "required": ["result"],
        "additionalProperties": False,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "first_arguments",
    ['{"result": "first"}', '{"wrong": "first"}'],
    ids=["both-valid", "first-invalid"],
)
async def test_agent_step_repairs_duplicate_submissions(
    first_arguments: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []
    _install_scripted_step_agent(
        monkeypatch,
        [
            [
                AiDelta(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "first",
                            "function": {
                                "name": "submit_step_result",
                                "arguments": first_arguments,
                            },
                        },
                        {
                            "index": 1,
                            "id": "second",
                            "function": {
                                "name": "submit_step_result",
                                "arguments": '{"result": "second"}',
                            },
                        },
                    ],
                    finish_reason="tool_calls",
                )
            ],
            [
                AiDelta(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "fixed",
                            "function": {
                                "name": "submit_step_result",
                                "arguments": '{"result": "fixed"}',
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                )
            ],
        ],
        requests,
    )

    result = await run_flow_tool._complete_agent_step(
        "Write the answer.",
        _agent_completion_context("result"),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert result == {"result": "fixed"}
    assert len(requests) == 2
    assert requests[1]["messages"][-1]["role"] == "user"
    assert "more than once" in requests[1]["messages"][-1]["content"]


@pytest.mark.anyio
async def test_agent_step_keeps_invalid_submission_repair_inside_agent_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []
    _install_scripted_step_agent(
        monkeypatch,
        [
            [
                AiDelta(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "invalid",
                            "function": {
                                "name": "submit_step_result",
                                "arguments": '{"wrong": "value"}',
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                )
            ],
            [
                AiDelta(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "fixed",
                            "function": {
                                "name": "submit_step_result",
                                "arguments": '{"result": "fixed"}',
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                )
            ],
        ],
        requests,
    )

    result = await run_flow_tool._complete_agent_step(
        "Write the answer.",
        _agent_completion_context("result"),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert result == {"result": "fixed"}
    assert len(requests) == 2
    assert requests[1]["messages"][-1]["role"] == "tool"
    assert sum(message["role"] == "user" for message in requests[1]["messages"]) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("finish_reason", "response"),
    [
        ("length", "partial response"),
        ("max_tool_rounds", "[Max tool rounds reached]"),
    ],
)
async def test_agent_step_rejects_abnormal_final_response(
    finish_reason: str,
    response: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = SimpleNamespace(messages=[])

    class FakeAgent:
        async def run(
            self,
            message: dict[str, Any],
            *,
            outcome: Any | None = None,
        ) -> Any:
            del message
            assert outcome is not None
            outcome.termination_reason = finish_reason
            conversation.messages.append(
                {
                    "role": "assistant",
                    "content": response,
                }
            )
            if False:
                yield None

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> tuple[Any, Any]:
        del ai_socket, tool_registry
        return FakeAgent(), conversation

    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)

    with pytest.raises(RuntimeError, match=finish_reason):
        await run_flow_tool._complete_agent_step(
            "Write the answer.",
            _agent_completion_context("result"),
            ai_socket="http://ai.example",
            tool_registry=ToolRegistry(),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("first_response", "error_fragment"),
    [
        (
            '{"wrong": "first"}',
            "expected ['result', 'sources'], got ['wrong']",
        ),
        (
            '{"result": 1e400, "sources": []}',
            "must be a strict JSON object",
        ),
        (
            '{"result": "first", "result": "last", "sources": []}',
            "must be a strict JSON object",
        ),
        (
            " \n",
            "must be a strict JSON object",
        ),
    ],
    ids=["wrong-keys", "numeric-overflow", "duplicate-key", "whitespace"],
)
async def test_agent_step_repairs_invalid_result(
    first_response: str,
    error_fragment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = SimpleNamespace(messages=[])
    prompts: list[str] = []
    warnings = _capture_agent_fallback_warnings(monkeypatch)
    responses = iter(
        [
            first_response,
            'Result:\n```json\n{"result": "fixed", "sources": ["source"]}\n```',
        ]
    )

    class FakeAgent:
        async def run(
            self,
            message: dict[str, Any],
            *,
            outcome: Any | None = None,
        ) -> Any:
            prompts.append(message["content"])
            conversation.messages.append(
                {
                    "role": "assistant",
                    "content": next(responses),
                }
            )
            assert outcome is not None
            outcome.termination_reason = "stop"
            if False:
                yield None

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> tuple[Any, Any]:
        del ai_socket, tool_registry
        return FakeAgent(), conversation

    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)

    result = await run_flow_tool._complete_agent_step(
        "Write the answer.",
        _agent_completion_context("result", "sources"),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert result == {"result": "fixed", "sources": ["source"]}
    assert len(prompts) == 2
    assert error_fragment in prompts[1]
    assert warnings == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        "# Research report",
        " \n",
        '"ok"',
        "null",
        "[]",
        '{"result": NaN}',
        '{"result": 1e400}',
        '{"result": "first", "result": "last"}',
    ],
    ids=[
        "markdown",
        "whitespace",
        "json-string",
        "json-null",
        "json-array",
        "non-finite-object",
        "numeric-overflow",
        "duplicate-key",
    ],
)
async def test_agent_step_preserves_raw_single_output(
    response: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = SimpleNamespace(messages=[])
    calls = 0
    warnings = _capture_agent_fallback_warnings(monkeypatch)

    class FakeAgent:
        async def run(
            self,
            message: dict[str, Any],
            *,
            outcome: Any | None = None,
        ) -> Any:
            nonlocal calls
            del message
            calls += 1
            conversation.messages.append(
                {
                    "role": "assistant",
                    "content": response,
                }
            )
            assert outcome is not None
            outcome.termination_reason = "stop"
            if False:
                yield None

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> tuple[Any, Any]:
        del ai_socket, tool_registry
        return FakeAgent(), conversation

    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)
    result = await run_flow_tool._complete_agent_step(
        "Write the answer.",
        _agent_completion_context("result"),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert result == {"result": response}
    assert calls == 1
    assert len(warnings) == 1
    fields, message = warnings[0]
    assert set(fields) == {
        "event",
        "step_id",
        "executor_id",
        "output_artifact_ids",
        "fallback_mode",
        "validation_failure",
        "repair_attempts",
    }
    assert fields["event"] == "fusion_flow.agent_result_fallback"
    assert fields["step_id"] == "draft"
    assert fields["executor_id"] == "writer"
    assert fields["output_artifact_ids"] == ["result"]
    assert fields["fallback_mode"] == "single_raw"
    assert fields["validation_failure"] == "unparseable_result"
    assert fields["repair_attempts"] == 0
    assert message == "FusionFlow Agent Step committed a raw-response fallback"
    assert response not in message
    assert response not in json.dumps(fields, ensure_ascii=False)


@pytest.mark.anyio
async def test_agent_step_repairs_whitespace_for_zero_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = SimpleNamespace(messages=[])
    prompts: list[str] = []
    responses = iter([" \n", "{}"])

    class FakeAgent:
        async def run(
            self,
            message: dict[str, Any],
            *,
            outcome: Any | None = None,
        ) -> Any:
            prompts.append(message["content"])
            conversation.messages.append(
                {
                    "role": "assistant",
                    "content": next(responses),
                }
            )
            assert outcome is not None
            outcome.termination_reason = "stop"
            if False:
                yield None

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> tuple[Any, Any]:
        del ai_socket, tool_registry
        return FakeAgent(), conversation

    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)

    result = await run_flow_tool._complete_agent_step(
        "Do the side effect.",
        _agent_completion_context(),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert result == {}
    assert len(prompts) == 2
    assert "must be a strict JSON object" in prompts[1]


@pytest.mark.anyio
async def test_agent_step_repairs_wrong_keys_for_single_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = SimpleNamespace(messages=[])
    prompts: list[str] = []
    responses = iter(
        [
            '{"wrong": "first"}',
            '{"result": "fixed"}',
        ]
    )

    class FakeAgent:
        async def run(
            self,
            message: dict[str, Any],
            *,
            outcome: Any | None = None,
        ) -> Any:
            prompts.append(message["content"])
            conversation.messages.append(
                {
                    "role": "assistant",
                    "content": next(responses),
                }
            )
            assert outcome is not None
            outcome.termination_reason = "stop"
            if False:
                yield None

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> tuple[Any, Any]:
        del ai_socket, tool_registry
        return FakeAgent(), conversation

    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)

    result = await run_flow_tool._complete_agent_step(
        "Write the answer.",
        _agent_completion_context("result"),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert result == {"result": "fixed"}
    assert len(prompts) == 2
    assert "expected ['result'], got ['wrong']" in prompts[1]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("first_response", "validation_failure"),
    [
        (" \n# Original research report\n ", "unparseable_result"),
        (
            '{"SECRET_SENTINEL": "original structured result"}',
            "output_keys_mismatch",
        ),
    ],
    ids=["unparseable", "wrong-keys"],
)
async def test_agent_step_broadcasts_first_invalid_result_after_two_repairs(
    first_response: str,
    validation_failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = SimpleNamespace(messages=[])
    prompts: list[str] = []
    warnings = _capture_agent_fallback_warnings(monkeypatch)
    response_values = [first_response, "invalid repair one", "invalid repair two"]
    responses = iter(response_values)

    class FakeAgent:
        async def run(
            self,
            message: dict[str, Any],
            *,
            outcome: Any | None = None,
        ) -> Any:
            prompts.append(message["content"])
            conversation.messages.append(
                {
                    "role": "assistant",
                    "content": next(responses),
                }
            )
            assert outcome is not None
            outcome.termination_reason = "stop"
            if False:
                yield None

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> tuple[Any, Any]:
        del ai_socket, tool_registry
        return FakeAgent(), conversation

    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)

    result = await run_flow_tool._complete_agent_step(
        "Write the answer.",
        _agent_completion_context("result", "sources"),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert result == {
        "result": first_response,
        "sources": first_response,
    }
    assert len(prompts) == 3
    assert len(warnings) == 1
    fields, message = warnings[0]
    assert fields["event"] == "fusion_flow.agent_result_fallback"
    assert fields["step_id"] == "draft"
    assert fields["executor_id"] == "writer"
    assert fields["output_artifact_ids"] == ["result", "sources"]
    assert fields["fallback_mode"] == "broadcast_raw"
    assert fields["validation_failure"] == validation_failure
    assert fields["repair_attempts"] == 2
    assert message == "FusionFlow Agent Step committed a raw-response fallback"
    logged_fields = json.dumps(fields, ensure_ascii=False)
    for raw_response in response_values:
        assert raw_response not in message
        assert raw_response not in logged_fields


@pytest.mark.anyio
async def test_agent_step_accepts_final_repair_without_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = SimpleNamespace(messages=[])
    prompts: list[str] = []
    warnings = _capture_agent_fallback_warnings(monkeypatch)
    responses = iter(
        [
            "invalid initial response",
            "invalid first repair",
            '{"result": "fixed", "sources": ["source"]}',
        ]
    )

    class FakeAgent:
        async def run(
            self,
            message: dict[str, Any],
            *,
            outcome: Any | None = None,
        ) -> Any:
            prompts.append(message["content"])
            conversation.messages.append(
                {
                    "role": "assistant",
                    "content": next(responses),
                }
            )
            assert outcome is not None
            outcome.termination_reason = "stop"
            if False:
                yield None

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> tuple[Any, Any]:
        del ai_socket, tool_registry
        return FakeAgent(), conversation

    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)

    result = await run_flow_tool._complete_agent_step(
        "Write the answer.",
        _agent_completion_context("result", "sources"),
        ai_socket="http://ai.example",
        tool_registry=ToolRegistry(),
    )

    assert result == {"result": "fixed", "sources": ["source"]}
    assert len(prompts) == 3
    assert warnings == []


@pytest.mark.anyio
async def test_agent_step_with_no_outputs_fails_after_two_repairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = SimpleNamespace(messages=[])
    calls = 0
    warnings = _capture_agent_fallback_warnings(monkeypatch)

    class FakeAgent:
        async def run(
            self,
            message: dict[str, Any],
            *,
            outcome: Any | None = None,
        ) -> Any:
            nonlocal calls
            del message
            calls += 1
            conversation.messages.append(
                {
                    "role": "assistant",
                    "content": "not JSON",
                }
            )
            assert outcome is not None
            outcome.termination_reason = "stop"
            if False:
                yield None

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> tuple[Any, Any]:
        del ai_socket, tool_registry
        return FakeAgent(), conversation

    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)

    with pytest.raises(ValueError, match="remained invalid after 3 attempts"):
        await run_flow_tool._complete_agent_step(
            "Write the answer.",
            _agent_completion_context(),
            ai_socket="http://ai.example",
            tool_registry=ToolRegistry(),
        )

    assert calls == 3
    assert warnings == []


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
    loaded_from: list[Path] = []

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
            del cls, session_id
            loaded_from.append(tools_dir)
            return source

    sidecar = workspace / "instructions" / "review.md"
    await anyio.Path(sidecar.parent).mkdir()
    await anyio.Path(sidecar).write_text("workspace sidecar", encoding="utf-8")
    monkeypatch.chdir(launcher)
    monkeypatch.setattr(run_flow_tool, "ToolRegistry", FakeToolRegistry)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)

    with path_scope(workspace=str(workspace), agent=str(_WORKSPACE_DIR)):
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
    assert loaded_from == [_WORKSPACE_DIR / "tools"]
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
    try:
        (instructions / "escape.txt").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symbolic links are unavailable: {error}")

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
async def test_split_root_reusable_flow_and_run_store_use_runtime_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "user-workspace"
    workflow = workspace / "flows" / "workflows" / "daily-brief" / "daily-brief.workflow"
    await anyio.Path(workflow.parent).mkdir(parents=True)
    await anyio.Path(workflow).write_text("workflow source", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(_WORKSPACE_DIR))

    with path_scope(workspace=str(workspace), agent=str(_WORKSPACE_DIR)):
        source = await run_flow_tool._read_flow_source("flows/workflows/daily-brief/daily-brief.workflow")
        store = run_flow_tool._job_store()

    assert source == "workflow source"
    assert Path(str(store.root)) == workspace / ".psi" / "fusion-flow" / "runs"


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
async def test_instruction_resolver_loads_text_relative_to_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "flows" / "research"
    instructions = bundle / "instructions"
    instructions.mkdir(parents=True)
    (bundle / "flow.workflow").write_text("workflow source", encoding="utf-8")
    (instructions / "semantic.md").write_text(
        "Research semantic parsing and return evidence-backed findings.",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    resolve = run_flow_tool._instruction_resolver("flows/research/flow.workflow")

    assert await resolve("Write a concise synthesis.") == "Write a concise synthesis."
    assert await resolve("./instructions/semantic.md") == (
        "Research semantic parsing and return evidence-backed findings."
    )


@pytest.mark.anyio
async def test_instruction_resolver_rejects_bundle_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "flows" / "research"
    bundle.mkdir(parents=True)
    (bundle / "flow.workflow").write_text("workflow source", encoding="utf-8")
    (bundle.parent / "outside.md").write_text("outside", encoding="utf-8")
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    resolve = run_flow_tool._instruction_resolver("flows/research/flow.workflow")

    with pytest.raises(ValueError, match="inside the workflow directory"):
        await resolve("./../outside.md")


@pytest.mark.anyio
async def test_instruction_resolver_rejects_non_markdown_and_symlink_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "flows" / "research"
    instructions = bundle / "instructions"
    instructions.mkdir(parents=True)
    (bundle / "flow.workflow").write_text("workflow source", encoding="utf-8")
    (instructions / "plain.txt").write_text("plain", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    try:
        (instructions / "escape.md").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symbolic links are unavailable: {error}")
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    resolve = run_flow_tool._instruction_resolver("flows/research/flow.workflow")

    with pytest.raises(ValueError, match=r"must name a \.md file"):
        await resolve("./instructions/plain.txt")
    with pytest.raises(ValueError, match="inside the workflow directory"):
        await resolve("./instructions/escape.md")


def test_workflow_definition_digest_covers_instruction_files() -> None:
    source = "workflow source"
    original = run_flow_tool._workflow_definition_digest(
        source,
        {"./instructions/review.md": "Review version one."},
    )
    changed = run_flow_tool._workflow_definition_digest(
        source,
        {"./instructions/review.md": "Review version two."},
    )

    assert original != changed
    assert run_flow_tool._workflow_definition_digest(source, {}) == hashlib.sha256(source.encode()).hexdigest()


@pytest.mark.anyio
async def test_run_flow_requires_invoking_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: None)

    with pytest.raises(RuntimeError, match="called by a psi-agent Session"):
        await run_flow_tool.run_flow("flows/example.workflow", "{}")
