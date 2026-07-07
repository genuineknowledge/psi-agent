from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL_PATHS = (
    ROOT / "examples" / "fusion-memory-workspace" / "skills" / "fusion-memory-setup" / "SKILL.md",
    ROOT / "examples" / "haitun-workspace" / "skills" / "fusion-memory-setup" / "SKILL.md",
)


def test_fusion_memory_setup_skills_use_windows_venv_installer() -> None:
    for path in SKILL_PATHS:
        skill = path.read_text(encoding="utf-8")

        assert ".fusion-memory-venv" not in skill
        assert "ModelScope" in skill
        assert "Do not ask the user to manually install Python" in skill
        assert "Git LFS" in skill
        assert "git lfs pull" not in skill.lower()
        assert "uv.exe" in skill
        assert "uv-managed Python 3.12" in skill
        assert "compatible Windows CPython" in skill
        assert "wheel-only" not in skill
        assert "Do not paste" in skill
        assert "full uv, dependency, or model download logs" in skill
        assert "local_test" in skill
        assert "do not use pwsh, powershell.exe" in skill
        assert "the Fusion Memory CLI creates the hidden/no-window service" in skill
        assert "PowerShell job/process wrappers" not in skill
        windows_repair = skill.split("On Windows PowerShell:", 1)[1].split(
            "If the repair attempt still reports not_ready", 1
        )[0]
        assert ".\\install.ps1" in windows_repair
        assert "fusion-memory[postgres,qwen]" not in windows_repair


def test_fusion_memory_prompts_do_not_wrap_service_start_in_powershell() -> None:
    prompt_files = (
        ROOT / "examples" / "haitun-workspace" / "systems" / "prompt_sections.py",
        ROOT / "examples" / "fusion-memory-workspace" / "systems" / "system.py",
    )
    for path in prompt_files:
        prompt_source = path.read_text(encoding="utf-8")

        assert "fusion-memory start --json" in prompt_source
        assert "sync-haitun-history --background --json" in prompt_source
        assert "hidden/no-window service and watcher" in prompt_source
        assert "do not use pwsh, powershell.exe" in prompt_source
