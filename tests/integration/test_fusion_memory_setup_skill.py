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

        assert ".fusion-memory-venv" in skill
        assert "ModelScope" in skill
        assert "Do not ask the user to manually install Python" in skill
        assert "Git LFS" in skill
        assert "git lfs pull" not in skill.lower()
        assert "uv.exe" in skill
        assert "wheel-only" in skill
        assert "Do not paste full pip logs" in skill
        assert "local_test" in skill
        windows_repair = skill.split("On Windows PowerShell:", 1)[1].split(
            "If the repair attempt still reports not_ready", 1
        )[0]
        assert ".\\install.ps1" in windows_repair
        assert "fusion-memory[postgres,qwen]" not in windows_repair
