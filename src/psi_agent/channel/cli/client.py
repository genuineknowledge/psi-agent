from __future__ import annotations

import json
import sys

from aiohttp import ClientSession, ClientTimeout
from loguru import logger
from rich.console import Console

from psi_agent._socket import resolve_connector_and_endpoint

console = Console(highlight=False)


async def run_cli(*, session_socket: str, message: str) -> None:
    logger.info(f"Connecting to session at {session_socket}")

    connector, endpoint = resolve_connector_and_endpoint(session_socket)

    try:
        async with ClientSession(connector=connector, timeout=ClientTimeout(total=None)) as session:
            req_data = {
                "messages": [{"role": "user", "content": message}],
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
                        console.print(f"[red]Error: {error.get('error', {}).get('message', body)}[/red]")
                    except Exception:
                        console.print(f"[red]Error: {body}[/red]")
                    sys.exit(1)

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

                console.print()

    except Exception as e:
        logger.error(f"CLI error: {e}")
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
