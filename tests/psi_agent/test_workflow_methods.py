from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPOSITORY_ROOT = Path(__file__).parents[2]
_WORKFLOW_SKILL = _REPOSITORY_ROOT / "agents" / "feishu" / "skills" / "workflow"
_WORKFLOW_TOOLS = _REPOSITORY_ROOT / "agents" / "feishu" / "tools"
sys.path.insert(0, str(_WORKFLOW_SKILL))

_workflow_execution = importlib.import_module("fusion_flow.workflow_execution")
_workflow_runner = importlib.import_module("fusion_flow.workflow_runner")

DispatchContext = _workflow_execution.DispatchContext
CompletionContext = _workflow_runner.CompletionContext
CompiledWorkflow = _workflow_runner.CompiledWorkflow
ProgramInvocation = _workflow_runner.ProgramInvocation
compile_workflow = _workflow_runner.compile_workflow
execute_workflow = _workflow_runner.execute_workflow


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _section(markdown: str, heading: str, *, level: int) -> str:
    marker = f"{'#' * level} {heading}\n"
    start = markdown.index(marker) + len(marker)
    next_heading = f"\n{'#' * level} "
    end = markdown.find(next_heading, start)
    return markdown[start:] if end == -1 else markdown[start:end]


def _single_step_source(executor_kind: str) -> str:
    program_path = '  program_path(worker) == "./worker.py";\n' if executor_kind == "Program" else ""
    return f"""
const source_value: Artifact;
const result_value: Artifact;
const work_step: Step;
const worker: {executor_kind}, Executor;

workflow value_transform {{
  input_workflow(value_transform) == [source_value];
  consumes(work_step) == [source_value];
  produces(work_step) == [result_value];
  output_workflow(value_transform) == [result_value];
  step_executor(work_step) == worker;
  step_name(work_step) == "Transform";
  step_instruction(work_step) == "Transform the value.";
{program_path}}}
"""


def test_authoring_guidance_defines_domain_neutral_planning_and_annotations() -> None:
    skill = (_WORKFLOW_SKILL / "SKILL.md").read_text(encoding="utf-8")
    planning = _section(skill, "Planning contract", level=3)

    for guidance in (
        "intent and concrete success condition",
        "every external input and final output Artifact",
        "each Step's single responsibility",
        "owner of every material constraint",
        "concurrency, timeout, retry, resource, and user-stated cost limits",
        "mechanically decidable constraints",
        "constraints that require judgment",
        "fan out independent work",
    ):
        assert guidance in planning
    assert "quality gate" not in planning.lower()
    assert "repair" not in planning.lower()

    annotations = " ".join(_section(skill, "Artifact Annotations", level=2).split())
    for guidance in (
        "free-form trailing comment",
        "ordinary text",
        "optional writing conventions, not syntax",
        "does not parse a type or schema",
        "unusual or incomplete annotation never makes the workflow invalid",
        "explicit deterministic Program validation Step",
    ):
        assert guidance in annotations


@pytest.mark.anyio
async def test_free_form_artifact_annotations_are_read_and_forwarded_without_validation() -> None:
    instruction = (
        "Summarize the supplied value. The strings -- not a comment and /* also not a comment */ "
        "are ordinary instruction content, as is @artifact instruction_only [array]:."
    )
    source = f"""
const source_document: Artifact; -- @Artifact source_document [dictionary] missing delimiter {{content: string}}
const detached_context: Artifact;
-- This detached comment is not an annotation.
const summary_sections: Artifact; /* [array maybe; suggested JSON: [{{\"heading\": \"...\"}}] */
const summarize_step: Step; -- @Artifact not_an_artifact [object]: ignored
const writer: Agent, Executor;

workflow document_summary {{
  input_workflow(document_summary) == [source_document, detached_context];
  consumes(summarize_step) == [source_document, detached_context];
  produces(summarize_step) == [summary_sections];
  output_workflow(document_summary) == [summary_sections];
  step_executor(summarize_step) == writer;
  step_name(summarize_step) == "Summarize";
  step_instruction(summarize_step) == {json.dumps(instruction)};
}}
"""
    expected_annotations = {
        "source_document": "@Artifact source_document [dictionary] missing delimiter {content: string}",
        "summary_sections": '[array maybe; suggested JSON: [{"heading": "..."}]',
    }

    compiled = compile_workflow(source)

    assert compiled.artifact_annotations == expected_annotations

    observed_prompt = ""
    observed_context: CompletionContext | None = None

    async def complete(prompt: str, context: CompletionContext) -> object:
        nonlocal observed_context, observed_prompt
        observed_prompt = prompt
        observed_context = context
        return {"summary_sections": "plain text despite the suggested array shape"}

    outputs = await execute_workflow(
        source,
        inputs={
            "source_document": ["a list despite the suggested dictionary shape"],
            "detached_context": "ordinary context",
        },
        complete=complete,
    )

    assert outputs == {"summary_sections": "plain text despite the suggested array shape"}
    assert observed_context is not None
    assert observed_context.input_annotations == {"source_document": expected_annotations["source_document"]}
    assert observed_context.output_annotations == {"summary_sections": expected_annotations["summary_sections"]}
    assert expected_annotations["source_document"] in observed_prompt
    assert (
        json.dumps(
            {"summary_sections": expected_annotations["summary_sections"]},
            ensure_ascii=False,
            sort_keys=True,
        )
        in observed_prompt
    )
    assert set(compiled.artifact_annotations) == {"source_document", "summary_sections"}


@pytest.mark.anyio
async def test_missing_annotations_preserve_existing_agent_human_and_program_contracts() -> None:
    source = _single_step_source("Agent")
    compiled = compile_workflow(source)
    legacy_positional = CompiledWorkflow(
        compiled.graph,
        compiled.executor_kinds,
        compiled.program_paths,
        compiled.agent_configs,
        compiled.diagnostics,
    )
    assert legacy_positional.diagnostics == compiled.diagnostics
    assert legacy_positional.artifact_annotations == {}

    agent_prompt = ""
    agent_context: CompletionContext | None = None

    async def complete(prompt: str, context: CompletionContext) -> object:
        nonlocal agent_context, agent_prompt
        agent_prompt = prompt
        agent_context = context
        return {"result_value": "agent result"}

    assert await execute_workflow(source, inputs={"source_value": "input"}, complete=complete) == {
        "result_value": "agent result"
    }
    assert agent_prompt == (
        "Instruction:\nTransform the value.\n\n"
        'Inputs: {"source_value": "input"}\n'
        "Return the value for output artifact 'result_value'."
    )
    assert agent_context is not None
    assert agent_context.input_annotations == {}
    assert agent_context.output_annotations == {}

    human_prompt = ""
    human_context: CompletionContext | None = None

    async def prepare_human(prompt: str, context: CompletionContext) -> str:
        nonlocal human_context, human_prompt
        human_prompt = prompt
        human_context = context
        return "Provide the transformed value."

    async def request_human(instruction: str, context: CompletionContext) -> object:
        assert instruction == "Provide the transformed value."
        assert context is human_context
        return "human result"

    assert await execute_workflow(
        _single_step_source("Human"),
        inputs={"source_value": "input"},
        prepare_human_instruction=prepare_human,
        request_human=request_human,
    ) == {"result_value": "human result"}
    assert human_prompt == (
        "Prepare this workflow step for a human.\n"
        "Step: work_step\n"
        "Instruction:\nTransform the value.\n\n"
        'Inputs: {"source_value": "input"}\n'
        "Output contract: Return the value for output artifact 'result_value'.\n"
        "Produce concise, readable guidance. Use available tools only when needed to inspect supporting "
        "resources named by the inputs. Do not ask the human directly, change resources, or invent "
        "inaccessible contents."
    )
    assert human_context is not None
    assert human_context.input_annotations == {}
    assert human_context.output_annotations == {}

    program_invocation: ProgramInvocation | None = None

    async def run_program(invocation: ProgramInvocation) -> object:
        nonlocal program_invocation
        program_invocation = invocation
        return "program result\n"

    assert await execute_workflow(
        _single_step_source("Program"),
        inputs={"source_value": "input"},
        work_dir=".",
        run_program=run_program,
    ) == {"result_value": "program result\n"}
    assert program_invocation is not None
    assert program_invocation.input_annotations == {}
    assert program_invocation.output_annotations == {}


@pytest.mark.anyio
async def test_annotations_describe_agent_properties_without_changing_program_output_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(_WORKFLOW_TOOLS))
    sys.modules.pop("run_flow", None)
    run_flow = importlib.import_module("run_flow")
    annotation = "[array]: suggested section objects, but plain text is also accepted."
    captured_schema: dict[str, object] = {}
    completion_calls = 0

    async def fake_create_step_agent(ai_socket, tool_registry, **kwargs):
        del ai_socket, kwargs
        captured_schema.update(tool_registry.tools["submit_step_result"].parameters)
        return object(), SimpleNamespace(messages=[])

    async def fake_complete_step_agent(agent, conversation, message, **kwargs):
        nonlocal completion_calls
        del agent, conversation, message, kwargs
        completion_calls += 1
        return '{"summary_sections":"plain text"}'

    monkeypatch.setattr(run_flow, "_create_step_agent", fake_create_step_agent)
    monkeypatch.setattr(run_flow, "_complete_step_agent", fake_complete_step_agent)
    context = CompletionContext(
        step_id="summarize_step",
        executor_id="writer",
        executor_kind="Agent",
        inputs={"source_document": {}},
        output_ids=("summary_sections",),
        dispatch=DispatchContext(),
        output_annotations={"summary_sections": annotation},
    )

    agent_result = await run_flow._complete_agent_step(
        "Summarize the document.",
        context,
        ai_socket="unix:///unused.sock",
        tool_registry=SimpleNamespace(tools={}, get=lambda name: None),
    )

    assert agent_result == {"summary_sections": "plain text"}
    assert completion_calls == 1
    assert captured_schema["properties"] == {"summary_sections": {"description": annotation}}

    invocation = ProgramInvocation(
        name="program",
        argv=("./summarize.py",),
        stdin="{}\n",
        cwd=".",
        binding_name="summarize_step",
        dispatch=DispatchContext(),
        output_ids=("summary_sections",),
        output_annotations={"summary_sections": annotation},
    )
    attempt = run_flow._ProgramProcessResult(
        argv=("python", "summarize.py"),
        exit_code=0,
        stdout=b"not JSON and still the exact output\n",
        stderr=b"",
    )

    assert run_flow._program_output_mode(invocation.output_ids) == "stdout_verbatim"
    assert run_flow._program_result_outputs(invocation, [attempt]) == {
        "summary_sections": "not JSON and still the exact output\n"
    }
