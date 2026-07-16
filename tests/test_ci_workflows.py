from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_WORKFLOWS = (
    "ci.yml",
    "nuitka.yml",
    "pyinstaller.yml",
    "auto-alpha-tag.yml",
)


def _load_workflow(filename: str) -> dict[str, Any]:
    path = Path(".github/workflows") / filename
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize("filename", _WORKFLOWS)
def test_workflow_pins_reproducible_python314_build_environment(filename: str) -> None:
    workflow = _load_workflow(filename)
    assert workflow["env"]["PYO3_USE_ABI3_FORWARD_COMPATIBILITY"] == "1"

    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    setup_count = 0
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict) or step.get("uses") != "astral-sh/setup-uv@v7":
                continue
            setup_count += 1
            config = step.get("with")
            assert isinstance(config, dict)
            assert config.get("version") == "0.11.23"
            assert config.get("python-version") == "3.14"
            assert config.get("enable-cache") is True
            cache_glob = config.get("cache-dependency-glob")
            assert isinstance(cache_glob, str)
            assert "uv.lock" in cache_glob
            assert "pyproject.toml" in cache_glob
    assert setup_count > 0


@pytest.mark.parametrize("filename", _WORKFLOWS)
def test_every_project_sync_is_frozen_and_has_rust_first(filename: str) -> None:
    workflow = _load_workflow(filename)
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        rust_seen = False
        for step in steps:
            if not isinstance(step, dict):
                continue
            rust_seen = rust_seen or step.get("uses") == "dtolnay/rust-toolchain@stable"
            command = step.get("run")
            if isinstance(command, str) and command.strip().startswith("uv sync"):
                assert command.strip() == "uv sync --frozen"
                assert rust_seen


def test_ci_smoke_checks_llmrouter_api_and_packaged_prompts() -> None:
    workflow = _load_workflow("ci.yml")
    steps = workflow["jobs"]["lint"]["steps"]
    smoke = next(step for step in steps if step.get("name") == "Verify LLMRouter environment")
    command = smoke["run"]
    for expected in (
        "litellm",
        "LLMMultiRoundRouter",
        "_decompose_and_route",
        "agent_decomp_route.yaml",
        "agent_decomp_cot.yaml",
        "agent_prompt.yaml",
    ):
        assert expected in command
    assert "psi_agent.router" in command


def test_nuitka_workflow_includes_router_custom_tasks_data() -> None:
    workflow = _load_workflow("nuitka.yml")
    common_flags = workflow["jobs"]["nuitka"]["env"]["NUITKA_COMMON_FLAGS"]
    assert isinstance(common_flags, str)
    assert (
        "--include-data-dir=src/psi_agent/router/custom_tasks=psi_agent/router/custom_tasks"
        in common_flags
    )
