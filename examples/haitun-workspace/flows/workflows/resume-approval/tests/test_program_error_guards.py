from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
PROGRAM_PATH = WORKFLOW_ROOT / "programs" / "assert_no_program_errors.py"
WORKFLOW_PATH = WORKFLOW_ROOT / "resume-approval.workflow"


def _run_guard(inputs: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROGRAM_PATH)],
        input=json.dumps({"inputs": inputs}, ensure_ascii=False),
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
    )


def test_program_error_guard_accepts_ordinary_artifacts_without_stdout() -> None:
    """A guard must not alter or reject valid upstream JSON values."""
    completed = _run_guard(
        {
            "batch_id": "resume-20260809-1",
            "candidate_assessments_round_1": [],
            "assessment_repair_merge_manifest_round_1": {"status": "complete"},
        }
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""


def test_program_error_guard_rejects_the_first_error_artifact_with_context() -> None:
    """A non-foreach Program failure must stop at its immediate graph boundary."""
    completed = _run_guard(
        {
            "candidate_assessments_round_1": {
                "$fusion_flow/program_error": {
                    "phase": "execution",
                    "kind": "nonzero_exit",
                    "message": "Program exited with code 1.",
                    "attempts": [],
                }
            }
        }
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "candidate_assessments_round_1" in completed.stderr
    assert "execution/nonzero_exit" in completed.stderr
    assert "Program exited with code 1." in completed.stderr


def test_workflow_guards_every_non_foreach_program_error_boundary() -> None:
    """Removing a guard dependency must not let an error-valued Artifact reach another Step."""
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    dependencies = {
        "fetch_reference_documents_step": "assert_defaults_program_ready_step",
        "stage_resume_files_step": "assert_defaults_program_ready_step",
        "extract_resume_step": "assert_resume_staging_program_ready_step",
        "repair_assessments_round_1_step": "assert_repair_builder_round_1_program_ready_step",
        "build_assessment_repairs_round_2_step": "assert_repair_merge_round_1_program_ready_step",
        "repair_assessments_round_2_step": "assert_repair_builder_round_2_program_ready_step",
        "validate_assessments_step": "assert_repair_merge_round_2_program_ready_step",
        "cleanup_temporary_files_step": "assert_initial_review_handoff_ready_step",
    }
    for step, guard in dependencies.items():
        assert f"depends_on({step}, {guard}) == True;" in workflow

    for guard in sorted(set(dependencies.values()) | {"assert_initial_review_handoff_ready_step"}):
        assert f"step_executor({guard}) == program_error_assertion;" in workflow
        assert f"produces({guard})" not in workflow

    assert (
        "depends_on(assert_initial_review_handoff_ready_step, persist_initial_review_handoff_step) == True;" in workflow
    )
