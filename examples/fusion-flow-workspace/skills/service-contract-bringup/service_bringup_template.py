#!/usr/bin/env python3
"""Pluggable scaffold: stand up a long-running service to an EXACT, externally-probed contract.

The deliverable of this class of task is not a file or a sequence of steps — it is a
*running process* that answers a contract the grader probes from OUTSIDE: specific
ports, socket/file paths, process-cmdline tokens, served content, log formats. The
grader never inspects how you did it; it connects to the surface and checks it
literally. So the scaffold factors the work into the three parts that actually vary:

    launch()   -> start the daemon; return a handle that must be KEPT ALIVE
    ready()    -> cheap readiness probe; True once the contract surface answers
    contract   -> the exact surface, as a list of out-of-band Probes (connect from
                  outside the process and assert the literal port/path/content/token)

Everything else — wait-for-ready with timeout, run-the-probes, report — is generic.

Ships two self-tested instances in DIFFERENT domains to prove this isn't one task
wearing adjectives:
    A. http-file-contract    : serve exact bytes at an exact TCP port + URL path
                               (shape of: package/index servers, git-over-http, web roots)
    B. unix-control-contract : expose a control endpoint at an exact UNIX socket path
                               (shape of: VM/daemon monitor sockets, IPC control planes)

    python3 service_bringup_template.py --selftest

NOTE FOR REAL TASKS: in selftest the services are torn down at the end to stay tidy.
In an actual task you LEAVE THE PROCESS RUNNING — it is the deliverable. Never clean
it up as a final step.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


# ----------------------------------------------------------------------------- core
@dataclass
class Probe:
    """One out-of-band check the grader would run: connect from outside, assert literally."""
    desc: str
    check: Callable[[], None]  # raises AssertionError on failure


@dataclass
class Service:
    name: str
    launch: Callable[[], subprocess.Popen]
    ready: Callable[[], bool]
    contract: list[Probe] = field(default_factory=list)
    ready_timeout_s: float = 15.0


def wait_ready(ready: Callable[[], bool], timeout_s: float) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            if ready():
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def bring_up_and_verify(svc: Service, keep_running: bool = False) -> bool:
    """Generic harness: launch -> wait ready -> run every contract probe -> report.

    Returns True iff the process stayed up AND every probe passed. With keep_running
    the process is intentionally left alive (real-task behavior); otherwise it is
    terminated after verification (selftest hygiene).
    """
    proc = svc.launch()
    ok = True
    try:
        if not wait_ready(svc.ready, svc.ready_timeout_s):
            print(f"  [{svc.name}] FAIL readiness: surface never answered in {svc.ready_timeout_s}s")
            return False
        if proc.poll() is not None:
            print(f"  [{svc.name}] FAIL: process exited early (rc={proc.returncode})")
            return False
        for p in svc.contract:
            try:
                p.check()
                print(f"  [{svc.name}] PASS  {p.desc}")
            except AssertionError as e:
                ok = False
                print(f"  [{svc.name}] FAIL  {p.desc}: {e}")
        return ok
    finally:
        if not keep_running:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


# ------------------------------------------------------------------ generic probes
def probe_process_alive(proc_getter: Callable[[], subprocess.Popen]) -> Probe:
    return Probe("process is running", lambda: _assert(proc_getter().poll() is None, "process not alive"))


def probe_tcp_listening(host: str, port: int) -> Probe:
    def chk():
        with socket.create_connection((host, port), timeout=3):
            pass
    return Probe(f"TCP {host}:{port} is listening", chk)


def probe_http_body(url: str, want_status: int, want_body: bytes) -> Probe:
    def chk():
        with urllib.request.urlopen(url, timeout=4) as r:
            status, body = r.status, r.read()
        _assert(status == want_status, f"status {status} != {want_status}")
        _assert(want_body in body, f"body {body!r} missing {want_body!r}")
    return Probe(f"GET {url} -> {want_status} + exact content", chk)


def probe_cmdline_token(pid_getter: Callable[[], int], token: str) -> Probe:
    """Mirror graders that read /proc/<pid>/cmdline for a literal contract token."""
    def chk():
        cl = Path(f"/proc/{pid_getter()}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        _assert(token in cl, f"cmdline missing token {token!r}: {cl!r}")
    return Probe(f"process cmdline carries {token!r}", chk)


def probe_unix_socket_exists(path: str) -> Probe:
    import stat
    def chk():
        _assert(os.path.exists(path), f"socket path {path} missing")
        _assert(stat.S_ISSOCK(os.stat(path).st_mode), f"{path} is not a socket")
    return Probe(f"UNIX socket exists at exact path {path}", chk)


def probe_unix_request(path: str, send: bytes, want: bytes) -> Probe:
    def chk():
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(path)
        s.sendall(send)
        got = s.recv(256)
        s.close()
        _assert(want in got, f"reply {got!r} missing {want!r}")
    return Probe(f"UNIX control {path} answers {send!r}->{want!r}", chk)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# ------------------------------------------------------------- example instance A
def instance_http_file(port: int = 8731) -> Service:
    """Serve exact bytes at an exact TCP port + URL path (separate process, probed out-of-band)."""
    tmp = Path(tempfile.mkdtemp(prefix="svc_http_"))
    (tmp / "vectorlib.txt").write_bytes(b"dotproduct-ok")
    holder: dict[str, subprocess.Popen] = {}

    def launch() -> subprocess.Popen:
        p = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        holder["p"] = p
        return p

    def ready() -> bool:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True

    return Service(
        name="http-file-contract",
        launch=launch,
        ready=ready,
        contract=[
            probe_process_alive(lambda: holder["p"]),
            probe_tcp_listening("127.0.0.1", port),
            probe_http_body(f"http://127.0.0.1:{port}/vectorlib.txt", 200, b"dotproduct-ok"),
            probe_cmdline_token(lambda: holder["p"].pid, str(port)),
        ],
    )


# ------------------------------------------------------------- example instance B
_UNIX_SERVER_SRC = r"""
import socket, sys, os
path = sys.argv[1]
if os.path.exists(path): os.unlink(path)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind(path); s.listen(8)
while True:
    try:
        c, _ = s.accept()
        data = c.recv(256)
        c.sendall(b"PONG\n" if data.strip() == b"PING" else b"ERR\n")
        c.close()
    except OSError:
        # A readiness probe may connect and close immediately; never let one
        # broken client take the daemon down.
        try:
            c.close()
        except Exception:
            pass
"""


def instance_unix_control(sock_path: str | None = None) -> Service:
    """Expose a control endpoint at an EXACT UNIX socket path (separate process, probed out-of-band)."""
    sock_path = sock_path or os.path.join(tempfile.mkdtemp(prefix="svc_ctl_"), "control.sock")
    holder: dict[str, subprocess.Popen] = {}

    def launch() -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, "-c", _UNIX_SERVER_SRC, sock_path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        holder["p"] = p
        return p

    def ready() -> bool:
        if not os.path.exists(sock_path):
            return False
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(sock_path)
        s.close()
        return True

    return Service(
        name="unix-control-contract",
        launch=launch,
        ready=ready,
        contract=[
            probe_process_alive(lambda: holder["p"]),
            probe_unix_socket_exists(sock_path),
            probe_unix_request(sock_path, b"PING\n", b"PONG"),
        ],
    )


# ----------------------------------------------------------------------- selftest
def selftest() -> int:
    print("service-contract-bringup scaffold selftest")
    results = []
    for make in (instance_http_file, instance_unix_control):
        svc = make()
        print(f"\n== instance: {svc.name} ==")
        results.append(bring_up_and_verify(svc, keep_running=False))
    print()
    if all(results):
        print(f"SELFTEST PASS — {len(results)} service domains brought up & contract-verified")
        return 0
    print("SELFTEST FAIL")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="pluggable service-contract bring-up scaffold")
    ap.add_argument("--selftest", action="store_true", help="run the two example domains")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(selftest())
    print(__doc__)
