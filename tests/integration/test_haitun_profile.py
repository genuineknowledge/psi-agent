from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest


def _load_profile_module(monkeypatch: pytest.MonkeyPatch):
    tools_dir = Path(__file__).parents[2] / "examples" / "haitun-workspace" / "tools"
    monkeypatch.syspath_prepend(str(tools_dir))
    sys.modules.pop("_user_profile", None)
    return importlib.import_module("_user_profile")


def _load_system_module(monkeypatch: pytest.MonkeyPatch):
    workspace = Path(__file__).parents[2] / "examples" / "haitun-workspace"
    monkeypatch.syspath_prepend(str(workspace / "systems"))
    monkeypatch.syspath_prepend(str(workspace / "tools"))
    spec = importlib.util.spec_from_file_location("haitun_system_test", workspace / "systems" / "system.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.anyio
async def test_profiles_are_isolated_by_explicit_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_profile_module(monkeypatch)

    alice = await module.get_profile(str(tmp_path), profile_id="alice")
    bob = await module.get_profile(str(tmp_path), user_id="bob")
    alice.update("请深入解释 Python 原理", "answer")
    bob.update("Python 简单说就行", "answer")
    await alice.save()
    await bob.save()

    assert alice is not bob
    assert alice.profile_id.startswith("profile-")
    assert bob.profile_id.startswith("user-")
    assert await anyio.Path(tmp_path / "wiki" / "profiles" / f"{alice.profile_id}.md").exists()
    assert await anyio.Path(tmp_path / "wiki" / "profiles" / f"{bob.profile_id}.md").exists()
    alice_saved = await anyio.Path(tmp_path / "wiki" / "profiles" / f"{alice.profile_id}.md").read_text(
        encoding="utf-8"
    )
    assert "answer" not in alice_saved


@pytest.mark.anyio
async def test_identity_precedence_namespaces_and_default_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_profile_module(monkeypatch)

    explicit = await module.get_profile(str(tmp_path), profile_id="same", user_id="same", session_id="same")
    user = await module.get_profile(str(tmp_path), user_id="same", session_id="same")
    session = await module.get_profile(str(tmp_path), session_id="same")
    fallback = await module.get_profile(str(tmp_path))

    digest = hashlib.sha256(b"same").hexdigest()
    assert [explicit.profile_id, user.profile_id, session.profile_id, fallback.profile_id] == [
        f"profile-{digest}",
        f"user-{digest}",
        f"session-{digest}",
        "default",
    ]
    assert len({id(explicit), id(user), id(session), id(fallback)}) == 4

    prefixed = await module.get_profile(str(tmp_path), profile_id="user-bob")
    raw_user = await module.get_profile(str(tmp_path), user_id="bob")
    punctuated = await module.get_profile(str(tmp_path), profile_id="a/b")
    dashed = await module.get_profile(str(tmp_path), profile_id="a-b")
    assert len({prefixed.profile_id, raw_user.profile_id, punctuated.profile_id, dashed.profile_id}) == 4


def test_style_and_meta_followups_inherit_topic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_profile_module(monkeypatch)
    profile = module.UserProfile(anyio.Path(tmp_path), profile_id="learner")

    topic = profile.update("解释 Transformer attention 原理", "answer")

    assert profile.update("讲短一点, 换个例子", "answer") == topic
    assert profile.update("我刚才的问题是什么意思?", "answer") == topic


def test_signed_double_speed_ema_and_global_warm_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_profile_module(monkeypatch)
    profile = module.UserProfile(anyio.Path(tmp_path), profile_id="learner")

    key = profile.update("请深入讲解 Rust 原理", "answer")
    topic = profile.topics[key]
    assert topic["dimensions"]["depth"] == pytest.approx(0.64)
    assert topic["short_term"]["depth"] == pytest.approx(0.82)

    profile._update_global_profile()
    new_key = profile.update("数据库是什么", "answer")
    assert new_key != key
    assert profile.topics[new_key]["dimensions"]["depth"] == pytest.approx(0.598)

    low = module.UserProfile(anyio.Path(tmp_path), profile_id="low")
    low_key = low.update("Python 简单说就行", "answer")
    assert low.topics[low_key]["dimensions"]["depth"] == pytest.approx(0.395)
    assert low.topics[low_key]["short_term"]["depth"] == pytest.approx(0.26)


@pytest.mark.anyio
async def test_reload_followup_uses_last_real_topic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_profile_module(monkeypatch)
    profile = module.UserProfile(anyio.Path(tmp_path), profile_id="reload")
    topic = profile.update("请深入解释 attention 原理", "answer")
    await profile.save()

    reloaded = module.UserProfile(anyio.Path(tmp_path), profile_id="reload")
    await reloaded.load()

    assert reloaded.last_topic_key == topic
    assert reloaded.update("讲短一点", "answer") == topic


def test_zero_turn_topics_do_not_divide_by_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_profile_module(monkeypatch)
    profile = module.UserProfile(anyio.Path(tmp_path), profile_id="zero")
    first = profile.get_or_create_topic("first", ["same"])
    second = profile.get_or_create_topic("second", ["same"])
    first["last_seen"] = "1"
    second["last_seen"] = "2"

    profile._auto_merge_topics()

    assert len(profile.topics) == 1
    assert next(iter(profile.topics.values()))["turns"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_dimensions,bad_turns",
    [({"depth": "nan", "goal": [], "familiarity": "oops"}, "bad"), ([], None), ({"depth": float("inf")}, -3)],
)
async def test_malformed_profile_yaml_loads_safe_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_dimensions,
    bad_turns,
) -> None:
    module = _load_profile_module(monkeypatch)
    profile = module.UserProfile(anyio.Path(tmp_path), profile_id="malformed")
    path = anyio.Path(tmp_path / "wiki" / "profiles" / f"{profile.profile_id}.md")
    await path.parent.mkdir(parents=True)
    payload = {
        "topics": {
            "valid": {
                "label": "valid",
                "keywords": "not-a-list",
                "dimensions": bad_dimensions,
                "short_term": {"depth": {}},
                "turns": bad_turns,
                "signals": [],
                "recent_signals": "bad",
            },
            "invalid": "not-a-dict",
        }
    }
    await path.write_text(f"---\n{module.yaml.safe_dump(payload)}---\n", encoding="utf-8")

    await profile.load()

    assert set(profile.topics) == {"valid"}
    assert profile.topics["valid"]["dimensions"] == module.DEFAULT_DIMENSIONS
    assert profile.topics["valid"]["turns"] == 0


@pytest.mark.anyio
async def test_concurrent_record_turn_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_profile_module(monkeypatch)
    profile = module.UserProfile(anyio.Path(tmp_path), profile_id="concurrent")

    async with anyio.create_task_group() as tg:
        for _ in range(20):
            tg.start_soon(profile.record_turn, "深入解释 Python 原理", "raw answer")

    reloaded = module.UserProfile(anyio.Path(tmp_path), profile_id="concurrent")
    await reloaded.load()
    real_topics = [topic for key, topic in reloaded.topics.items() if key != module.GLOBAL_TOPIC_KEY]
    assert sum(topic["turns"] for topic in real_topics) == 20
    saved = await anyio.Path(tmp_path / "wiki" / "profiles" / f"{profile.profile_id}.md").read_text(encoding="utf-8")
    assert "raw answer" not in saved


def test_policy_uses_current_turn_and_single_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_system_module(monkeypatch)

    third = module._build_profile_policy({"turns": 2, "dimensions": {"familiarity": 0.7}})
    fifth = module._build_profile_policy({"turns": 4, "dimensions": {"familiarity": 0.7}})
    sixth = module._build_profile_policy({"turns": 5, "dimensions": {"familiarity": 0.7}})
    gated = module._build_profile_policy({"turns": 5, "dimensions": {"familiarity": 0.5}})

    assert "本轮必须提问" in third
    assert "破圈条件未满足" in third
    assert "本轮必须提出跨领域" in fifth
    assert "本轮必须提出跨领域" in sixth
    assert "破圈条件未满足" in gated


@pytest.mark.anyio
async def test_generated_prompt_has_one_profile_and_policy_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_system_module(monkeypatch)

    async def base_prompt(_self) -> str:
        return "stable<!-- HAITUN_CACHE_BOUNDARY -->dynamic"

    monkeypatch.setattr(module.System, "build_system_prompt", base_prompt)
    prompt = await module.system_prompt_builder(
        {"role": "user", "content": "深入讲 Python 原理", "user_id": "runtime"},
        workspace_raw=str(tmp_path),
    )

    assert prompt.count("## 当前知识点学习画像") == 1
    assert prompt.count("## 强制监督规则") == 1
    assert not await anyio.Path(tmp_path / "wiki" / "profiles" / "user-runtime.md").exists()


def test_standalone_demo_is_runnable_without_real_wiki(tmp_path: Path) -> None:
    demo = Path(__file__).parents[2] / "examples" / "haitun-workspace" / "demo_adaptive_profile.py"
    real_profiles = demo.parent / "wiki" / "profiles"
    profiles_before = set(real_profiles.glob("*")) if real_profiles.exists() else set()
    env = os.environ | {"HAITUN_DEMO_WORKSPACE": str(tmp_path), "PYTHONUTF8": "1"}

    result = subprocess.run(
        [sys.executable, str(demo)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "BEFORE" in result.stdout
    assert "AFTER UPDATE" in result.stdout
    assert "NEXT PROMPT" in result.stdout
    assert "USER alice" in result.stdout
    assert "USER bob" in result.stdout
    profile_paths = [line for line in result.stdout.splitlines() if line.startswith("PROFILE FILE")]
    assert len(profile_paths) == 2
    assert profile_paths[0] != profile_paths[1]
    assert "深入原理" in result.stdout
    assert "一句话结论" in result.stdout
    profiles_after = set(real_profiles.glob("*")) if real_profiles.exists() else set()
    assert profiles_after == profiles_before


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
    saved = await anyio.Path(tmp_path / "wiki" / "profiles" / "default.md").read_text(encoding="utf-8")
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

    saved = await (wiki / "profiles" / "default.md").read_text(encoding="utf-8")
    assert "version: 2" in saved
    assert "history:" not in saved
    assert "旧回复原文" not in saved
    assert "过拟合" in saved
