"""Run deterministic profile, map, heatmap, and recovery stability simulations."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
from functools import partial
from itertools import pairwise
from time import perf_counter
from typing import Any

import anyio
from loguru import logger

ROOT = os.path.dirname(os.path.abspath(__file__))
for relative in ("", "tools", "systems"):
    path = os.path.join(ROOT, relative)
    if path not in sys.path:
        sys.path.insert(0, path)

_profile = importlib.import_module("_user_profile")
_scenarios = importlib.import_module("long_run_scenarios")
_store = importlib.import_module("supervisor_store")


def _node_id(domain: str) -> str:
    return f"{domain}-core"


async def run_stability(output_raw: str, *, limit: int = 250) -> dict[str, Any]:
    output = anyio.Path(output_raw)
    await output.mkdir(parents=True, exist_ok=True)
    turns = _scenarios.build_turns()[:limit]
    registration = _scenarios.registration_profile()
    user_id = str(registration["user_id"])
    user_hash = hashlib.sha256(user_id.encode()).hexdigest()
    profile = _profile.UserProfile(output, profile_id=str(registration["profile_id"]))
    await profile.load()
    store = _store.SupervisorStore(output)
    evidence: list[dict[str, Any]] = []
    failures: list[str] = []
    seen_events: set[str] = set()
    degraded = False
    recovery_turns: list[int] = []
    latencies: list[float] = []

    for turn in turns:
        started = perf_counter()
        duplicate_update = turn.event_id in seen_events
        if not duplicate_update:
            seen_events.add(turn.event_id)
            await profile.record_turn(turn.question, "deterministic answer")

        faulted = turn.fault != "none"
        if faulted:
            degraded = True
            supervisor_state = "degraded"
            advice_source = "unavailable"
        else:
            if degraded:
                recovery_turns.append(turn.index)
                degraded = False
            supervisor_state = "ready"
            advice_source = "repaired" if turn.index % 7 == 0 else "live"

            domain_map = await store.load_map(turn.domain)
            incoming = {
                "domain_id": turn.domain,
                "nodes": [
                    {
                        "id": _node_id(turn.domain),
                        "label": turn.topic,
                        "aliases": [turn.topic.lower()],
                    }
                ],
                "edges": [],
            }
            await store.save_map(turn.domain, _store.merge_map(domain_map, incoming))
            heatmap = await store.load_heatmap(user_hash, turn.domain)
            heatmap = _store.update_heatmap(
                heatmap,
                node_ids=[_node_id(turn.domain)],
                cognitive_level={"simple": "0.2", "balanced": "0.5", "deep": "0.8"}[turn.depth],
                intent=turn.intent,
                surface=turn.depth != "deep",
                branch_id=f"{turn.domain}/{_node_id(turn.domain)}",
                requested_depth=turn.depth,
            )
            await store.save_heatmap(user_hash, turn.domain, heatmap)

        elapsed_ms = (perf_counter() - started) * 1000
        latencies.append(elapsed_ms)
        evidence.append(
            {
                "turn": turn.index,
                "event_id": turn.event_id,
                "domain": turn.domain,
                "depth": turn.depth,
                "intent": turn.intent,
                "fault": turn.fault,
                "supervisor_state": supervisor_state,
                "advice_source": advice_source,
                "duplicate_update": duplicate_update,
                "elapsed_ms": round(elapsed_ms, 3),
            }
        )

    await profile.save()
    real_topics = [topic for key, topic in profile.topics.items() if key != _profile.GLOBAL_TOPIC_KEY]
    profile_turns = sum(topic["turns"] for topic in real_topics)
    heatmap_events = 0
    parse_failures = 0
    domains = {turn.domain for turn in turns if turn.fault == "none"}
    for domain in domains:
        if await store.load_map(domain) is None:
            parse_failures += 1
            failures.append(f"missing_map:{domain}")
        heatmap = await store.load_heatmap(user_hash, domain)
        history = heatmap.get("history")
        if isinstance(history, list):
            heatmap_events += len(history)
        else:
            parse_failures += 1
            failures.append(f"invalid_heatmap:{domain}")

    expected_events = sum(1 for turn in turns if turn.fault == "none")
    if profile_turns != len(seen_events):
        failures.append(f"profile_turns:{profile_turns}!={len(seen_events)}")
    if heatmap_events != expected_events:
        failures.append(f"heatmap_events:{heatmap_events}!={expected_events}")
    if recovery_turns and any(current - previous > 2 for previous, current in pairwise(recovery_turns)):
        failures.append("recovery_gap")

    summary: dict[str, Any] = {
        "processed_turns": len(turns),
        "profile_turns": profile_turns,
        "domains": len(domains),
        "heatmap_events": heatmap_events,
        "faults": sum(turn.fault != "none" for turn in turns),
        "recoveries": len(recovery_turns),
        "parse_failures": parse_failures,
        "invariant_failures": failures,
        "median_elapsed_ms": sorted(latencies)[len(latencies) // 2] if latencies else 0,
        "max_elapsed_ms": max(latencies, default=0),
        "registration": registration,
    }
    await (output / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    await (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Deterministic stability run completed: {summary}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=250)
    args = parser.parse_args()
    anyio.run(partial(run_stability, args.output, limit=args.limit))


if __name__ == "__main__":
    main()
