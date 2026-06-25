from __future__ import annotations

import json
import sys

from aiohttp import ClientConnectorError, ClientSession, ClientTimeout
from loguru import logger
from prompt_toolkit.shortcuts import PromptSession
from rich.console import Console
from rich.panel import Panel

from psi_agent._socket import resolve_connector_and_endpoint

console = Console(highlight=False)


async def run_repl(session_socket: str) -> None:
    logger.info(f"Connecting to session at {session_socket}")

    connector, endpoint = resolve_connector_and_endpoint(session_socket)
    prompt_session = PromptSession(multiline=True)

    try:
        async with ClientSession(connector=connector, timeout=ClientTimeout(total=None)) as session:
            logger.info("Connected to session. Enter for newline, Alt+Enter to send (Ctrl+D to exit).")
            console.print(Panel.fit("psi-agent REPL — Enter newline, Alt+Enter send"))
            console.print("[dim]Ctrl+D to exit[/dim]\n")

            while True:
                try:
                    user_input = await prompt_session.prompt_async("> ", prompt_continuation=". ")
                except EOFError, KeyboardInterrupt:
                    console.print("\nGoodbye!")
                    break

                if not user_input.strip():
                    continue

                req_data = {
                    "messages": [{"role": "user", "content": user_input}],
                    "stream": True,
                }

                async with session.post(
                    endpoint,
                    json=req_data,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        try:
                            error = json.loads(body)
                            console.print(f"\n[red]Error: {error.get('error', {}).get('message', body)}[/red]")
                        except Exception:
                            console.print(f"\n[red]Error: {body}[/red]")
                        continue

                    console.print()

                    async for raw_line in resp.content:
                        line = raw_line.decode().strip()
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break

                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        for choice in data.get("choices", []):
                            delta = choice.get("delta", {})
                            reasoning = delta.get("reasoning_content")
                            content = delta.get("content")

                            if choice.get("finish_reason") == "error":
                                console.print(f"\n[red]Error: {content}[/red]")
                                continue

                            if reasoning:
                                logger.debug(f"Reasoning: {reasoning}")
                                console.print(reasoning, style="dim", end="")

                            if content:
                                console.print(content, end="")

                    console.print("\n")

    except ClientConnectorError as e:
        console.print(f"[red]Connection error: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected REPL error")
        console.print(f"[red]Unexpected error: {e}[/red]")
        sys.exit(1)
