from __future__ import annotations

import importlib
import sys
from pathlib import Path

import anyio
import pytest


def _load_profile_module(monkeypatch: pytest.MonkeyPatch):
    tools_dir = Path(__file__).parents[2] / "examples" / "haitun-workspace" / "tools"
    monkeypatch.syspath_prepend(str(tools_dir))
    sys.modules.pop("_user_profile", None)
    return importlib.import_module("_user_profile")


@pytest.mark.anyio
async def test_topic_dimensions_update_without_raw_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_profile_module(monkeypatch)
    profile = module.UserProfile(anyio.Path(tmp_path))

    overfit_key = profile.update("你好, 我想了解过拟合是什么?", "answer")
    same_key = profile.update("不用太深入, 简单说就行.", "short answer")
    database_key = profile.update("数据库选型需要考虑哪些成本和风险?", "decision answer")

    assert same_key == overfit_key
    assert database_key != overfit_key
    assert profile.topics[overfit_key]["dimensions"]["depth"] < 0.45
    assert profile.topics[overfit_key]["dimensions"]["familiarity"] < 0.4
    assert profile.topics[database_key]["dimensions"]["goal"] > 0.6

    await profile.save()
    saved = await anyio.Path(tmp_path / "wiki" / "_profile.md").read_text(encoding="utf-8")
    assert "history:" not in saved
    assert "short answer" not in saved
    assert "topics:" in saved
    assert "过拟合" in saved
    assert "数据库" in saved


@pytest.mark.anyio
async def test_legacy_history_migrates_to_topic_aggregates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_profile_module(monkeypatch)
    wiki = anyio.Path(tmp_path / "wiki")
    await wiki.mkdir(parents=True)
    await (wiki / "_profile.md").write_text(
        "---\n"
        "dimensions: {depth: 0.5, goal: 0.5, familiarity: 0.5}\n"
        "history:\n"
        "- {role: user, text: '我想了解过拟合是什么'}\n"
        "- {role: agent, text: '旧回复原文'}\n"
        "---\n",
        encoding="utf-8",
    )
    profile = module.UserProfile(anyio.Path(tmp_path))

    await profile.load()
    await profile.save()

    saved = await (wiki / "_profile.md").read_text(encoding="utf-8")
    assert "version: 2" in saved
    assert "history:" not in saved
    assert "旧回复原文" not in saved
    assert "过拟合" in saved
