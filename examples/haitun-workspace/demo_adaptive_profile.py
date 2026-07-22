"""Standalone adaptive-profile demo; does not start or import SessionAgent."""
# ruff: noqa: T201

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import anyio

WORKSPACE = Path(__file__).parent
DEMO_ROOT = Path(os.environ.get("HAITUN_DEMO_WORKSPACE") or tempfile.mkdtemp(prefix="haitun-profile-demo-"))
sys.path.insert(0, str(WORKSPACE / "tools"))
sys.path.insert(0, str(WORKSPACE / "systems"))


def _load_system_module():
    spec = importlib.util.spec_from_file_location("haitun_demo_system", WORKSPACE / "systems" / "system.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load Haitun system module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _teaching_hint(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("- 教学指令:"):
            return line.removeprefix("- 教学指令:").strip()
    return "<missing teaching hint>"


async def main() -> None:
    system = _load_system_module()
    demo_root = DEMO_ROOT
    await anyio.Path(demo_root).mkdir(parents=True, exist_ok=True)
    learners = {
        "alice": "请深入解释 Transformer attention 原理",
        "bob": "Transformer attention 简单说就行",
    }
    for user_id, question in learners.items():
        message = {"role": "user", "content": question, "user_id": user_id}
        before = await system.system_prompt_builder(message, workspace_raw=str(demo_root))
        print("USER", user_id, "BEFORE", _teaching_hint(before))

        await system.system_after_turn(
            message,
            {"role": "assistant", "content": "Raw assistant text is not persisted."},
            workspace_raw=str(demo_root),
        )
        print("USER", user_id, "AFTER UPDATE")

        next_message = {"role": "user", "content": "讲短一点", "user_id": user_id}
        next_prompt = await system.system_prompt_builder(next_message, workspace_raw=str(demo_root))
        print("USER", user_id, "NEXT PROMPT", _teaching_hint(next_prompt))
        digest = hashlib.sha256(user_id.encode()).hexdigest()
        print("PROFILE FILE", demo_root / "wiki" / "profiles" / f"user-{digest}.md")


if __name__ == "__main__":
    anyio.run(main)
