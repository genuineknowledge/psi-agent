"""闸门: 全库禁止 ``contextlib.suppress(CancelledError)``。

2026-09-16 生产上一个飞书用户的 session 被锁死约 3 小时。链路的最后一环是
``forget_and_wait`` 里的 ``suppress(asyncio.CancelledError)``: 一条执行流取消了它
**自己所在**的任务, 而这个 suppress 把「取消已经提出」这个事实从调用栈里抹掉, 任务
于是「已被提出取消却仍然活着」。外层 anyio task group 的 ``__aexit__`` 便通过
``loop.call_soon`` 无限重试交付取消 —— 事件循环 100% 忙转、``turn_lock`` 永不释放、
零日志。这个 suppress 不是事故的**原因**, 是它的**必要条件**: 少了它, 取消会正常向外
传播, 任务结束, 什么都不会卡住。

所以这道闸门要挡的是形状, 而不是某一处代码。它不判断「这个 suppress 包住的 await
是不是自己的任务」—— 那个判断在静态层面做不到, 而事故本身证明了人也判不准。

扫描用 ``ast`` 而不是正则, 有三个具体理由, 都是这个仓库栽过的:

1. 正则漏别名。``import contextlib as c`` / ``from contextlib import suppress`` /
   ``from asyncio import CancelledError`` 写出来的文本里没有
   ``suppress(asyncio.CancelledError)`` 这个串。同一类盲区在改名自查那次让判据报了
   0 处而代码里还剩 10 处 (见 ``rename-regex-misses-segmented-paths``)。
2. 正则漏分段拼接。异常元组先赋给一个常量再 ``suppress(*_EXC)`` 展开, 两段文本永远
   不相邻。
3. 正则会误报散文。全库有 7 处注释与 docstring 里的文字提及 (包括本文件), 它们讨论
   的正是这个事故。``ast`` 只看真的 Call 节点, 字符串与注释天然不命中 —— 这也是本文件
   可以放心地把危险形状**写成字符串样本**来自证判据吃劲的原因。

白名单精确到「文件 + 形状 + 次数」而不是整文件豁免: 整文件豁免会让那个文件以后新写
的一处也悄悄放过, 而已知存量恰恰就在 ``tests/`` 下, 挖掉整个目录等于把覆盖面清零。
次数进 key 是为了让同一文件里**再写一处一模一样的**也会转红。
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

# 不是仓库应用代码, 也不该由本闸门管: 依赖目录与构建产物。
# ``docs/superpowers/salvage`` **刻意留在扫描范围内** —— 那是生产「领先」文件的逐字节
# 存底, 真出现这个形状说明生产上有一处仓库还不知道的, 正是最该报出来的情况。
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".kanban",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
        ".eggs",
    }
)

_SUPPRESS = "suppress"
_CANCELLED = "CancelledError"


@dataclass(frozen=True)
class _Hit:
    """一处命中。``shape`` 是 ``ast.unparse`` 还原出的调用文本, 用来对白名单。"""

    path: str
    lineno: int
    shape: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}  {self.shape}"


# 显式白名单: key 是 (仓库相对路径, 形状), value 是 (允许的处数, 理由)。
#
# 行号**不进** key: 上下几行一动就要来改白名单, 那种噪音会让人干脆改成整文件豁免。
# 形状 + 处数已经足够: 换一种写法、或者多写一处, 都会转红。
_ALLOWED: dict[tuple[str, str], tuple[int, str]] = {
    (
        "tests/psi_agent/session/test_tool_taskgroup_livelock_guard.py",
        "contextlib.suppress(TimeoutError, asyncio.CancelledError)",
    ): (
        1,
        # 事故那处 forget_and_wait 的逐字形状: 先 me.cancel(), 再在 asyncio.timeout 里
        # await 自己, 两个异常都吞掉。判据要**构造**出活锁来验证 agent.py 的探测层, 不
        # suppress 就构造不出「已被提出取消却仍然活着」的任务, 探测层也就无从被触发。
        "判据需要复现该形状: 构造 task group 子任务里的自指取消, 验证活锁探测层能报出来",
    ),
    (
        "tests/psi_agent/session/test_tool_taskgroup_livelock_guard.py",
        "contextlib.suppress(asyncio.CancelledError)",
    ): (
        1,
        # 同一文件的反例: 取消的是**别人**的任务。少了这条, 一个「什么都不取消」的守卫
        # 也能让正例全绿。这处 suppress 是正常的跨任务取消收尾, 不是自指。
        "判据需要复现该形状: 反例里取消别人的任务并等它收尾, 证明守卫没退化成「永不取消」",
    ),
}


def _python_files() -> list[Path]:
    return [
        p
        for p in sorted(_REPO_ROOT.rglob("*.py"))
        if not any(part in _SKIP_DIRS for part in p.relative_to(_REPO_ROOT).parts)
    ]


def _mentions_cancelled_error(node: ast.expr) -> bool:
    """这个实参里出现 ``CancelledError`` 吗。

    走整棵子树而不是只看顶层, 因为实参可以是 ``asyncio.CancelledError``、裸
    ``CancelledError``、``(TimeoutError, CancelledError)`` 元组, 或者 ``*_EXC`` 展开。
    只按名字判、不解析别名到底指向谁: 一个叫 ``CancelledError`` 的东西被 suppress 掉,
    无论它从哪来都是本闸门要挡的形状。
    """
    return any(
        (isinstance(sub, ast.Name) and sub.id == _CANCELLED)
        or (isinstance(sub, ast.Attribute) and sub.attr == _CANCELLED)
        for sub in ast.walk(node)
    )


def _suppress_names(tree: ast.Module) -> frozenset[str]:
    """这个模块里, 哪些名字指向 ``contextlib.suppress``。

    总是包含 ``suppress`` 本身 (裸调用、``contextlib.suppress``、``c.suppress`` 的末段
    都是它)。此外把 ``from contextlib import suppress as quiet`` 里的 ``quiet`` 也算上
    —— 我自己的样本先把这个盲区暴露出来了: 只认 ``suppress`` 三个字, 改个名就绕过去。
    """
    names = {_SUPPRESS}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "contextlib":
            names.update(alias.asname for alias in node.names if alias.name == _SUPPRESS and alias.asname)
    return frozenset(names)


def _named_bindings(tree: ast.Module) -> dict[str, list[ast.expr]]:
    """``NAME = <expr>`` 的赋值表, 用来展开 ``suppress(*_EXC)`` 这种分段拼接。

    异常元组先赋给一个常量、再在别处 ``*`` 展开, 是正则永远看不见的形状: 两段文本不
    相邻。一个名字可能被赋多次, 所以值是列表 —— 任意一次赋值里出现 ``CancelledError``
    就算命中, 宁可多报也不留盲区。
    """
    bindings: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        value = node.value
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bindings.setdefault(target.id, []).append(value)
    return bindings


def _scan(source: str, rel_path: str) -> list[_Hit]:
    """找出 ``source`` 里所有 ``suppress(... CancelledError ...)`` 调用。

    按被调用者的**末段名字**认 ``suppress``: ``contextlib.suppress(...)``、
    ``c.suppress(...)`` (别名导入的模块)、裸 ``suppress(...)`` (``from contextlib
    import suppress``) 三种写法都是同一个 ``ast.Call``, 名字都落在 ``Attribute.attr``
    或 ``Name.id`` 上; 改过名的 ``suppress`` 由 ``_suppress_names`` 补上。不去核实它真的
    来自 ``contextlib``: 自己写一个叫 ``suppress`` 的东西来吞 ``CancelledError``, 同样是
    这道闸门要挡的。
    """
    tree = ast.parse(source)
    callable_names = _suppress_names(tree)
    bindings = _named_bindings(tree)

    def mentions(arg: ast.expr) -> bool:
        if _mentions_cancelled_error(arg):
            return True
        # ``*_EXC`` / 裸 ``_EXC``: 顺着赋值追一层。只追一层是够的 —— 再套一层间接的写法
        # 至今没在这个仓库出现过, 而每多追一层就多一份把无关代码误报的机会。
        inner = arg.value if isinstance(arg, ast.Starred) else arg
        if isinstance(inner, ast.Name):
            return any(_mentions_cancelled_error(bound) for bound in bindings.get(inner.id, []))
        return False

    hits: list[_Hit] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            called = func.attr
        elif isinstance(func, ast.Name):
            called = func.id
        else:
            continue
        if called not in callable_names:
            continue
        args: list[ast.expr] = [*node.args, *(kw.value for kw in node.keywords)]
        if any(mentions(arg) for arg in args):
            hits.append(_Hit(rel_path, node.lineno, ast.unparse(node)))
    return hits


@cache
def _scan_repo() -> tuple[_Hit, ...]:
    """全库扫一遍。缓存: 三条判据都要这份结果, 而解析 900 余个文件占了本文件几乎全部耗时。"""
    hits: list[_Hit] = []
    for path in _python_files():
        rel = path.relative_to(_REPO_ROOT).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:  # pragma: no cover - 非 utf-8 的 .py 是异常情况
            pytest.fail(f"{rel} 读不出来, 闸门无法覆盖它: {exc}")
        try:
            hits.extend(_scan(source, rel))
        except SyntaxError as exc:  # pragma: no cover - 语法坏了本身该由 lint 报
            pytest.fail(f"{rel} 解析失败, 闸门无法覆盖它: {exc}")
    return tuple(hits)


def test_the_repository_does_not_suppress_cancelled_error_outside_the_whitelist() -> None:
    """本闸门的正题: 除白名单外, 全库不得出现 ``suppress(CancelledError)``。"""
    hits = _scan_repo()
    counted = Counter((hit.path, hit.shape) for hit in hits)

    offenders = [hit for hit in hits if (hit.path, hit.shape) not in _ALLOWED]
    assert not offenders, (
        "新增了 suppress(CancelledError) —— 它把「取消已提出」从调用栈里抹掉, "
        "是 2026-09-16 那次 3 小时会话锁死的必要条件。\n"
        + "\n".join(f"  {hit}" for hit in offenders)
        + "\n真的必须这么写(比如判据要复现该形状), 就把它加进本文件的 _ALLOWED 并写清理由。"
    )

    # 白名单是「这一处允许」, 不是「这个形状随便写」: 同一文件里再写一处一模一样的也要转红。
    for key, (allowed, reason) in _ALLOWED.items():
        actual = counted.get(key, 0)
        assert actual <= allowed, f"{key[0]} 里 `{key[1]}` 出现 {actual} 处, 白名单只放行 {allowed} 处({reason})。"


def test_the_whitelist_has_no_stale_entries() -> None:
    """白名单条目必须还对应着真实存量。

    存量被清掉而条目留着, 就成了一张对未来的空白许可: 以后在同一文件里新写一处一样的,
    静默放过。所以白名单只能记录**现在真的存在**的处数。
    """
    counted = Counter((hit.path, hit.shape) for hit in _scan_repo())
    stale = {key: allowed for key, (allowed, _) in _ALLOWED.items() if counted.get(key, 0) < allowed}
    assert not stale, f"白名单条目已无对应存量, 请删掉(留着等于给未来发空白许可): {stale}"


def test_every_whitelist_entry_carries_a_reason() -> None:
    """每条白名单都要写理由 —— 没有理由的豁免下一个人无从判断能不能删。"""
    for key, (_, reason) in _ALLOWED.items():
        assert reason.strip(), f"{key} 缺理由"
        assert "判据" in reason or "复现" in reason, f"{key} 的理由太空泛, 说不清为什么必须这么写: {reason!r}"


# 危险形状的样本。它们是**字符串**, 因此不会命中扫描自己 —— 这正是 ``ast`` 相比正则的
# 好处之一: 散文与样本天然不参与判定, 不需要「跳过 tests/」这种把覆盖面挖空的排除法。
#
# 前四条是别名导入的各种写法, 文本里都**没有** ``suppress(asyncio.CancelledError)``
# 这个串 —— 正则照抄形状就会全漏。第五条是分段拼接。
_DANGEROUS_SAMPLES = {
    "contextlib 别名导入": "import contextlib as c\nwith c.suppress(CancelledError):\n    pass\n",
    "from contextlib import suppress": (
        "from asyncio import CancelledError\nfrom contextlib import suppress\n"
        "with suppress(CancelledError):\n    pass\n"
    ),
    "suppress 改名": "from contextlib import suppress as quiet\nwith quiet(CancelledError):\n    pass\n",
    "CancelledError 从 anyio 之类的别处来": (
        "import contextlib\nfrom asyncio.exceptions import CancelledError\n"
        "with contextlib.suppress(TimeoutError, CancelledError):\n    pass\n"
    ),
    "异常元组先赋给常量再展开": (
        "import asyncio\nimport contextlib\n_EXC = (TimeoutError, asyncio.CancelledError)\n"
        "with contextlib.suppress(*_EXC):\n    pass\n"
    ),
    "事故那处的原形状": (
        "import asyncio\nimport contextlib\nwith contextlib.suppress(asyncio.CancelledError):\n    await task\n"
    ),
}

_SAFE_SAMPLES = {
    "suppress 别的异常": "import contextlib\nwith contextlib.suppress(TimeoutError):\n    pass\n",
    "捕获 CancelledError 但重新抛出": ("import asyncio\ntry:\n    pass\nexcept asyncio.CancelledError:\n    raise\n"),
    "只在注释里提到": "# 这里刻意不 suppress(asyncio.CancelledError), 那是活锁的必要条件\npass\n",
    "只在 docstring 里提到": '"""原实现是 contextlib.suppress(asyncio.CancelledError)。"""\n',
    "字符串常量里提到": 'MSG = "never write contextlib.suppress(asyncio.CancelledError)"\n',
}


@pytest.mark.parametrize("label", sorted(_DANGEROUS_SAMPLES))
def test_the_scan_catches_every_dangerous_spelling(label: str) -> None:
    """判据吃劲的证据: 别名导入与分段拼接都要被扫出来。

    这几条是 ``ast`` 而非正则的**理由**本身。少了它们, 上面那条正题在扫描退化成正则、
    或者退化成「只认 ``asyncio.CancelledError`` 这一种写法」之后, 照旧全绿。
    """
    hits = _scan(_DANGEROUS_SAMPLES[label], f"<sample:{label}>")
    assert hits, f"这种写法没被扫出来, 闸门有盲区: {_DANGEROUS_SAMPLES[label]!r}"


@pytest.mark.parametrize("label", sorted(_SAFE_SAMPLES))
def test_the_scan_does_not_flag_prose_or_unrelated_suppress(label: str) -> None:
    """反例: 注释、docstring、字符串里的文字提及不算存量。

    全库有 7 处这类提及, 讨论的正是这次事故。正则会把它们全部误报, 一道天天误报的闸门
    最后只会被加个整文件豁免绕过去。
    """
    hits = _scan(_SAFE_SAMPLES[label], f"<sample:{label}>")
    assert not hits, f"误报: {hits}"
