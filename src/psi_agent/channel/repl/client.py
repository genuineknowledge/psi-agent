from __future__ import annotations

import json
import sys

from aiohttp import ClientConnectorError, ClientTimeout
from loguru import logger
from prompt_toolkit.shortcuts import PromptSession
from rich.console import Console
from rich.panel import Panel

from psi_agent.net import make_client_session

console = Console(highlight=False)


async def run_repl(session_socket: str) -> None:
    logger.info(f"Connecting to session at {session_socket}")

    prompt_session = PromptSession(multiline=True)

    try:
        client_session, endpoint = make_client_session(session_socket, timeout=ClientTimeout(total=None))
        async with client_session as session:
            logger.info("Connected to session. Enter for newline, Alt+Enter to send (Ctrl+D to exit).")
            console.print(Panel.fit("psi-agent REPL — Enter newline, Alt+Enter send"))
            console.print("[dim]Ctrl+D to exit[/dim]\n")

            while True:
                try:
                    user_input = await prompt_session.prompt_async("> ", prompt_continuation=". ")
                except (EOFError, KeyboardInterrupt):
                    console.print("\nGoodbye!")
                    break

                if not user_input.strip():
                    continue

                req_data = {
                    "model": "psi-agent",
                    "messages": [{"role": "user", "content": user_input}],
                    "stream": True,
                }

                async with session.post(endpoint, json=req_data) as resp:
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

                            if reasoning:
                                logger.debug(f"Reasoning: {reasoning}")

                            if content:
                                console.print(content, end="")

                    console.print("\n")

    except ConnectionRefusedError:
        console.print("[red]Cannot connect to session. Is the session running?[/red]")
        sys.exit(1)
    except ClientConnectorError as e:
        console.print(f"[red]Connection error: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected REPL error")
        console.print(f"[red]Unexpected error: {e}[/red]")
        sys.exit(1)
