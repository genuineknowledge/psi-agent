"""Run a checkpointed real 30-turn multi-domain evaluation."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from datetime import UTC, datetime
from functools import partial
from typing import Any

import aiohttp
import anyio
from loguru import logger

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_scenarios = importlib.import_module("long_run_scenarios")


def selected_turns() -> list[Any]:
    turns = _scenarios.build_turns()
    return [turn for turn in turns if (turn.index - 1) % 25 in {0, 9, 16}]


async def _post(url: str, question: str, user_id: str, profile_id: str) -> dict[str, Any]:
    body = {
        "model": "deepseek-chat",
        "stream": True,
        "user_id": user_id,
        "profile_id": profile_id,
        "messages": [{"role": "user", "content": question}],
    }
    answer: list[str] = []
    errors: list[str] = []
    finish_reasons: list[str] = []
    started = datetime.now(UTC)
    try:
        timeout = aiohttp.ClientTimeout(total=600)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.post(url, json=body) as response:
            async for raw in response.content:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    errors.append(f"invalid_sse:{payload[:120]}")
                    continue
                choices = chunk.get("choices") if isinstance(chunk, dict) else None
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                    continue
                choice = choices[0]
                delta = choice.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    answer.append(delta["content"])
                finish = choice.get("finish_reason")
                if isinstance(finish, str):
                    finish_reasons.append(finish)
                    if finish == "error":
                        errors.append("finish_reason=error")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "answer": "".join(answer),
        "errors": errors,
        "finish_reasons": finish_reasons,
    }


async def run_real(url: str, output_raw: str, user_id: str, profile_id: str) -> None:
    output = anyio.Path(output_raw)
    await output.mkdir(parents=True, exist_ok=True)
    evidence_path = output / "evidence.json"
    evidence: list[dict[str, Any]] = []
    if await evidence_path.exists():
        loaded = json.loads(await evidence_path.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            evidence = [item for item in loaded if isinstance(item, dict)]
    turns = selected_turns()
    registration = _scenarios.registration_profile()
    completed = {item.get("evaluation_turn") for item in evidence}
    for evaluation_turn, turn in enumerate(turns, 1):
        if evaluation_turn in completed:
            continue
        question = turn.question
        if evaluation_turn == 1:
            question = (
                "Registration context (weak prior; current conversation overrides it): "
                + json.dumps(registration, ensure_ascii=False)
                + "\n\n"
                + question
            )
        result = await _post(url, question, user_id, profile_id)
        result.update(
            {
                "evaluation_turn": evaluation_turn,
                "scenario_turn": turn.index,
                "domain": turn.domain,
                "depth": turn.depth,
                "intent": turn.intent,
                "question": question,
            }
        )
        evidence.append(result)
        await evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            f"real_turn={evaluation_turn}/30 domain={turn.domain} "
            f"chars={len(result['answer'])} errors={result['errors']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--profile-id", required=True)
    args = parser.parse_args()
    anyio.run(partial(run_real, args.url, args.output, args.user_id, args.profile_id))


if __name__ == "__main__":
    main()
