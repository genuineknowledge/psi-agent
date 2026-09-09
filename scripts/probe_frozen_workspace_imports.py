"""Import, inside a frozen bundle, every external module the desktop workspace
needs at load time -- and report which ones are missing.

This is the **artifact layer** of the packaging check: the layer the 2026-08-18
incident lacked. Source-layer (flags contain the entry) and build-layer
(PyInstaller logged the module) both pass while the shipped binary still cannot
import the module, because neither looks at the binary.

Run it two ways:

* from source, to see the list this repo currently needs::

      python scripts/probe_frozen_workspace_imports.py --list

* frozen, as the payload of a throwaway one-file exe built with the production
  flags. Missing modules are printed and the exit code is 1.

The list is deliberately **measured, not derived**. Scanning for "imported by
`agents/desktop` but not by `src/`" yields 42 candidates, of which only 4 are
genuinely absent from a real bundle -- the rest arrive transitively via the
third-party packages already collected. A check built on the static list would be
38/42 false positives and would be turned off within a week.

`ALLOWED_MISSING` records the measured exceptions with the reason each one is
fine, so a new absence is a failure rather than a line in a long amber list.
"""

# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。

from __future__ import annotations

import argparse
import os
import shlex
import sys

# workspace 在**模块级**(无守卫) import 的外部模块, 缺了就是加载期失败。
# 由 scripts 目录的同名判据在源码侧核对, 这里只负责在 frozen 环境里逐个试。
REQUIRED = (
    # Fusion Memory 的存储层。1.0.15 补的第二个缺口: src/ 里零引用, 所以
    # --collect-submodules psi_agent 覆盖不到, 缺了摄取链照样断。
    "sqlite3",
    # 语音转写 (tools/_xfyun_stt.py)。同上, 无守卫的模块级 import。
    "wave",
    # 内核侧: workspace 的 system.py 直接导入这四个。
    "psi_agent",
    "psi_agent.session._compaction",
    "psi_agent.session.history_display",
    "psi_agent.session.prompt_budget",
    "psi_agent.session.runtime_context",
)

# 实测缺席但**不该**判失败的, 连同理由。
ALLOWED_MISSING = {
    # POSIX-only。Windows 上 bin/session_shim.py 走 msvcrt 分支, 缺席是正确的。
    "fcntl": "POSIX 专有; Windows 侧走 msvcrt 分支",
    # 缺的是 dist-info 元数据而非模块本身 (--collect-submodules 不带元数据), 且
    # tools/github.py 是惰性 import + friendly "not installed" 降级。
    "pygount": "只缺 dist-info 元数据, 调用点有降级路径",
}


def _force_utf8_stdout() -> None:
    """把 stdout 切成 UTF-8。

    这个仓库踩过一次: windows-latest 的 stdout 是 cp1252/cp936, 中文编不出去,
    于是**判据本身**抛 UnicodeEncodeError 退出 1 —— 明明产物是好的却报红
    (见 tests/test_gen_legal_html.py 的 cp1252 那条用例)。探针跑在打包 job 的
    windows runner 上, 同一个坑就在脚下。
    """
    stream = getattr(sys.stdout, "reconfigure", None)
    if stream is not None:
        stream(encoding="utf-8", errors="replace")


def probe() -> int:
    _force_utf8_stdout()
    missing: list[str] = []
    for name in REQUIRED:
        try:
            __import__(name)
        # 捕获 Exception 而非 ImportError: pygount 那类缺的是包元数据, 抛的是
        # PackageNotFoundError —— 只接 ImportError 会漏掉一整类缺口。
        except Exception as exc:
            missing.append(f"{name}  ({type(exc).__name__}: {exc})")
        else:
            print(f"OK      {name}")
    frozen = getattr(sys, "frozen", False)
    print(f"\n探针环境: frozen={frozen}, {len(REQUIRED)} 个模块, 缺 {len(missing)} 个")
    if not frozen:
        print("::warning::不在 frozen 环境里跑 —— 这只证明源码环境可用, 不是产物层结论")
    for entry in missing:
        print(f"::error::{entry} 在包里不可用")
    if missing:
        print(
            f"\n{len(missing)} 个模块装进 exe 后 import 不到。装机后对应功能静默失效 "
            f"(1.0.14 就是这样丢掉整条 Fusion Memory 自动摄取链)。"
            f"修法: 在 PYINSTALLER_COMMON_FLAGS 里补 --hidden-import 或 --collect-submodules。"
        )
        return 1
    print("产物层通过: 所有模块在 frozen 环境里都能 import。")
    return 0


def collect_flags(raw: str) -> str:
    """`PYINSTALLER_COMMON_FLAGS` 去掉产物命名/落点和 `--add-data` 后的部分。

    探针要跟主产物在"收了哪些模块"上完全一致, 所以收集类 flag 一条都不动; 只有产物
    命名和 spa 资源是探针不需要的。按 token 剥而不是正则替换: `--add-data` 的值里带
    路径和冒号, 正则很容易连带吃掉别的东西。

    住在脚本里而不是 workflow 的 `run:` 块里, 是因为 YAML 块标量容不下顶格的多行
    Python —— 内联那份写法会让整个 workflow 解析失败。
    """
    tokens = raw.split()
    kept: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"--add-data", "--name", "--distpath"}:
            index += 2  # 连值一起跳过
            continue
        if token == "--onefile":
            index += 1
            continue
        kept.append(token)
        index += 1
    return shlex.join(kept)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="只打印清单, 不做 import")
    parser.add_argument(
        "--collect-flags",
        action="store_true",
        help="打印 PYINSTALLER_COMMON_FLAGS 去掉产物命名与 --add-data 后的部分",
    )
    args = parser.parse_args(argv)
    if args.list:
        for name in REQUIRED:
            print(name)
        return 0
    if args.collect_flags:
        raw = os.environ.get("PYINSTALLER_COMMON_FLAGS", "")
        if not raw:
            print("::error::PYINSTALLER_COMMON_FLAGS 是空的, 探针会用一份与主产物不同的 flags 编", file=sys.stderr)
            return 1
        print(collect_flags(raw))
        return 0
    return probe()


if __name__ == "__main__":
    sys.exit(main())
