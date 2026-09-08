"""``_feishu/*.py`` must each import standalone, in any order.

The tool registry loads ``agents/feishu/tools/*.py`` by glob order (字母序,
``tool_registry.py``), so which module lands in ``sys.modules`` first is not
something this code gets to choose. A private module that only imports cleanly
*after* ``_feishu_impl`` is already loaded is therefore a latent failure, not a
working design: production hit exactly that, 59 tool files failing with

    ImportError: cannot import name '_LEDGER_SCHEMA_FIELDS' from partially
    initialized module '_feishu.mentor_ledger' (most likely due to a circular
    import)

because ``_feishu_impl`` imports the constant from ``mentor_ledger`` while
``mentor_ledger`` imports ``_feishu_impl`` as ``_core`` at module scope. The
``_feishu_impl`` import sits at the *bottom* of that file, so loading it first
happened to work and hid the cycle for as long as glob order got lucky.

Each import runs in a **fresh interpreter**: doing it in-process would let an
earlier test's ``sys.modules`` supply the module that is meant to be missing,
which is the very thing that made this bug invisible.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[3] / "agents" / "feishu" / "tools"

# Every private domain module, so a cycle introduced in any of them fails here
# and not only in the one module that already regressed once.
_PRIVATE_MODULES = sorted(p.stem for p in (_TOOLS / "_feishu").glob("*.py") if p.stem != "__init__")

# The same cycle exists in these modules and is *not* fixed: each is re-exported by
# ``_feishu_impl`` for 3 to 91 names apiece (``message`` alone is 91), so untangling
# them means splitting inert data from client-touching code module by module, far past
# one domain that actually broke production. They are listed rather than skipped
# wholesale so a *new* module is failing-by-default, and ``strict=True`` means fixing
# one turns this test red until it moves out of the list.
_KNOWN_CYCLIC = frozenset(
    {
        "approval",
        "attendance",
        "auth",
        "bitable",
        "contact",
        "doc",
        "drive",
        "leave",
        "message",
        "sheet",
        "task",
        "worktree",
    }
)


def _import_in_fresh_interpreter(statement: str) -> subprocess.CompletedProcess[str]:
    """Run ``statement`` in a clean interpreter that can see the tools dir."""
    return subprocess.run(
        [sys.executable, "-c", statement],
        capture_output=True,
        text=True,
        cwd=_TOOLS,
        env={**os.environ, "PYTHONPATH": f"{_TOOLS}{os.pathsep}src"},
    )


@pytest.mark.parametrize(
    "module",
    [
        pytest.param(
            m,
            marks=pytest.mark.xfail(strict=True, reason="pre-existing import cycle, not in this fix's scope"),
        )
        if m in _KNOWN_CYCLIC
        else m
        for m in _PRIVATE_MODULES
    ],
)
def test_private_module_imports_without_impl_loaded_first(module: str) -> None:
    """加载顺序 B: 直接 import ``_feishu.<module>``, 不先 import ``_feishu_impl``。

    This is the production failure mode verbatim. Asserting on ``returncode`` alone
    would also pass for an unrelated crash, so the ImportError text is checked too:
    a *partially initialized module* message is the cycle's own signature.
    """
    proc = _import_in_fresh_interpreter(f"import _feishu.{module}")

    assert "partially initialized module" not in proc.stderr, (
        f"_feishu.{module} 只有在 _feishu_impl 先加载时才可导入 —— 环路仍在:\n{proc.stderr}"
    )
    assert proc.returncode == 0, f"_feishu.{module} 无法独立导入:\n{proc.stderr}"


def test_impl_first_order_still_works() -> None:
    """加载顺序 A: 先 ``_feishu_impl``。曾经唯一能过的那条路, 不许被修法弄坏。"""
    proc = _import_in_fresh_interpreter("import _feishu_impl; import _feishu.mentor_ledger")

    assert proc.returncode == 0, proc.stderr


def test_ledger_schema_is_one_object_on_both_import_paths() -> None:
    """两条路拿到的必须是同一个对象 —— 下沉常量时复制一份就会静默漂移。

    ``feishu_mentor_ledger_cycle_table`` reads it as ``_core._LEDGER_SCHEMA_FIELDS``
    while ``mentor_ledger`` uses its own module-level name; if those ever became two
    separate lists, a schema edit would apply to only one of the two tools.
    """
    proc = _import_in_fresh_interpreter(
        "import _feishu_impl as core;"
        " import _feishu.mentor_ledger as ml;"
        " print(core._LEDGER_SCHEMA_FIELDS is ml._LEDGER_SCHEMA_FIELDS)"
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "True", proc.stdout
