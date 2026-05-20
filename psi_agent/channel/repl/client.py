from __future__ import annotations

import json
import sys

from aiohttp import ClientSession, UnixConnector
from loguru import logger
from prompt_toolkit.shortcuts import PromptSession


async def run_repl(session_socket: str) -> None:
    logger.info(f"Connecting to session at {session_socket}")

    connector = UnixConnector(path=session_socket)
    prompt_session = PromptSession()

    try:
        async with ClientSession(connector=connector) as session:
            logger.info("Connected to session. Type your message (Ctrl+D to exit).")
            print("Connected to psi-agent session. Type your message and press Enter.\n")

            while True:
                try:
                    user_input = await prompt_session.prompt_async("> ")
                except EOFError, KeyboardInterrupt:
                    print("\nGoodbye!")
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
                            print(f"\nError: {error.get('error', {}).get('message', body)}")
                        except Exception:
                            print(f"\nError: {body}")
                        continue

                    print()

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
                                print(f"\033[90m[思考] {reasoning}\033[0m", end="", flush=True)

                            if content:
                                print(content, end="", flush=True)

                    print("\n")

    except Exception as e:
        logger.error(f"REPL error: {e}")
        print(f"Error: {e}")
        sys.exit(1)
