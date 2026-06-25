#!/usr/bin/env python3
"""Fusion Flow stateful-session shim.

把 flow 运行时（bundle）对子 agent 的「一次性 psi-agent run」调用，劫持成「连一个常驻
psi-agent session」，从而让同一个子 agent（同一 system prompt）跨多轮调用保持记忆。

bundle 实际发来的命令形如：
    <this-shim> run --workspace W --message "<system>\\n\\n---\\n\\n<prompt>" \\
                --output-format text [--model M] [--ai-socket S] [--ai ...] ...
我们只关心 --workspace / --message / --model；其余忽略。

机制：
  key = sha256(system)[:16]               # 同角色每轮恒定（已探针验证）
  key 无常驻 session → 起一个 psi-agent session（复用本 run 共享的 AI backend），记 key→socket
  key 有             → 复用
  → psi-agent channel cli --session-socket <socket> --message <prompt>  （只发本轮 prompt）
  stdout 原样回给 bundle（bundle 全程以为自己在调 psi-agent run）

生命周期：所有常驻 session + 共享 AI backend 的 pid/socket 登记在
  $FUSION_SHIM_STATE_DIR（默认 /tmp/fusion-shim-<ppid>），由 flow 结束后的清理脚本统一 kill。

环境变量（复用 flow 既有的 FLOW_PSI_*）：
  PSI_CMD                  起 psi-agent 的命令前缀，默认 "uv run --no-sync psi-agent"
  FLOW_PSI_WORKSPACE       子 agent 执行 workspace（必需）
  FLOW_PSI_MODEL           模型名
  FLOW_PSI_API_KEY         AI backend key
  FLOW_PSI_BASE_URL        AI backend base url
  FLOW_PSI_AI              backend 类型，默认 openai-completions
  FLOW_PSI_AI_SOCKET       若给定，直接复用这个现成 AI backend，不自起
  FUSION_SHIM_STATE_DIR    会话/进程登记目录（默认据 PPID 派生）
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path


def _psi_cmd() -> list[str]:
    return shlex.split(os.environ.get("PSI_CMD", "uv run --no-sync psi-agent"))


def _state_dir() -> Path:
    raw = os.environ.get("FUSION_SHIM_STATE_DIR") or f"/tmp/fusion-shim-{os.getppid()}"
    d = Path(raw)
    (d / "sockets").mkdir(parents=True, exist_ok=True)
    return d


def _parse_args(argv: list[str]) -> dict[str, str]:
    """从 bundle 发来的 `run --flag val ...` 里取我们关心的几个。"""
    out: dict[str, str] = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--workspace", "--message", "--model", "--ai-socket", "--ai") and i + 1 < len(argv):
            out[a.lstrip("-")] = argv[i + 1]
            i += 2
            continue
        i += 1
    return out


def _split_message(message: str) -> tuple[str, str]:
    """message = system + '\\n\\n---\\n\\n' + prompt（无 system 时整体即 prompt）。"""
    sep = "\n\n---\n\n"
    if sep in message:
        system, prompt = message.split(sep, 1)
        return system, prompt
    return "", message


def _session_key(system: str) -> str:
    return hashlib.sha256(system.encode("utf-8")).hexdigest()[:16]


def _wait_socket(sock: Path, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if sock.is_socket():
            return True
        time.sleep(0.2)
    return sock.is_socket()


def _record_pid(state: Path, kind: str, pid: int, sock: Path) -> None:
    """登记进程，供 cleanup 脚本统一回收。一行一条 JSON。"""
    with (state / "procs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, "pid": pid, "socket": str(sock)}) + "\n")


def _ensure_ai_backend(state: Path) -> str:
    """返回一个可用的 AI backend socket 路径。

    优先复用 FLOW_PSI_AI_SOCKET；否则本 run 起一个共享 backend（只起一次，落盘记 socket）。
    """
    explicit = os.environ.get("FLOW_PSI_AI_SOCKET")
    if explicit:
        return explicit

    marker = state / "ai_socket"
    if marker.exists():
        sock = marker.read_text(encoding="utf-8").strip()
        if Path(sock).is_socket():
            return sock

    sock = state / "sockets" / "shared-ai.sock"
    cmd = [
        *_psi_cmd(), "ai", os.environ.get("FLOW_PSI_AI", "openai-completions"),
        "--session-socket", str(sock),
    ]
    if os.environ.get("FLOW_PSI_MODEL"):
        cmd += ["--model", os.environ["FLOW_PSI_MODEL"]]
    if os.environ.get("FLOW_PSI_API_KEY"):
        cmd += ["--api-key", os.environ["FLOW_PSI_API_KEY"]]
    if os.environ.get("FLOW_PSI_BASE_URL"):
        cmd += ["--base-url", os.environ["FLOW_PSI_BASE_URL"]]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    _record_pid(state, "ai", proc.pid, sock)
    if not _wait_socket(sock):
        sys.stderr.write(f"[shim] AI backend socket not up: {sock}\n")
        sys.exit(1)
    marker.write_text(str(sock), encoding="utf-8")
    return str(sock)


def _ensure_session(state: Path, key: str, workspace: str, model: str, ai_socket: str) -> str:
    """返回该 key 对应常驻 session 的 channel-socket；无则起一个。"""
    sock = state / "sockets" / f"sess-{key}.sock"
    if sock.is_socket():
        return str(sock)

    cmd = [
        *_psi_cmd(), "session",
        "--workspace", workspace,
        "--channel-socket", str(sock),
        "--ai-socket", ai_socket,
    ]
    if model:
        cmd += ["--model", model]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    _record_pid(state, "session", proc.pid, sock)
    if not _wait_socket(sock):
        sys.stderr.write(f"[shim] session socket not up for key={key}: {sock}\n")
        sys.exit(1)
    return str(sock)


def _send(channel_socket: str, prompt: str) -> int:
    """通过一次性 channel cli 把 prompt 发给常驻 session，输出原样转发给 bundle。"""
    cmd = [*_psi_cmd(), "channel", "cli", "--session-socket", channel_socket, "--message", prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def main(argv: list[str]) -> int:
    # argv 形如: run --workspace W --message M ...  （第一个 token 'run' 直接忽略）
    args = _parse_args(argv)
    message = args.get("message", "")
    workspace = args.get("workspace") or os.environ.get("FLOW_PSI_WORKSPACE", "")
    model = args.get("model") or os.environ.get("FLOW_PSI_MODEL", "")
    if not workspace:
        sys.stderr.write("[shim] no workspace (need --workspace or FLOW_PSI_WORKSPACE)\n")
        return 1

    system, prompt = _split_message(message)
    key = _session_key(system)

    state = _state_dir()
    ai_socket = _ensure_ai_backend(state)
    channel_socket = _ensure_session(state, key, workspace, model, ai_socket)
    # 首条消息需带上 system（让常驻 session 拿到角色设定）；之后同 key 复用、只发 prompt。
    first_marker = state / "sockets" / f"sess-{key}.primed"
    if not first_marker.exists():
        payload = f"{system}\n\n---\n\n{prompt}" if system else prompt
        first_marker.write_text("1", encoding="utf-8")
    else:
        payload = prompt
    return _send(channel_socket, payload)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

