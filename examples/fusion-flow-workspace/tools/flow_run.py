"""Run a Fusion Flow (.flow.ts) in the background and poll node-level progress.

一个 flow 运行是长任务: 每个 flow.session 是外部 CLI 子进程 (10-20s 冷启动) ,
flow.parallel 辩论 / 多步 pipeline 串起来一次跑几分钟。用同步 bash 跑会被超时
SIGKILL 掉, 误报"引擎超时"。本工具改为后台运行 + 阻塞式进度轮询:

  start  -> 后台起 `npx tsx <flow>`, stdout 重定向到日志, 解析 [run] <runId>/dir,
            登记状态文件, 立即返回 run_token (=runId) 。
  status -> 阻塞到"下个节点完成 / flow 结束 / 窗口超时"三者之一, 返回进度快照。
            进度来自运行时增量写的 runs/<runId>/progress.jsonl。
  result -> flow 结束后读 meta.json / bindings / execution-graph 返回最终产物。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import anyio

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# 运行时启动即打印的两行:  "[run] <runId>" 和 "[run] dir: <runDir>"
_RUN_ID_RE = re.compile(r"^\[run\]\s+(\S+)\s*$")
_RUN_DIR_RE = re.compile(r"^\[run\]\s+dir:\s+(.+?)\s*$")


def _state_dir() -> Path:
    raw = os.environ.get("FLOW_RUN_STATE_DIR")
    base = Path(raw) if raw else Path.home() / ".psi" / "flow-run"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _state_file(token: str) -> Path:
    return _state_dir() / f"{token}.json"


def _find_bash() -> str | None:
    if os.name == "nt":
        for c in (
            "C:/Program Files/Git/bin/bash.exe",
            "C:/Program Files/Git/usr/bin/bash.exe",
            "D:/Program Files/Git/bin/bash.exe",
        ):
            if Path(c).is_file():
                return c
    return shutil.which("bash")


def _parse_run_header(log_path: Path, deadline: float) -> tuple[str, str]:
    """Tail the log until the runtime prints [run] <runId> and [run] dir: <dir>."""
    run_id = ""
    run_dir = ""
    while time.time() < deadline:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            text = ""
        for line in text.splitlines():
            if not run_id:
                m = _RUN_ID_RE.match(line)
                # 跳过 "[run] dir:" / "[run] resuming" 之类, 仅纯 runId 行
                if m and ":" not in m.group(1) and m.group(1) != "dir":
                    run_id = m.group(1)
            md = _RUN_DIR_RE.match(line)
            if md:
                run_dir = md.group(1)
        if run_id and run_dir:
            return run_id, run_dir
        time.sleep(0.3)
    return run_id, run_dir


def _spawn_flow(flow: Path, workdir: str, log_path: Path) -> tuple[int, str, str]:
    """Blocking: spawn the detached `npx tsx <flow>` and parse its runId/runDir.

    Runs in a worker thread (see _start) so the async tool never blocks. Returns
    (pid, run_id, run_dir); run_id/run_dir are "" if the runtime never printed
    its start header within the timeout (start likely failed).
    """
    argv = ["npx", "tsx", str(flow)]
    bash = _find_bash()
    if os.name == "nt" and bash:
        # 经 git-bash -lc, 保证 npx/tsx 的解析与运行时既有约定一致
        popen_args: list[str] = [bash, "-lc", " ".join(argv)]
        use_shell = False
    else:
        popen_args = argv
        use_shell = os.name == "nt"

    kwargs: dict = {
        "cwd": workdir,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    with open(log_path, "w", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(popen_args, shell=use_shell, stdout=log_fh, **kwargs)
    run_id, run_dir = _parse_run_header(log_path, time.time() + 30.0)
    return proc.pid, run_id, run_dir


async def _start(flow_path: str, cwd: str) -> dict:
    flow = Path(flow_path)
    if not await anyio.to_thread.run_sync(flow.is_file):
        return {"ok": False, "message": f"flow file not found: {flow_path}"}
    workdir = cwd.strip() or str(flow.parent)

    log_path = _state_dir() / f"flow-{int(time.time() * 1000)}.log"
    pid, run_id, run_dir = await anyio.to_thread.run_sync(_spawn_flow, flow, workdir, log_path)

    if not run_id or not run_dir:
        tail = ""
        with contextlib.suppress(OSError):
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
        return {
            "ok": False,
            "message": "flow did not report a runId within 30s (start likely failed)",
            "alive": _pid_alive(pid),
            "pid": pid,
            "log_tail": tail,
        }

    token = run_id
    state = {
        "run_token": token,
        "run_id": run_id,
        "run_dir": run_dir,
        "pid": pid,
        "log_path": str(log_path),
        "cwd": workdir,
        "flow_path": str(flow),
        "started_ts": time.time(),
        "cursor": 0,  # 已上报的 node_end 计数游标
    }
    _state_file(token).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return {
        "ok": True,
        "run_token": token,
        "run_id": run_id,
        "run_dir": run_dir,
        "pid": pid,
        "message": "flow started in background",
    }


def _load_state(token: str) -> dict | None:
    p = _state_file(token)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None


def _tail(path: str, n: int = 800) -> str:
    """Blocking: read the last n chars of a file (run via to_thread)."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[-n:]
    except OSError:
        return ""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_progress(run_dir: str) -> list[dict]:
    """Read runs/<id>/progress.jsonl, tolerating a half-written trailing line."""
    p = Path(run_dir) / "progress.jsonl"
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    events: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue  # 末尾未写完的残行, 忽略
    return events


def _nodes_summary(events: list[dict]) -> list[dict]:
    """Collapse start/end events into per-node status rows."""
    by_id: dict[str, dict] = {}
    for e in events:
        nid = e.get("id", "")
        if not nid:
            continue
        row = by_id.setdefault(nid, {"id": nid, "type": e.get("type", ""), "status": "running"})
        if e.get("label"):
            row["label"] = e["label"]
        if e.get("event") == "node_end":
            row["status"] = e.get("status", "ok")
            if "durationMs" in e:
                row["durationMs"] = e["durationMs"]
    return list(by_id.values())


def _done(run_dir: str) -> bool:
    return (Path(run_dir) / "meta.json").is_file()


async def _status(token: str, window_seconds: float) -> dict:
    state = _load_state(token)
    if state is None:
        return {"ok": False, "message": f"unknown run_token: {token}"}
    run_dir = state["run_dir"]
    pid = int(state.get("pid", 0))
    cursor = int(state.get("cursor", 0))

    # 阻塞到: 出现新的 node_end / flow 结束(meta.json) / 进程死 / 窗口超时
    deadline = time.time() + max(1.0, window_seconds)
    while True:
        events = await anyio.to_thread.run_sync(_read_progress, run_dir)
        ended = sum(1 for e in events if e.get("event") == "node_end")
        done = await anyio.to_thread.run_sync(_done, run_dir)
        alive = _pid_alive(pid)
        crashed = (not alive) and (not done)
        if ended > cursor or done or crashed or time.time() >= deadline:
            state["cursor"] = ended
            _state_file(token).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            nodes = _nodes_summary(events)
            resp = {
                "ok": True,
                "run_token": token,
                "alive": alive,
                "done": done,
                "nodes": nodes,
                "completed": ended,
                "elapsed_s": round(time.time() - state.get("started_ts", time.time()), 1),
            }
            if crashed:
                resp["crashed"] = True
                resp["log_tail"] = await anyio.to_thread.run_sync(_tail, state["log_path"])
            return resp
        await anyio.sleep(0.5)


async def _result(token: str) -> dict:
    state = _load_state(token)
    if state is None:
        return {"ok": False, "message": f"unknown run_token: {token}"}
    run_dir = Path(state["run_dir"])
    meta_path = run_dir / "meta.json"
    if not meta_path.is_file():
        return {"ok": False, "message": "run not finished yet (no meta.json)", "done": False}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": f"failed to read meta.json: {exc}"}
    bindings: dict[str, str] = {}
    bdir = run_dir / "bindings"
    if bdir.is_dir():
        for f in sorted(bdir.glob("*.md")):
            try:
                bindings[f.stem] = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return {
        "ok": True,
        "run_token": token,
        "status": meta.get("status", "unknown"),
        "meta": meta,
        "bindings": bindings,
    }


async def flow_run(
    action: str,
    flow_path: str = "",
    run_token: str = "",
    cwd: str = "",
    window_seconds: float = 60.0,
) -> str:
    """Run a Fusion Flow (.flow.ts) in the background and poll its node-level progress.

    A flow run takes minutes (each step is an external CLI subprocess), so it must
    NOT be run through the synchronous ``bash`` tool — that would time out and
    SIGKILL the flow. Use this tool instead: ``start`` launches it in the
    background, ``status`` blocks until the next progress step, ``result`` returns
    the final output.

    Args:
        action: ``start`` | ``status`` | ``result``.
        flow_path: (start) absolute or cwd-relative path to the ``.flow.ts`` file.
        run_token: (status/result) the token returned by ``start``.
        cwd: (start) working dir to run from; empty = the flow file's directory.
        window_seconds: (status) max seconds to block before returning a keepalive
            snapshot when there is no new progress (default 60).

    Returns:
        JSON. start: {ok, run_token, run_id, run_dir, pid}. status: {ok, alive,
        done, nodes:[{id,type,label,status,durationMs}], completed, elapsed_s,
        crashed?, log_tail?}. result: {ok, status, meta, bindings}.
    """
    act = action.strip().lower()
    if act == "start":
        if not flow_path:
            return json.dumps({"ok": False, "message": "start requires flow_path"}, ensure_ascii=False)
        return json.dumps(await _start(flow_path, cwd), ensure_ascii=False)
    if act == "status":
        if not run_token:
            return json.dumps({"ok": False, "message": "status requires run_token"}, ensure_ascii=False)
        return json.dumps(await _status(run_token, window_seconds), ensure_ascii=False)
    if act == "result":
        if not run_token:
            return json.dumps({"ok": False, "message": "result requires run_token"}, ensure_ascii=False)
        return json.dumps(await _result(run_token), ensure_ascii=False)
    return json.dumps(
        {"ok": False, "message": f"unknown action {action!r}; use start|status|result"},
        ensure_ascii=False,
    )
