"""A Step's own tool registry must not depend on the exposure gate's fallback.

The Program Step hands its Agent a registry assembled in code (``bash``,
``find_files``, ``list_dir``, ``powershell``, ``read`` plus ``execute_program`` /
``compile_program`` / ``submit_program_result``).  The session gate narrows the
model-facing ``tools`` array by content layer, and a layer-less registry used to
survive only through ``select_exposed``'s "nothing matched, pass the registry
through" branch -- which stops applying the moment any tool in that registry is
exposed by another route.  ``tool_search`` is exactly such a tool, so the probe
below puts one in the registry: without the declaration the criterion goes red
instead of passing on the fallback.

``_StepToolRegistry`` now declares what it holds.  The probe runs the real class in
a fresh interpreter because importing ``run_flow`` in-process would reuse the
already-loaded ``fusion_flow`` and pass for the wrong reason.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from psi_agent.session.tool_exposure import DISCOVERY_TOOLS, ExposureTier, select_exposed

_REPO = Path(__file__).resolve().parents[3]
_TOOLS = _REPO / "agents" / "feishu" / "tools"

# What ``_PROGRAM_AGENT_TOOLS`` picks out of the step registry, plus what
# ``_complete_program_step`` adds on top of it.
_PROGRAM_STEP_TOOLS = (
    "bash",
    "find_files",
    "list_dir",
    "powershell",
    "read",
    "execute_program",
    "compile_program",
    "submit_program_result",
)

# A registry holding a tool the gate keeps by another route can no longer rely on
# the empty-result fallback, so this name is what makes the criterion able to fail.
_ESCAPE_HATCH = sorted(DISCOVERY_TOOLS)[0]
_STEP_REGISTRY_TOOLS = (*_PROGRAM_STEP_TOOLS, _ESCAPE_HATCH)


def test_step_registry_declares_its_tools_to_the_exposure_gate() -> None:
    """Run the real ``_StepToolRegistry`` through ``select_exposed``, all three gears."""

    probe = textwrap.dedent("""
        import json, sys
        sys.path.insert(0, r"%s")
        import run_flow
        from psi_agent.session.tool_exposure import ExposureTier, select_exposed
        from psi_agent.session.tool_registry import FileEntry

        names = %r
        entry = FileEntry(file_hash="", tools=dict.fromkeys(names), funcs={})
        registry = run_flow._StepToolRegistry(files={"__step__": entry})

        declared = {
            tier.value: sorted(
                select_exposed(
                    registry.tools,
                    tier=tier,
                    layer_of=registry.layer_of_tool,
                    manifests=registry.exposure_manifests,
                )
            )
            for tier in (ExposureTier.OFF, ExposureTier.LAYERED, ExposureTier.DECLARED)
        }

        # Negative control: the pre-fix shape.  No declaration, same tool set, so
        # the gate keeps only what it exposes by another route.
        undeclared = {
            tier.value: sorted(
                select_exposed(
                    registry.tools,
                    tier=tier,
                    layer_of=dict.fromkeys(names, ""),
                    manifests={},
                )
            )
            for tier in (ExposureTier.OFF, ExposureTier.LAYERED, ExposureTier.DECLARED)
        }
        print(json.dumps({"declared": declared, "undeclared": undeclared}))
    """) % (str(_TOOLS), list(_STEP_REGISTRY_TOOLS))

    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(_REPO / "src")},
        cwd=str(_REPO),
        timeout=180,
    )
    assert result.returncode == 0, f"探针进程失败:\n{result.stderr}"

    payload = json.loads(result.stdout.splitlines()[-1])
    for tier, names in payload["declared"].items():
        assert set(names) == set(_STEP_REGISTRY_TOOLS), f"{tier} 档丢了 Program 步的工具: {names}"

    # The control must fail, otherwise the criterion above would stay green with the
    # declaration removed.  Only the narrowing gears drop anything.
    assert set(payload["undeclared"]["declared"]) == {_ESCAPE_HATCH}
    assert payload["undeclared"]["layered"] == sorted(_STEP_REGISTRY_TOOLS)
    assert payload["undeclared"]["off"] == sorted(_STEP_REGISTRY_TOOLS)


def test_declared_registry_survives_the_narrowest_gear() -> None:
    """The contract the Step registry relies on, stated without importing run_flow."""

    tools = dict.fromkeys([*_PROGRAM_STEP_TOOLS, _ESCAPE_HATCH])
    layer_of = dict.fromkeys(tools, "")

    kept = select_exposed(
        tools,
        tier=ExposureTier.DECLARED,
        layer_of=layer_of,
        manifests={"": frozenset(tools)},
    )

    assert set(kept) == set(tools)
