from __future__ import annotations

import json
import sys

from aiohttp import ClientSession, UnixConnector
from loguru import logger
from prompt_toolkit.shortcuts import PromptSession
from rich.console import Console
from rich.panel import Panel

console = Console(highlight=False)


async def run_repl(session_socket: str) -> None:
    logger.info(f"Connecting to session at {session_socket}")

    connector = UnixConnector(path=session_socket)
    prompt_session = PromptSession(multiline=True)

    try:
        async with ClientSession(connector=connector) as session:
            logger.info("Connected to session. Enter for newline, Alt+Enter to send (Ctrl+D to exit).")
            console.print(Panel("psi-agent REPL — Enter for newline, Alt+Enter to send", subtitle="Ctrl+D to exit"))

            while True:
                try:
                    user_input = await prompt_session.prompt_async("> ")
                except EOFError, KeyboardInterrupt:
                    console.print("\nGoodbye!")
                    break

                if not user_input.strip():
                    continue

                req_data = {
                    "model": "psi-agent",
                    "messages": [{"role": "user", "content": user_input}],
                    "stream": True,
                }

                async with session.post(
                    "http://localhost/v1/chat/completions",
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

                            if reasoning:
                                logger.debug(f"Reasoning: {reasoning}")
                                console.print(reasoning, style="dim", end="")

                            if content:
                                console.print(content, end="")

                    console.print("\n")

    except Exception as e:
        logger.error(f"REPL error: {e}")
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
