from __future__ import annotations

import contextlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.anyio
async def test_fusion_memory_workspace_prompt_requires_first_use_consent() -> None:
    module = _load_module(
        ROOT / "examples" / "fusion-memory-workspace" / "systems" / "system.py",
        "fusion_memory_workspace_system",
    )
    prompt = await module.system_prompt_builder()

    assert "ask the user whether to enable Fusion Memory persistent memory" in prompt
    assert "cannot remember across sessions" in prompt
    assert "If the user declines" in prompt


def test_haitun_fusion_memory_section_requires_first_use_consent() -> None:
    module = _load_module(
        ROOT / "examples" / "haitun-workspace" / "systems" / "prompt_sections.py",
        "haitun_prompt_sections",
    )
    section = module.FUSION_MEMORY_SECTION

    assert "ask the user whether to enable Fusion Memory persistent memory" in section
    assert "cannot remember across sessions" in section
    assert "If the user declines" in section


def _load_module(path: Path, name: str) -> object:
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(str(path.parent))
