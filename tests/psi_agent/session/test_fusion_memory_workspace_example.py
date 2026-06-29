from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from psi_agent.session.system_prompt import SystemPrompt
from psi_agent.session.tool_registry import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = REPO_ROOT / "examples" / "fusion-memory-workspace"
TOOLS_DIR = WORKSPACE / "tools"
SKILL_FILE = WORKSPACE / "skills" / "fusion-memory-setup" / "SKILL.md"


def _load_tool_module(name: str):
    return _load_tool_module_from(TOOLS_DIR, name)


def _load_tool_module_from(tools_dir: Path, name: str):
    module_path = tools_dir / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"fusion_memory_example_{name}", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


@pytest.mark.anyio
async def test_fusion_memory_workspace_loads_only_memory_tools() -> None:
    registry = await ToolRegistry.load(TOOLS_DIR, "fusion-memory-example")

    assert set(registry.tools) == {"memory_add", "memory_answer_context", "memory_search"}
    assert registry.tools["memory_add"].parameters["required"] == ["content"]
    assert registry.tools["memory_search"].parameters["required"] == ["query"]
    assert registry.tools["memory_answer_context"].parameters["required"] == ["query"]


@pytest.mark.anyio
async def test_fusion_memory_workspace_system_prompt_mentions_memory_tools() -> None:
    prompt = await SystemPrompt.from_workspace(WORKSPACE, "fusion-memory-example")
    content = await prompt._builder()

    assert "memory_add" in content
    assert "memory_search" in content
    assert "memory_answer_context" in content
    assert "durable Fusion Memory" in content
    assert "fusion-memory-setup" in content
    assert "first use of Fusion Memory" in content


def test_fusion_memory_workspace_config_defaults_are_beginner_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PSI_MEMORY_BASE_URL", raising=False)
    monkeypatch.delenv("PSI_MEMORY_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("PSI_MEMORY_USER_ID", raising=False)
    monkeypatch.delenv("PSI_MEMORY_AGENT_ID", raising=False)
    monkeypatch.delenv("PSI_MEMORY_SESSION_ID", raising=False)
    monkeypatch.setenv("USER", "beginner")

    config_module = _load_tool_module("_config")
    cfg = config_module.build_memory_config()

    assert cfg.base_url == "http://127.0.0.1:8700"
    assert cfg.workspace_id == "dolphin"
    assert cfg.user_id == "beginner"
    assert cfg.agent_id == "dolphin"
    assert cfg.session_id is None
    assert cfg.allow_cross_session is True


@pytest.mark.anyio
async def test_fusion_memory_workspace_tools_return_fallback_when_memory_is_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PSI_MEMORY_BASE_URL", "http://127.0.0.1:1")
    module = _load_tool_module("memory_search")

    result = await module.memory_search("anything")

    assert "Fusion Memory is not available" in result


@pytest.mark.anyio
async def test_fusion_memory_workspace_can_be_copied_to_new_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    copied_workspace = tmp_path / "copied-memory-workspace"
    shutil.copytree(WORKSPACE, copied_workspace)

    registry = await ToolRegistry.load(copied_workspace / "tools", "copied-memory-workspace")
    prompt = await SystemPrompt.from_workspace(copied_workspace, "copied-memory-workspace")
    monkeypatch.setenv("PSI_MEMORY_BASE_URL", "http://127.0.0.1:1")
    memory_search = _load_tool_module_from(copied_workspace / "tools", "memory_search")

    assert set(registry.tools) == {"memory_add", "memory_answer_context", "memory_search"}
    assert "durable Fusion Memory" in await prompt._builder()
    assert (copied_workspace / "skills" / "fusion-memory-setup" / "SKILL.md").exists()
    assert "Fusion Memory is not available" in await memory_search.memory_search("anything")


def test_fusion_memory_workspace_readme_documents_copy_paste_usage() -> None:
    readme = (WORKSPACE / "README.md").read_text()

    assert "Copy Into Another Workspace" in readme
    assert "cp -R examples/fusion-memory-workspace" in readme
    assert "cp examples/fusion-memory-workspace/tools/memory_*.py" in readme
    assert "cp examples/fusion-memory-workspace/tools/_client.py" in readme
    assert "cp examples/fusion-memory-workspace/tools/_config.py" in readme
    assert "skills/fusion-memory-setup" in readme


def test_fusion_memory_setup_skill_documents_first_use_initialization() -> None:
    skill = SKILL_FILE.read_text()

    assert "name: fusion-memory-setup" in skill
    assert "first use of Fusion Memory" in skill
    assert "git clone https://github.com/genuineknowledge/fusion-memory.git" in skill
    assert "git@" not in skill
    assert "wey-bo" not in skill
    assert "identity" not in skill.lower()
    assert "authentication" not in skill.lower()
    assert "sh install.sh" in skill
    assert "fusion-memory init --local-test --json" in skill
    assert "fusion-memory start --json" in skill
    assert "fusion-memory doctor --json" in skill
    assert "PSI_MEMORY_BASE_URL=http://127.0.0.1:8700" in skill
    assert "compromised" in skill
    assert "DASHSCOPE_API_KEY" in skill


def test_fusion_memory_workspace_readme_uses_public_repository() -> None:
    readme = (WORKSPACE / "README.md").read_text()

    assert "git clone https://github.com/genuineknowledge/fusion-memory.git" in readme
    assert "wey-bo" not in readme
    assert "git@" not in readme


@pytest.mark.anyio
async def test_fusion_memory_workspace_memory_add_marks_explicit_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict] = []
    module = _load_tool_module("memory_add")

    async def fake_post_json(base_url: str, path: str, payload: dict, timeout_seconds: float) -> dict:
        seen.append(payload)
        return {"ok": True, "saved": True}

    monkeypatch.setattr(module, "_post_json", fake_post_json)

    result = await module.memory_add("remember this")

    assert "saved" in result
    assert seen[0]["metadata"]["write_mode"] == "explicit_tool"
    assert seen[0]["metadata"]["auto_persisted"] is False
