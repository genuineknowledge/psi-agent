from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import anyio

from psi_agent.fusion_guard.analysis import build_intent_analysis_prompt, parse_intent_analysis_reply
from psi_agent.fusion_guard.messages import normalize_denial_message
from psi_agent.session.runtime_context import SessionToolContext, get_session_tool_context

AnalysisRunner = Callable[..., Awaitable[str]]
PolicyInstaller = Callable[[list[str], SessionToolContext], Awaitable[None]]
CommandExecutor = Callable[..., Awaitable[str]]


async def secure_bash(
    command: str,
    cwd: str | None = None,
    *,
    context_override: SessionToolContext | None = None,
    analysis_runner: AnalysisRunner | None = None,
    policy_installer: PolicyInstaller | None = None,
    executor: CommandExecutor | None = None,
) -> str:
    ctx = context_override or get_session_tool_context()
    if ctx is None:
        return normalize_denial_message("missing session context")

    prompt = build_intent_analysis_prompt(
        history_messages=ctx.history_messages,
        latest_user_message=ctx.latest_user_message,
        session_id=ctx.session_id,
    )
    analysis = await (analysis_runner or run_intent_analysis_in_temp_cli)(prompt=prompt, ctx=ctx)
    parsed = parse_intent_analysis_reply(analysis)

    if parsed.decision == "deny":
        return normalize_denial_message("Fusion-Guard denied the requested operation")
    if parsed.decision == "none":
        return await (executor or execute_with_policy)(command, cwd=cwd, ctx=ctx)
    if parsed.decision == "allow_rules":
        await (policy_installer or install_allowed_policy)(parsed.rules, ctx)
        return await (executor or execute_with_policy)(command, cwd=cwd, ctx=ctx)

    return normalize_denial_message("Fusion-Guard analysis failed")


async def run_intent_analysis_in_temp_cli(*, prompt: str, ctx: SessionToolContext) -> str:
    # Temporary CLI/session orchestration lands in the next integration slice.
    return "NONE"


async def install_allowed_policy(rules: list[str], ctx: SessionToolContext) -> None:
    _ = (rules, ctx)


async def execute_with_policy(command: str, *, cwd: str | None, ctx: SessionToolContext) -> str:
    working_dir = cwd or str(ctx.workspace_path or ".")
    try:
        result = await anyio.run_process(["/bin/bash", "-c", command], cwd=working_dir)
    except Exception as e:
        return f"Error executing command: {e}"

    stdout = result.stdout.decode().strip()
    stderr = result.stderr.decode().strip()
    output = stdout
    if stderr:
        output = f"{output}\n[stderr]\n{stderr}" if output else f"[stderr]\n{stderr}"
    return output.strip() or "(no output)"
