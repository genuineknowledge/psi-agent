"""Reconcile the two `.env` injection steps in `pyinstaller.yml` against the
single key list they both claim to follow.

`runtime-env-keys.txt` is the source of truth, and both injection steps read it at
runtime -- but each step also needs an `env:` block naming every secret, because
GitHub's `secrets` context cannot be indexed by a variable. Those two blocks are
hand-written, so they can drift from the list and from each other.

Drift fails silently in the worst direction: a key present in the list but absent
from an `env:` block is simply never set in the step's environment, so the
injector writes no line for it and reports `present: False` -- exactly what a
genuinely missing secret looks like. The installed `.env` is short one variable
and nothing is red.

Usage::

    python scripts/check_runtime_env_keys.py
"""

# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pyinstaller.yml"
KEY_LIST = REPO_ROOT / ".github" / "inno-setup" / "runtime-env-keys.txt"
STEP_NAME = "Inject runtime .env"


def parse_key_list(text: str) -> list[str]:
    keys: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            keys.append(line)
    return keys


def injection_env_blocks(text: str) -> list[dict[str, str]]:
    """The `env:` mapping of every step named `Inject runtime .env`.

    Parsed off the raw text rather than via a YAML loader so the check keeps
    working if the workflow grows anchors or other constructs, and so the
    reported order matches what a reader sees in the file.
    """
    blocks: list[dict[str, str]] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(rf"^\s*-?\s*name:\s*{re.escape(STEP_NAME)}\s*$", line):
            continue
        mapping: dict[str, str] = {}
        in_env = False
        env_indent = 0
        for follower in lines[index + 1 :]:
            stripped = follower.strip()
            if not in_env:
                if stripped == "env:":
                    in_env = True
                    env_indent = len(follower) - len(follower.lstrip())
                    continue
                # `run:` ends the step's header without an env block having started.
                if stripped.startswith("run:"):
                    break
                continue
            indent = len(follower) - len(follower.lstrip())
            if stripped and indent <= env_indent:
                break
            entry = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", follower)
            if entry:
                mapping[entry.group(1)] = entry.group(2).strip()
        blocks.append(mapping)
    return blocks


def _force_utf8_output() -> None:
    """把 stdout/stderr 切成 UTF-8。**本脚本所有输出都是中文, 不切会直接崩。**

    与 `scripts/check_pyinstaller_workspace_imports.py` 同款做法。这一步排在那个判据
    之后 (pyinstaller.yml:191), PR 878 里前一个先崩, 于是这个**根本没跑到** —— 缺陷
    一模一样, 只是被上一步的失败掩盖着。别等它自己在 CI 上露头。

    修在脚本里而不是给 workflow 加 `PYTHONIOENCODING`: 判据不该依赖调用方的环境才不崩。

    `reconfigure` 在流被替换成非 `TextIOWrapper` 时可能不存在(某些捕获实现), 所以先探
    再调; 探不到就维持原样, 不为了日志把主流程搞挂。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    keys = parse_key_list(KEY_LIST.read_text(encoding="utf-8"))
    if not keys:
        print(f"::error::{KEY_LIST.name} 里一个 key 都没有, 判据本身失效了")
        return 1

    blocks = injection_env_blocks(WORKFLOW.read_text(encoding="utf-8"))
    # 两处: Windows (pwsh) 和 macOS (bash)。少一处说明有人删了注入或改了步骤名。
    if len(blocks) != 2:
        print(f"::error::期望 2 个名为 '{STEP_NAME}' 的步骤, 实际 {len(blocks)} 个")
        return 1

    print(f"清单 ({KEY_LIST.relative_to(REPO_ROOT).as_posix()}) 共 {len(keys)} 个 key:")
    for key in keys:
        print(f"  {key}")

    failures = 0
    for position, mapping in enumerate(blocks, start=1):
        label = f"第 {position} 处注入 env 块"
        missing = [key for key in keys if key not in mapping]
        extra = [key for key in mapping if key not in keys]
        print(f"\n{label}: {len(mapping)} 个条目")
        for key in missing:
            print(f"::error::{label} 缺 {key} —— 该步骤里这个变量永远不会有值, 装机 .env 会少一行且不报错")
            failures += 1
        for key in extra:
            print(f"::error::{label} 多出 {key}, 不在清单里 —— 清单是唯一来源, 两处会就此分叉")
            failures += 1
        # 值必须真取自同名 secret: 写成 `${{ secrets.OTHER }}` 会让日志报 present
        # 而 .env 里落的是另一个 secret 的值。
        for key, value in mapping.items():
            if key in keys and f"secrets.{key}" not in value:
                print(f"::error::{label} 的 {key} 取值不是 secrets.{key}, 而是 {value!r}")
                failures += 1
        if not missing and not extra:
            print(f"  与清单一致 ({len(keys)} 个 key 全覆盖)")

    if failures:
        print(f"\n{failures} 处不一致。两处注入必须与 {KEY_LIST.name} 完全对齐。")
        return 1
    print(f"\n两处注入的清单一致, 且都与 {KEY_LIST.name} 对齐。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
