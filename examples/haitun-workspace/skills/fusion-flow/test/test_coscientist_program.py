from __future__ import annotations

import io
import json
import runpy
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from fusion_flow.workflow_runner import compile_workflow

_WORKSPACE_DIR = Path(__file__).resolve().parents[3]
_WORKFLOW_DIR = _WORKSPACE_DIR / "flows" / "workflows" / "coscientist-ows"
_PROGRAM_PATH = _WORKSPACE_DIR / "skills" / "coscientist-ows-entry" / "scripts" / "program.py"
_DISTRIBUTED_SKILLS = (
    "coscientist-ows-entry",
    "mattergen-structure-sampler",
    "mattersim-structure-evaluator",
    "ows-catalyst-recommender",
    "round-parallel-synthesis-advisor",
    "source-liquid-sop-designer",
    "stage08-catalytic-performance-prover",
    "stage08-synthesis-safety-feasibility-judge",
)


@pytest.mark.parametrize(
    ("slug", "step_count"),
    [("single-step", 1), ("sequential", 2), ("parallel-join", 3)],
)
def test_short_workflow_compiles_with_current_executor_contract(
    slug: str,
    step_count: int,
) -> None:
    source = (_WORKSPACE_DIR / "flows" / "workflows" / slug / f"{slug}.workflow").read_text(encoding="utf-8")

    compiled = compile_workflow(source, strict_executors=True)

    assert len(compiled.graph.steps) == step_count
    assert set(compiled.executor_kinds.values()) == {"Agent"}


def _load_program() -> dict[str, Any]:
    assert _PROGRAM_PATH.is_file(), "coscientist Program executable is missing"
    return runpy.run_path(str(_PROGRAM_PATH))


def test_workflow_compiles_with_local_instruction_and_program_assets() -> None:
    source = (_WORKFLOW_DIR / "coscientist-ows.workflow").read_text(encoding="utf-8")

    compiled = compile_workflow(source, strict_executors=True)

    assert len(compiled.graph.steps) == 12
    assert set(compiled.executor_kinds.values()) == {"Agent", "Program"}
    assert not any(step.resources for step in compiled.graph.steps)
    for reference in (step.instruction_id for step in compiled.graph.steps):
        assert reference is not None
        assert reference.startswith("./")
        assert (_WORKFLOW_DIR / reference.removeprefix("./")).is_file()
    for reference in compiled.program_paths.values():
        assert reference.startswith("./")
        assert (_WORKSPACE_DIR / reference.removeprefix("./")).is_file()


def test_saved_workflow_has_public_distribution_assets() -> None:
    inputs_path = _WORKFLOW_DIR / "coscientist-ows.inputs.example.json"
    inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
    source = (_WORKFLOW_DIR / "coscientist-ows.workflow").read_text(encoding="utf-8")
    scheduler_source = (
        _WORKSPACE_DIR / "skills" / "coscientist-ows-entry" / "scripts" / "run_ows_streaming_scheduler.py"
    ).read_text(encoding="utf-8")
    compiled = compile_workflow(source, strict_executors=True)

    assert (_WORKFLOW_DIR / "README.md").is_file()
    assert "/public/home/" not in scheduler_source
    assert set(inputs) == {artifact.artifact_id for artifact in compiled.graph.artifacts if artifact.is_input}
    for skill_name in _DISTRIBUTED_SKILLS:
        assert (_WORKSPACE_DIR / "skills" / skill_name / "SKILL.md").is_file()


def test_program_main_dispatches_from_materialized_instruction_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _load_program()
    main = namespace["main"]
    calls: list[str] = []

    def prepare(inputs: dict[str, object], workspace: Path) -> dict[str, object]:
        del inputs, workspace
        calls.append("prepare")
        return {"handler": "prepare"}

    def merge(inputs: dict[str, object], workspace: Path) -> dict[str, object]:
        del inputs, workspace
        calls.append("merge")
        return {"handler": "merge"}

    main.__globals__["prepare"] = prepare
    main.__globals__["merge"] = merge
    cases = (
        ({"result_directory_name": "results"}, "prepare"),
        ({"tmp_candidates_directory_initial": "results/tmp/candidates"}, "merge"),
    )
    for inputs, expected in cases:
        stdout = io.StringIO()
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "instruction": "Materialized instruction text.",
                        "inputs": inputs,
                    }
                )
            ),
        )
        monkeypatch.setattr(sys, "stdout", stdout)

        assert main() == 0
        assert json.loads(stdout.getvalue()) == {"handler": expected}

    assert calls == ["prepare", "merge"]


def test_prepare_program_initializes_all_declared_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections: list[sqlite3.Connection] = []
    original_connect = sqlite3.connect

    class TrackingConnection(sqlite3.Connection):
        closed = False

        def close(self) -> None:
            self.closed = True
            super().close()

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs["factory"] = TrackingConnection
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    scheduler_source = _WORKSPACE_DIR / "skills" / "coscientist-ows-entry"
    shutil.copytree(
        scheduler_source,
        tmp_path / "skills" / "coscientist-ows-entry",
    )
    prepare = _load_program()["prepare"]

    outputs = prepare(
        {
            "candidate_knowledge_base_initial": {"candidates": []},
            "result_directory_name": "results",
        },
        tmp_path,
    )

    assert set(outputs) == {
        "candidate_catalyst_pool_initial",
        "candidate_catalyst_structure_pool_initial",
        "candidate_knowledge_base",
        "fail_candidates_directory_initial",
        "fail_directory",
        "mattergen_stage_directory_initial",
        "mattersim_stage_directory_initial",
        "novel_and_stable_catalysts_initial",
        "prepare_workflow_step_result",
        "recommender_slot_1_directory",
        "recommender_slot_2_directory",
        "recommender_slot_3_directory",
        "recommender_slot_4_directory",
        "round_parallel_synthesis_stage_directory_initial",
        "scheduler_state",
        "tmp_candidates_directory_initial",
        "tmp_knowledge_directory_initial",
        "workflow_run_context",
    }
    assert outputs["candidate_knowledge_base"] == {"candidates": []}
    assert (tmp_path / outputs["scheduler_state"]).is_file()
    assert (tmp_path / outputs["prepare_workflow_step_result"]).is_file()
    assert connections
    assert all(cast(TrackingConnection, connection).closed for connection in connections)


def test_merge_program_preserves_each_slot_directory(tmp_path: Path) -> None:
    merge = _load_program()["merge"]
    candidate_destination = tmp_path / "results" / "tmp" / "candidates"
    knowledge_destination = tmp_path / "results" / "tmp" / "knowledge"
    candidate_destination.mkdir(parents=True)
    knowledge_destination.mkdir(parents=True)
    inputs: dict[str, object] = {
        "tmp_candidates_directory_initial": str(candidate_destination),
        "tmp_knowledge_directory_initial": str(knowledge_destination),
    }
    for slot in range(1, 5):
        candidate_source = tmp_path / "candidate-deltas" / f"slot_{slot}"
        knowledge_source = tmp_path / "knowledge-deltas" / f"slot_{slot}"
        candidate_source.mkdir(parents=True)
        knowledge_source.mkdir(parents=True)
        (candidate_source / "candidate.json").write_text("{}", encoding="utf-8")
        (knowledge_source / "manifest.json").write_text(
            '{"captured": false}',
            encoding="utf-8",
        )
        inputs[f"tmp_candidates_directory_from_recommend_{slot}"] = str(candidate_source)
        inputs[f"tmp_knowledge_directory_from_recommend_{slot}"] = str(knowledge_source)

    outputs = merge(inputs, tmp_path)

    assert outputs == {
        "tmp_candidates_directory_after_recommendations": "results/tmp/candidates",
        "tmp_knowledge_directory": "results/tmp/knowledge",
    }
    for slot in range(1, 5):
        assert (candidate_destination / f"slot_{slot}" / "candidate.json").is_file()
        assert (knowledge_destination / f"slot_{slot}" / "manifest.json").is_file()


def test_merge_program_rejects_conflicting_slot_directory(tmp_path: Path) -> None:
    merge = _load_program()["merge"]
    candidate_destination = tmp_path / "results" / "tmp" / "candidates"
    knowledge_destination = tmp_path / "results" / "tmp" / "knowledge"
    (candidate_destination / "slot_1").mkdir(parents=True)
    knowledge_destination.mkdir(parents=True)
    candidate_source = tmp_path / "candidate-deltas" / "slot_1"
    knowledge_source = tmp_path / "knowledge-deltas" / "slot_1"
    candidate_source.mkdir(parents=True)
    knowledge_source.mkdir(parents=True)
    inputs: dict[str, object] = {
        "tmp_candidates_directory_initial": str(candidate_destination),
        "tmp_knowledge_directory_initial": str(knowledge_destination),
    }
    for slot in range(1, 5):
        inputs[f"tmp_candidates_directory_from_recommend_{slot}"] = str(candidate_source)
        inputs[f"tmp_knowledge_directory_from_recommend_{slot}"] = str(knowledge_source)

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        merge(inputs, tmp_path)
