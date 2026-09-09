"""`scripts/probe_frozen_workspace_imports.py` 的测试。

这个脚本是打包判据的第 3 层 (产物层) —— 8-18 事故缺的那层。它有两个职责, 各有一条
容易静默坏掉的路:

1. `collect_flags()` 决定探针用什么 flags 编。剥多了, 探针与主产物就不是同一份收集
   面, 它的绿灯**证明不了主产物**;
2. `probe()` 的非 0 返回是判据本身。恒返回 0 的话, 这层就只是一个跑得比较久的空操作。

脚本在 `scripts/` 下不属包, 故用 importlib 按路径加载。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "probe_frozen_workspace_imports.py"
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "pyinstaller.yml"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("probe_frozen_workspace_imports", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def _production_flags() -> str:
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return data["jobs"]["pyinstaller"]["env"]["PYINSTALLER_COMMON_FLAGS"]


def test_collect_flags_drops_only_naming_and_data() -> None:
    raw = (
        "--onefile --name psi-agent --distpath pyinstaller-dist "
        "--add-data src/a/dist:a/dist --add-data src/b:b "
        "--collect-submodules psi_agent --hidden-import sqlite3"
    )
    assert probe.collect_flags(raw) == "--collect-submodules psi_agent --hidden-import sqlite3"


def test_collect_flags_keeps_every_collection_flag_from_production() -> None:
    """**探针的价值全靠这条**: 收集面与主产物一致, 少一条它就证明不了主产物。"""
    raw = _production_flags()
    stripped = probe.collect_flags(raw).split()
    original = raw.split()

    kept = [t for t in original if t.startswith(("--collect-submodules", "--hidden-import"))]
    for flag in kept:
        assert flag in stripped
    # 收集类 flag 连同它们的值应一个不少地留下
    assert original.count("--collect-submodules") == stripped.count("--collect-submodules")
    assert original.count("--hidden-import") == stripped.count("--hidden-import")
    # 而 --add-data 的值 (带路径和冒号) 必须被连值剥掉, 不能留下裸路径
    assert "--add-data" not in stripped
    assert not [t for t in stripped if ":" in t and "/" in t]


def test_collect_flags_strips_add_data_values_not_just_the_flag() -> None:
    """`--add-data` 的值必须连带剥掉。

    只删 flag 名会把 `src/...:...` 留在命令行上, PyInstaller 会把它当成第二个入口
    脚本 —— 报错信息与打包无关, 排查会走很远。
    """
    raw = "--add-data src/psi_agent/i18n:psi_agent/i18n --collect-submodules psi_agent"
    assert probe.collect_flags(raw) == "--collect-submodules psi_agent"


def test_collect_flags_mode_needs_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量缺席时要报错退出, 不能打印空串。

    空 flags 会让探针编出一个**什么都没收**的 exe: 它一定会报缺模块, 判据变成恒红;
    更坏的情况是有人为了让它变绿而放宽判据。
    """
    monkeypatch.delenv("PYINSTALLER_COMMON_FLAGS", raising=False)
    assert probe.main(["--collect-flags"]) == 1


def test_collect_flags_mode_prints_stripped_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYINSTALLER_COMMON_FLAGS", "--onefile --collect-submodules psi_agent")
    assert probe.main(["--collect-flags"]) == 0
    assert capsys.readouterr().out.strip() == "--collect-submodules psi_agent"


def test_required_list_covers_the_two_measured_gaps() -> None:
    """清单必须含实测缺的那两个 + 事故里那个内核模块。"""
    assert "sqlite3" in probe.REQUIRED  # Fusion Memory 存储层
    assert "wave" in probe.REQUIRED  # 语音转写
    assert "psi_agent.session._compaction" in probe.REQUIRED  # 1.0.14 的现场


def test_required_modules_are_actually_imported_by_the_workspace() -> None:
    """清单里的每个外部模块都要真被 workspace 导入 —— 否则是凭空加的恒红项。"""
    workspace = _REPO_ROOT / "agents" / "desktop"
    sources = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in workspace.rglob("*.py"))
    for name in ("sqlite3", "wave"):
        assert f"import {name}" in sources, f"{name} 在清单里但 workspace 不导入它"


def test_allowed_missing_entries_carry_a_reason() -> None:
    """豁免项必须写明理由。

    没有理由的豁免会慢慢变成一张"已知缺失"清单, 判据就此失效 —— 这正是它要防的东西。
    """
    assert probe.ALLOWED_MISSING
    for name, reason in probe.ALLOWED_MISSING.items():
        assert reason.strip(), f"{name} 的豁免没写理由"
        assert name not in probe.REQUIRED, f"{name} 既在必需清单又在豁免清单里"


def test_probe_passes_in_this_interpreter() -> None:
    """源码环境下应全绿。

    注意这**不是**产物层结论: 源码环境里 sqlite3 本来就有。真的产物层结论只能由
    frozen exe 给出 (脚本在非 frozen 环境会打 ::warning:: 说明这一点)。
    """
    assert probe.probe() == 0


def test_probe_warns_when_not_frozen(capsys: pytest.CaptureFixture[str]) -> None:
    """非 frozen 环境要自己声明"这不是产物层结论"。

    把源码环境的绿灯当成产物层结论, 正是 8-18 事故的那种错。
    """
    probe.probe()
    assert "::warning::" in capsys.readouterr().out


def test_probe_reports_failure_for_a_missing_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """**判据能拦住事故全靠这个非 0 返回。**"""
    monkeypatch.setattr(probe, "REQUIRED", ("definitely_not_a_real_module_xyz",))
    assert probe.probe() == 1


def test_workflow_runs_the_probe_after_compile() -> None:
    """workflow 里探针那步必须在 compile 之后 (它要编第二个 exe, 顺序错了没意义)。"""
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["pyinstaller"]["steps"]
    names = [step.get("name") or step.get("run", "") for step in steps]
    compile_at = next(i for i, n in enumerate(names) if "uv run pyinstaller --log-level DEBUG" in n)
    probe_at = next(i for i, n in enumerate(names) if "frozen bundle" in n)
    source_at = next(i for i, n in enumerate(names) if "covered by packaging flags" in n)
    # 源码层在 compile 之前 (缺覆盖时不该先花十分钟编包), 产物层在之后。
    assert source_at < compile_at < probe_at
