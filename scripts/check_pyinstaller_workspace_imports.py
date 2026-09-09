"""Reconcile the kernel modules workspace ``system.py`` files import against the
PyInstaller flags that are supposed to put those modules in the bundle.

Why this exists (1.0.14 field failure, err.log line 127)::

    ModuleNotFoundError: No module named 'psi_agent.session._compaction'

``agents/*/systems/system.py`` is ``exec``-ed by path at runtime, so it is **not**
in PyInstaller's static import graph. Whether a kernel module it imports ends up
in the bundle depends on whether ``src/psi_agent/cli.py`` (the packaging entry
point) happens to reach it transitively -- a coincidence, not a guarantee. When
the coincidence failed, ``system.py`` failed to load, ``system_after_turn``
silently degraded to the no-op default, and Fusion Memory ingested nothing. No
alarm fired: histories kept being written and the Memory tools still worked by
hand, so only the automatic-ingest chain was dead.

The check: parse the imports, then require the flags to cover every module
either wholesale (``--collect-submodules psi_agent``) or one by one
(``--hidden-import psi_agent.X``). Missing coverage is an ``::error::`` and a
non-zero exit.

Usage::

    python scripts/check_pyinstaller_workspace_imports.py
"""

# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pyinstaller.yml"
SYSTEM_GLOB = "agents/*/systems/system.py"
ROOT_PACKAGE = "psi_agent"


def _imported_kernel_modules(path: Path) -> set[str]:
    """Every ``psi_agent.*`` module name ``path`` imports, at any nesting depth.

    ``ast.walk`` rather than a line regex on purpose: the real file has these
    imports at module level, inside ``if TYPE_CHECKING:``, and inside function
    bodies (the guarded ``runtime_context`` ones). A module imported lazily still
    has to be in the bundle -- it fails at call time instead of load time, which
    is strictly harder to notice.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import -- a sibling workspace module
            # (prompt_sections), not a kernel one.
            if node.level == 0 and node.module and _is_kernel(node.module):
                found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_kernel(alias.name):
                    found.add(alias.name)
    return found


def _is_kernel(module: str) -> bool:
    return module == ROOT_PACKAGE or module.startswith(f"{ROOT_PACKAGE}.")


def _force_utf8_output() -> None:
    """把 stdout/stderr 切成 UTF-8。**本脚本所有输出都是中文, 不切会直接崩。**

    实测的 CI 失败 (PR 878, run 34313907771): windows-latest 的 stdout 是 cp1252,
    `print("扫到 N 个 system.py...")` 抛 UnicodeEncodeError 退出 1 —— 覆盖其实是
    完整的, 却在打包开始前就把整个 job 判红。这比漏检更坏: 判据变成了「Windows 上必红」。

    修在脚本里而不是给 workflow 加 `PYTHONIOENCODING`: 判据不该依赖调用方的环境才不崩,
    且本仓开发机就是 Windows, 人在 cmd.exe 里跑会撞同一个坑。

    `reconfigure` 在流被替换成非 `TextIOWrapper` 时可能不存在(某些捕获实现), 所以先探
    再调; 探不到就维持原样, 不为了日志把主流程搞挂。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _workflow_flags() -> str:
    """The workflow text, with YAML folding collapsed to single spaces.

    ``PYINSTALLER_COMMON_FLAGS`` is a ``>-`` folded block, so each flag sits on
    its own line in the file but is one space-separated string at runtime. Match
    against the joined form or every multi-token flag looks absent.
    """
    return re.sub(r"\s+", " ", WORKFLOW.read_text(encoding="utf-8"))


def _covered_wholesale(flags: str) -> bool:
    return f"--collect-submodules {ROOT_PACKAGE} " in f"{flags} "


def _hidden_imports(flags: str) -> set[str]:
    return set(re.findall(r"--hidden-import (psi_agent[\w.]*)", flags))


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    systems = sorted(REPO_ROOT.glob(SYSTEM_GLOB))
    if not systems:
        print(f"::error::找不到任何 {SYSTEM_GLOB}, 判据本身失效了")
        return 1

    required: dict[str, list[str]] = {}
    for path in systems:
        rel = path.relative_to(REPO_ROOT).as_posix()
        for module in _imported_kernel_modules(path):
            required.setdefault(module, []).append(rel)
    if not required:
        print(f"::error::{len(systems)} 个 system.py 里一条 {ROOT_PACKAGE} 导入都没扫到, 判据本身失效了")
        return 1

    flags = _workflow_flags()
    print(f"扫到 {len(systems)} 个 system.py, {len(required)} 个内核模块被导入:")
    for module in sorted(required):
        print(f"  {module}  <- {', '.join(sorted(required[module]))}")

    if _covered_wholesale(flags):
        print(f"\n--collect-submodules {ROOT_PACKAGE} 在 flags 里: 整包收进, {len(required)} 个模块全部覆盖。")
        return 0

    hidden = _hidden_imports(flags)
    missing = sorted(module for module in required if module not in hidden)
    print(
        f"\n没有 --collect-submodules {ROOT_PACKAGE}, 按逐条 --hidden-import 核: "
        f"flags 里有 {len(hidden)} 条, 缺 {len(missing)} 条。"
    )
    if not missing:
        print("逐条覆盖完整。注意这是临时方案: 每加一个 import 都要记得同步这个文件。")
        return 0
    for module in missing:
        print(f"::error::{module} 被 {', '.join(sorted(required[module]))} 导入, 但 pyinstaller.yml 里没有对应条目")
    print(
        f"\n{len(missing)} 个模块不在打包 flags 里。装机后 system.py 会 ModuleNotFoundError, "
        f"system_after_turn 静默退化成 no-op。修法: 在 PYINSTALLER_COMMON_FLAGS 里加 "
        f"--collect-submodules {ROOT_PACKAGE} (首选, 一次覆盖), 或逐条补 --hidden-import。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
