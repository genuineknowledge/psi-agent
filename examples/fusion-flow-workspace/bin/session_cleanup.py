#!/usr/bin/env python3
"""Fusion Flow shim cleanup —— flow 跑完后回收本 run 起的所有常驻进程。

用法：
    session_cleanup.py [STATE_DIR]
STATE_DIR 默认取 $FUSION_SHIM_STATE_DIR，再不行据 PPID 派生（须与 shim 一致）。

读取 STATE_DIR/procs.jsonl（shim 登记的每个 session / ai backend），逐个 SIGTERM→SIGKILL，
再删 socket 文件与 STATE_DIR。幂等：可重复调用。
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path


def _resolve_state(argv: list[str]) -> Path:
    if len(argv) > 1 and argv[1]:
        return Path(argv[1])
    raw = os.environ.get("FUSION_SHIM_STATE_DIR") or f"/tmp/fusion-shim-{os.getppid()}"
    return Path(raw)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not _alive(pid):
            return
        try:
            os.killpg(os.getpgid(pid), sig)
        except OSError:
            try:
                os.kill(pid, sig)
            except OSError:
                return
        time.sleep(0.4)


def main(argv: list[str]) -> int:
    state = _resolve_state(argv)
    procs_file = state / "procs.jsonl"
    killed = 0
    if procs_file.exists():
        for line in procs_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            pid = int(rec.get("pid", 0))
            if pid and _alive(pid):
                _kill(pid)
                killed += 1
            sock = rec.get("socket")
            if sock:
                try:
                    Path(sock).unlink()
                except OSError:
                    pass
    # 整个 state 目录端掉（含 sockets/、marker）
    if state.exists():
        shutil.rmtree(state, ignore_errors=True)
    sys.stderr.write(f"[shim-cleanup] killed {killed} proc(s), removed {state}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
