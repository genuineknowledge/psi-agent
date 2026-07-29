#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast


def _workspace_path(workspace: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty path string")
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else workspace / candidate).resolve()
    workspace = workspace.resolve()
    if not resolved.is_relative_to(workspace) or resolved == workspace:
        raise ValueError(f"{label} must stay below the workspace root")
    return resolved


def _relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def prepare(inputs: dict[str, object], workspace: Path) -> dict[str, object]:
    output_root = _workspace_path(
        workspace,
        inputs.get("result_directory_name"),
        label="result_directory_name",
    )
    scheduler_path = workspace / "skills" / "coscientist-ows-entry" / "scripts" / "run_ows_streaming_scheduler.py"
    namespace: dict[str, object] = {
        "__file__": str(scheduler_path),
        "__name__": "coscientist_ows_scheduler",
    }
    exec(
        compile(scheduler_path.read_text(encoding="utf-8"), str(scheduler_path), "exec"),
        namespace,
    )
    command_init = namespace.get("command_init")
    if not callable(command_init):
        raise RuntimeError(f"scheduler has no command_init: {scheduler_path}")
    command_init = cast(Callable[[SimpleNamespace], dict[str, object]], command_init)
    entry_dir = output_root / "00-coscientist-ows-entry"
    scheduler_result = command_init(
        SimpleNamespace(
            repo_root=str(workspace),
            output_root=str(output_root),
            knowledge_base_path="data/knowledge-base/knowledge_base_for_agent.json",
            recommendation_branch="single-photocatalyst",
            execution_scope="full",
            gpu_id="",
            target_recommendation_count=None,
            recommendation_parallelism=4,
            mattersim_batch_size=8,
            resume=(entry_dir / "PARAMETERS.json").exists(),
        )
    )

    paths = {
        "mattergen_stage_directory_initial": output_root / "04-mattergen-structure-sampler",
        "mattersim_stage_directory_initial": output_root / "05-mattersim-structure-evaluator",
        "round_parallel_synthesis_stage_directory_initial": (output_root / "08-round-parallel-synthesis-advisor"),
        "candidate_catalyst_pool_initial": output_root / "pools" / "candidates",
        "candidate_catalyst_structure_pool_initial": output_root / "pools" / "structures",
        "novel_and_stable_catalysts_initial": (output_root / "pools" / "novel_and_stable_catalysts"),
        "fail_directory": output_root / "fail",
        "fail_candidates_directory_initial": output_root / "fail" / "candidates",
        "tmp_candidates_directory_initial": output_root / "tmp" / "candidates",
        "tmp_knowledge_directory_initial": output_root / "tmp" / "knowledge",
        **{
            f"recommender_slot_{slot}_directory": (output_root / "02-ows-catalyst-recommender" / f"slot_{slot}")
            for slot in range(1, 5)
        },
    }
    for path in (
        *paths.values(),
        output_root / "05-mattersim-structure-evaluator" / "streaming" / "batches",
        output_root / "08-round-parallel-synthesis-advisor" / "rounds",
        output_root / "08-round-parallel-synthesis-advisor" / "synthesis-routes",
        output_root / "pools" / "knowledge",
    ):
        path.mkdir(parents=True, exist_ok=True)

    prepare_result = entry_dir / "PREPARE_WORKFLOW_STEP_RESULT.json"
    outputs: dict[str, object] = {
        "workflow_run_context": {
            "output_root": _relative(workspace, output_root),
            "scheduler": scheduler_result,
        },
        "scheduler_state": _relative(
            workspace,
            entry_dir / "STREAMING_SCHEDULER_STATE.json",
        ),
        **{key: _relative(workspace, path) for key, path in paths.items()},
        "candidate_knowledge_base": inputs.get("candidate_knowledge_base_initial"),
        "prepare_workflow_step_result": _relative(workspace, prepare_result),
    }
    prepare_result.write_text(
        json.dumps(outputs, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return outputs


def _merge_directories(destination: Path, sources: list[Path]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for source in sources:
        if not source.is_dir():
            raise FileNotFoundError(f"recommendation delta directory does not exist: {source}")
        if source.is_relative_to(destination):
            continue
        target = destination / source.name
        if target.exists():
            raise FileExistsError(f"refusing to overwrite recommendation delta: {target}")
        shutil.copytree(source, target)


def merge(inputs: dict[str, object], workspace: Path) -> dict[str, object]:
    candidate_destination = _workspace_path(
        workspace,
        inputs.get("tmp_candidates_directory_initial"),
        label="tmp_candidates_directory_initial",
    )
    knowledge_destination = _workspace_path(
        workspace,
        inputs.get("tmp_knowledge_directory_initial"),
        label="tmp_knowledge_directory_initial",
    )
    candidate_sources = [
        _workspace_path(
            workspace,
            inputs.get(f"tmp_candidates_directory_from_recommend_{slot}"),
            label=f"tmp_candidates_directory_from_recommend_{slot}",
        )
        for slot in range(1, 5)
    ]
    knowledge_sources = [
        _workspace_path(
            workspace,
            inputs.get(f"tmp_knowledge_directory_from_recommend_{slot}"),
            label=f"tmp_knowledge_directory_from_recommend_{slot}",
        )
        for slot in range(1, 5)
    ]
    _merge_directories(candidate_destination, candidate_sources)
    _merge_directories(knowledge_destination, knowledge_sources)
    return {
        "tmp_candidates_directory_after_recommendations": _relative(
            workspace,
            candidate_destination,
        ),
        "tmp_knowledge_directory": _relative(workspace, knowledge_destination),
    }


def main() -> int:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise ValueError("Program stdin must be a JSON object")
    inputs = payload.get("inputs")
    instruction = payload.get("instruction")
    if not isinstance(inputs, dict) or not all(isinstance(key, str) for key in inputs):
        raise ValueError("Program inputs must be a JSON object with string keys")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("Program instruction must be non-empty text")
    workspace = Path(__file__).resolve().parents[3]
    is_prepare = "result_directory_name" in inputs
    is_merge = "tmp_candidates_directory_initial" in inputs
    if is_prepare == is_merge:
        raise ValueError("Program inputs do not identify exactly one supported operation")
    handler = prepare if is_prepare else merge
    outputs = handler(cast(dict[str, object], inputs), workspace)
    json.dump(outputs, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
