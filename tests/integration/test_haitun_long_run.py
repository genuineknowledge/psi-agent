from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load(name: str):
    root = Path(__file__).parents[2] / "agents" / "feishu"
    spec = importlib.util.spec_from_file_location(name, root / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scenario_suite_has_250_multidomain_fault_aware_turns() -> None:
    scenarios = _load("long_run_scenarios")

    turns = scenarios.build_turns()

    assert len(turns) == 250
    assert len({turn.domain for turn in turns}) >= 8
    assert {turn.depth for turn in turns} >= {"simple", "balanced", "deep"}
    assert sum(turn.fault != "none" for turn in turns) >= 20
    assert sum(turn.duplicate for turn in turns) >= 10
    assert all(turn.event_id == f"deterministic-{turn.index:03d}" for turn in turns)


def test_registration_is_a_weak_cross_domain_prior() -> None:
    scenarios = _load("long_run_scenarios")

    registration = scenarios.registration_profile()

    assert "corporate-law" in registration["strong_domains"]
    assert "machine-learning" in registration["new_domains"]
    assert registration["priority"] == "conversation_over_registration"


@pytest.mark.anyio
async def test_25_turn_preflight_persists_profile_maps_and_heatmaps(tmp_path: Path) -> None:
    runner = _load("run_supervisor_stability")

    summary = await runner.run_stability(str(tmp_path), limit=25)

    assert summary["processed_turns"] == 25
    assert summary["profile_turns"] == 25
    assert summary["domains"] == 1
    assert summary["heatmap_events"] == 25
    assert summary["parse_failures"] == 0
    assert summary["invariant_failures"] == []


def test_real_evaluation_selects_three_depths_per_domain() -> None:
    runner = _load("run_real_multidomain_evaluation")

    turns = runner.selected_turns()

    assert len(turns) == 30
    assert len({turn.domain for turn in turns}) == 10
    for domain in {turn.domain for turn in turns}:
        assert {turn.depth for turn in turns if turn.domain == domain} == {"simple", "balanced", "deep"}
