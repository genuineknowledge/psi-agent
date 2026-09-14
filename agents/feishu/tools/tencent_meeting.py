"""Tencent Meeting tools exposed to the Feishu Haitun agent.

The upstream skill is distributed as a command-line MCP proxy rather than a
long-lived stdio server.  This thin entry point keeps the agent-facing surface
small while forwarding the JSON-RPC method and parameters unchanged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import _content_layers as _layers
import anyio
from _meeting_automation import automation_runtime

#: 技能包内的入口脚本, 相对某一层的 ``skills/`` 目录。
_SCRIPT_REL = ("tencent-meeting-mcp", "scripts", "tencent_meeting.py")

#: 单根世界里的老落点。分层未声明时 ``layers_for`` 本身就退化成这个目录, 所以这条只在
#: ``_content_layers`` 整个不可用时兜底(见 ``_skill_script``)。
_LEGACY_SCRIPT = Path(__file__).resolve().parent.parent / "skills" / Path(*_SCRIPT_REL)


async def _skill_script() -> Path:
    """按内容层梯子找上游技能的入口脚本, 就近者胜。

    内容分层把技能挪出了 ``<workspace>/skills`` —— 它们现在按 ``PSI_CONTENT_ROOTS``
    分散在各层里, 于是原来写死的 ``parent.parent/"skills"/...`` 直接断链, 这个工具
    在生产上返回"skill entrypoint not found"。

    走 ``_content_layers.layers_for("skills")`` 而不是自己拼一遍根列表: 读侧
    (``SystemPrompt``/``skill_manage``)用的是同一个梯子, 各留一份的话某次改动后会
    出现"索引里有、这里找不到"的分歧。

    **每次调用时解析, 不在 import 时**: 层是按进程的环境变量算的, 而 Gateway 一个
    进程跑很多 Session; import 期定死会把第一个 Session 的层固化给所有人。
    """
    for layer in _layers.layers_for("skills"):
        candidate = layer.path / Path(*_SCRIPT_REL)
        if await candidate.is_file():
            return Path(str(candidate))
    # 没有任何一层命中时返回老落点 —— 报错信息里给出的是那个人认得的路径。
    return _LEGACY_SCRIPT


def _default_call_timeout() -> float:
    # 单次子进程调用上限 (秒): meeting-automation.yaml runtime.tencent.call_timeout_seconds,
    # 可用 ``TENCENT_MEETING_CALL_TIMEOUT`` 环境变量覆盖。上游/网络挂起时, 超时后
    # 子进程被终止而不是让 12:00 的 cron 管道永久阻塞。
    raw = os.environ.get("TENCENT_MEETING_CALL_TIMEOUT", "").strip()
    if raw:
        return float(raw)
    return float(automation_runtime()["tencent"]["call_timeout_seconds"])


DEFAULT_CALL_TIMEOUT = _default_call_timeout()


async def _tencent_meeting_call_with_token_env(
    method: str,
    params_json: str = "",
    *,
    token_env: str = "TENCENT_MEETING_TOKEN",
    call_timeout: float = DEFAULT_CALL_TIMEOUT,
) -> str:
    """Run the MCP proxy with a selected process environment token.

    ``token_env`` is an internal routing choice made by a fixed meeting task;
    it is never part of the agent-facing tool schema or JSON-RPC payload.
    """

    token = os.environ.get(token_env, "").strip() if token_env else ""
    if not token:
        return f"Error: {token_env} is not configured in the HaiTun process."
    script = await _skill_script()
    if not script.is_file():
        return f"Error: Tencent Meeting skill entrypoint not found: {script}"

    args = [sys.executable, str(script), method]
    if params_json.strip():
        args.append(params_json)
    child_env = os.environ.copy()
    child_env["TENCENT_MEETING_TOKEN"] = token
    try:
        with anyio.fail_after(call_timeout):
            result = await anyio.run_process(args, check=False, env=child_env)
    except TimeoutError:
        return f"Error: Tencent Meeting tool timed out after {call_timeout:g}s."
    except Exception as exc:
        return f"Error: Tencent Meeting tool process failed: {type(exc).__name__}: {exc}"

    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
    output = stdout.strip()
    if stderr.strip():
        output = f"{output}\n{stderr.strip()}".strip()
    if result.returncode != 0:
        return f"Error: Tencent Meeting tool exited with code {result.returncode}.\n{output}"
    return output or "(Tencent Meeting tool returned no output)"


async def tencent_meeting_call(method: str, params_json: str = "") -> str:
    """Call a Tencent Meeting MCP method through the installed Tencent skill.

    Use this for meeting lookup, recordings, and transcript retrieval.  For
    transcript analysis, call the recording/transcript methods and prefer the
    original full transcript over smart minutes.  The token is read only from
    the process environment; it is never accepted as a tool argument.

    Args:
        method: JSON-RPC method, usually ``tools/call`` or ``tools/list``.
        params_json: JSON object string passed as the method parameters.  For
            ``tools/call``, include ``name`` and ``arguments``.
    """
    return await _tencent_meeting_call_with_token_env(method, params_json)
