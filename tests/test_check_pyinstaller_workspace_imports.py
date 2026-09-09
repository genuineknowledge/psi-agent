"""`scripts/check_pyinstaller_workspace_imports.py` 的判据测试。

守的是 1.0.14 那次静默失效: workspace 的 `system.py` 是运行时按路径 exec 的, 不在
PyInstaller 静态 import 图里, 它导入的内核模块没被打包保证 -> `ModuleNotFoundError`
-> `system_after_turn` 退化成 no-op -> Fusion Memory 零摄取, 且无任何告警。

**判据能拦住这件事全靠非 0 返回**, 所以红的那一面必须逐条测: 只测"当前库内配置是绿的"
在 `main()` 恒返回 0 时照样通过, 等于守门的那一半没测。

脚本在 `scripts/` 下不属包, 故用 importlib 按路径加载。
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_pyinstaller_workspace_imports.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_pyinstaller_workspace_imports", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


chk = _load()


def _write_system(tmp_path: Path, body: str, pack: str = "desktop") -> Path:
    path = tmp_path / "agents" / pack / "systems" / "system.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_finds_module_level_and_nested_imports(tmp_path: Path) -> None:
    """三种位置都要扫到: 模块级、TYPE_CHECKING 块内、函数体内。

    真实的 `system.py` 三种都有 —— `runtime_context` 那三条就在函数体里被守卫导入。
    延迟导入的模块缺包时在**调用时**才炸, 比加载期失败更难发现, 所以同样要覆盖。
    """
    path = _write_system(
        tmp_path,
        "from psi_agent.session._compaction import compact_history\n"
        "import psi_agent.session.prompt_budget\n"
        "if TYPE_CHECKING:\n"
        "    from psi_agent.session.history_display import message_kind\n"
        "def f():\n"
        "    from psi_agent.session.runtime_context import get_agent\n",
    )
    assert chk._imported_kernel_modules(path) == {
        "psi_agent.session._compaction",
        "psi_agent.session.prompt_budget",
        "psi_agent.session.history_display",
        "psi_agent.session.runtime_context",
    }


def test_ignores_non_kernel_and_relative_imports(tmp_path: Path) -> None:
    """兄弟模块和三方包不该混进清单 —— 它们不由这条 flag 负责。

    `psi_agentx` 这种前缀相同但不同包的名字也要排除, 否则清单里会出现打包侧根本
    不存在的条目, 判据变成恒红。
    """
    path = _write_system(
        tmp_path,
        "from prompt_sections import CONTEXT_FILE_ORDER\n"
        "from . import sibling\n"
        "from .relative import thing\n"
        "import anyio\n"
        "import psi_agentx.other\n"
        "from psi_agent import cli\n",
    )
    assert chk._imported_kernel_modules(path) == {"psi_agent"}


def test_wholesale_flag_covers_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_system(tmp_path, "from psi_agent.session._compaction import compact_history\n")
    monkeypatch.setattr(chk, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(chk, "WORKFLOW", _workflow_at(tmp_path, "--collect-submodules psi_agent\n--onefile"))
    assert chk.main([]) == 0


def test_missing_coverage_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**这条就是 1.0.14 的现场**: flags 里 20 条 --collect-submodules 全是三方包。"""
    _write_system(tmp_path, "from psi_agent.session._compaction import compact_history\n")
    monkeypatch.setattr(chk, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        chk, "WORKFLOW", _workflow_at(tmp_path, "--collect-submodules any_llm\n--collect-submodules mcp")
    )
    assert chk.main([]) == 1
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "psi_agent.session._compaction" in out


def test_hidden_import_fallback_counts_per_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """退回逐条 --hidden-import 时, 覆盖齐了才算过, 缺一条就红。

    锁的是"临时方案也得吃劲": 补了一个模块就以为修好了, 是这次事故的复发形状。
    """
    _write_system(
        tmp_path,
        "from psi_agent.session._compaction import compact_history\n"
        "from psi_agent.session.prompt_budget import PromptBudget\n",
    )
    monkeypatch.setattr(chk, "REPO_ROOT", tmp_path)

    monkeypatch.setattr(chk, "WORKFLOW", _workflow_at(tmp_path, "--hidden-import psi_agent.session._compaction"))
    assert chk.main([]) == 1
    assert "psi_agent.session.prompt_budget" in capsys.readouterr().out

    monkeypatch.setattr(
        chk,
        "WORKFLOW",
        _workflow_at(
            tmp_path,
            "--hidden-import psi_agent.session._compaction\n--hidden-import psi_agent.session.prompt_budget",
            name="flags2.yml",
        ),
    )
    assert chk.main([]) == 0


def test_prefix_collision_is_not_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--collect-submodules psi_agent_extras` 不该被当成 `psi_agent` 的覆盖。

    子串匹配会在这里假绿 —— 假绿的判据比没有判据更坏。
    """
    _write_system(tmp_path, "from psi_agent.session._compaction import compact_history\n")
    monkeypatch.setattr(chk, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(chk, "WORKFLOW", _workflow_at(tmp_path, "--collect-submodules psi_agent_extras"))
    assert chk.main([]) == 1


def test_yaml_folding_is_collapsed_before_matching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """flag 与包名在 YAML 折叠块里被换行分开时也要认出来。

    `PYINSTALLER_COMMON_FLAGS` 是 `>-` 块, 每条 flag 独占一行; 不做折叠归一化,
    `--collect-submodules psi_agent` 这个两词串在原文里根本不相邻, 判据会恒红。
    """
    _write_system(tmp_path, "from psi_agent.session._compaction import compact_history\n")
    monkeypatch.setattr(chk, "REPO_ROOT", tmp_path)
    workflow = tmp_path / "folded.yml"
    workflow.write_text(
        "    env:\n      FLAGS: >-\n        --onefile\n        --collect-submodules\n        psi_agent\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(chk, "WORKFLOW", workflow)
    assert chk.main([]) == 0


def test_no_system_files_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """扫不到 system.py 要红, 不能"没找到 = 没问题"地放过。

    改目录结构或改错 glob 时, 静默返回 0 会让判据消失而没人知道。
    """
    monkeypatch.setattr(chk, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(chk, "WORKFLOW", _workflow_at(tmp_path, "--collect-submodules psi_agent"))
    assert chk.main([]) == 1


def test_system_without_kernel_imports_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """扫到文件却一条内核导入都没有, 同样判失效 —— 现实里两份 system.py 都有。"""
    _write_system(tmp_path, "import anyio\n")
    monkeypatch.setattr(chk, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(chk, "WORKFLOW", _workflow_at(tmp_path, "--collect-submodules psi_agent"))
    assert chk.main([]) == 1


def _workflow_at(tmp_path: Path, flags: str, name: str = "flags.yml") -> Path:
    path = tmp_path / name
    path.write_text(flags, encoding="utf-8")
    return path


def test_repo_state_passes() -> None:
    """库内真实配置应为绿。改动 flags 却漏掉 workspace 依赖时这条会红。"""
    assert chk.main([]) == 0


def test_survives_cp1252_stdout(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """cp1252 的 stdout 下, 库内配置仍要返回 0。

    实测的 CI 失败 (PR 878, run 34313907771): windows-latest 的 stdout 是 cp1252,
    `main()` 第一条中文 print 抛 UnicodeEncodeError 退出 1 —— flags 覆盖其实是完整的,
    却在 PyInstaller 开跑之前就把整个打包 job 判红。用真的 cp1252 缓冲区复现,
    不 mock print 了事: 要锁的正是「中文能编出去」。
    """
    with capsys.disabled():
        buf = io.BytesIO()
        cp1252 = io.TextIOWrapper(buf, encoding="cp1252", newline="")
        monkeypatch.setattr(sys, "stdout", cp1252)
        try:
            rc = chk.main([])
        finally:
            cp1252.flush()

    assert rc == 0
    # reconfigure 之后写进去的是 UTF-8 字节, 所以按 UTF-8 读回来。
    assert "整包收进" in buf.getvalue().decode("utf-8")


def test_repo_systems_include_the_module_that_broke_1_0_14() -> None:
    """清单里必须有 `_compaction` —— 判据声称覆盖的就是它。

    绑定到真实文件而不是构造输入: `SYSTEM_GLOB` 或库内路径变了, 这条会红。
    """
    systems = sorted(chk.REPO_ROOT.glob(chk.SYSTEM_GLOB))
    assert len(systems) >= 2
    found: set[str] = set()
    for path in systems:
        found |= chk._imported_kernel_modules(path)
    assert "psi_agent.session._compaction" in found
