from __future__ import annotations

import json
import unicodedata
from typing import Any

BREAKOUT_TYPES = {
    "none",
    "broaden",
    "deepen",
    "reframe",
    "cross_domain",
    "operationalize",
}
ADVICE_SOURCES = {"live", "repaired", "stale", "unavailable"}

_ANSWER_DEPTHS = {"concise", "balanced", "deep"}
_ANSWER_SCOPES = {"local", "framework", "cross_domain"}
_GOAL_MODES = {"explain", "compare", "decide", "execute", "plan"}
_TERMINOLOGY = {"explain_all", "explain_key_terms", "professional"}
_BREAKOUT_INTEGRATIONS = {
    "none",
    "light_footer",
    "integrated_section",
    "restructure_answer",
}


def empty_advice(*, source: str = "unavailable") -> dict[str, Any]:
    if source not in ADVICE_SOURCES:
        source = "unavailable"
    return {
        "schema_version": "1.0",
        "advice_id": "",
        "user_id_hash": "",
        "profile_id": "",
        "turn_index": 0,
        "classification": {
            "is_learning": False,
            "domain": "",
            "topic": "",
            "confidence": 0.0,
        },
        "user_state": {
            "depth": 0.0,
            "goal": 0.0,
            "familiarity": 0.0,
            "evidence": [],
        },
        "breakout": {
            "needed": False,
            "type": "none",
            "score": 0.0,
            "reason": "",
            "directions": [],
            "evidence": [],
        },
        "latent_need": {
            "detected": False,
            "need": "",
            "missing_dimensions": [],
            "confidence": 0.0,
        },
        "profile_shift": {
            "detected": False,
            "from": "",
            "to": "",
            "evidence": [],
            "confidence": 0.0,
        },
        "response_strategy": {
            "answer_depth": "balanced",
            "answer_scope": "local",
            "goal_mode": "explain",
            "terminology": "explain_key_terms",
            "breakout_integration": "none",
            "instructions": [],
        },
        "map_updates": {
            "proposed_map": None,
            "visited_nodes": [],
            "branch_additions": [],
        },
        "diagnostics": {"source": source, "evidence": []},
    }


def extract_json_object(text: str) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _bounded_text(value: object, *, limit: int = 500) -> str:
    if not isinstance(value, str):
        return ""
    flattened = "".join(
        " " if character.isspace() or unicodedata.category(character).startswith("C") else character
        for character in value
    )
    return " ".join(flattened.split())[:limit]


def _bounded_strings(value: object, *, maximum: int = 5, limit: int = 240) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _bounded_text(item, limit=limit)
        if text:
            result.append(text)
        if len(result) == maximum:
            break
    return result


def _score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return min(1.0, max(0.0, float(value)))


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    return value if isinstance(value, dict) else {}


def _enum(value: object, allowed: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _map_node(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    node_id = _bounded_text(value.get("id"), limit=120)
    label = _bounded_text(value.get("label"), limit=200)
    if not node_id or not label:
        return None
    return {
        "id": node_id,
        "label": label,
        "importance": _score(value.get("importance")),
        "cognitive_level": _bounded_text(value.get("cognitive_level"), limit=80),
    }


def _map_edge(value: object, node_ids: set[str]) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    source = _bounded_text(value.get("source"), limit=120)
    target = _bounded_text(value.get("target"), limit=120)
    edge_type = _bounded_text(value.get("type"), limit=80)
    if not source or not target or not edge_type or source not in node_ids or target not in node_ids:
        return None
    return {"source": source, "target": target, "type": edge_type}


def _proposed_map(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    domain_id = _bounded_text(value.get("domain_id"), limit=120)
    label = _bounded_text(value.get("label"), limit=200)
    if not domain_id or not label:
        return None
    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    raw_nodes = value.get("nodes")
    if isinstance(raw_nodes, list):
        for item in raw_nodes[:50]:
            node = _map_node(item)
            if node is not None and node["id"] not in node_ids:
                nodes.append(node)
                node_ids.add(node["id"])
    edges: list[dict[str, str]] = []
    raw_edges = value.get("edges")
    if isinstance(raw_edges, list):
        for item in raw_edges[:100]:
            edge = _map_edge(item, node_ids)
            if edge is not None:
                edges.append(edge)
    return {
        "domain_id": domain_id,
        "label": label,
        "aliases": _bounded_strings(value.get("aliases"), maximum=20, limit=120),
        "scope": _bounded_text(value.get("scope"), limit=240),
        "confidence": _score(value.get("confidence")),
        "nodes": nodes,
        "edges": edges,
    }


def _branch_additions(value: object, proposed_node_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    additions: list[dict[str, Any]] = []
    for item in value[:10]:
        if not isinstance(item, dict):
            continue
        parent_id = _bounded_text(item.get("parent_id"), limit=120)
        if not parent_id or (proposed_node_ids and parent_id not in proposed_node_ids):
            continue
        nodes: list[dict[str, Any]] = []
        node_ids = {parent_id}
        raw_nodes = item.get("nodes")
        if isinstance(raw_nodes, list):
            for raw_node in raw_nodes[:20]:
                node = _map_node(raw_node)
                if node is not None and node["id"] not in node_ids:
                    nodes.append(node)
                    node_ids.add(node["id"])
        if not nodes:
            continue
        edges: list[dict[str, str]] = []
        raw_edges = item.get("edges")
        if isinstance(raw_edges, list):
            for raw_edge in raw_edges[:40]:
                edge = _map_edge(raw_edge, node_ids)
                if edge is not None:
                    edges.append(edge)
        additions.append({"parent_id": parent_id, "nodes": nodes, "edges": edges})
    return additions


def validate_advice(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return empty_advice()

    normalized_raw: dict[str, Any] = {key: value for key, value in raw.items() if isinstance(key, str)}
    discarded_non_string_key = len(normalized_raw) != len(raw)
    raw = normalized_raw

    diagnostics = _section(raw, "diagnostics")
    requested_source = diagnostics.get("source")
    source = requested_source if requested_source in ADVICE_SOURCES else "repaired"
    advice = empty_advice(source=source)

    for key, limit in (
        ("schema_version", 20),
        ("advice_id", 128),
        ("user_id_hash", 128),
        ("profile_id", 128),
    ):
        value = _bounded_text(raw.get(key), limit=limit)
        if value:
            advice[key] = value
    turn_index = raw.get("turn_index")
    if isinstance(turn_index, int) and not isinstance(turn_index, bool):
        advice["turn_index"] = max(0, turn_index)

    classification = _section(raw, "classification")
    advice["classification"] = {
        "is_learning": classification.get("is_learning") is True,
        "domain": _bounded_text(classification.get("domain"), limit=120),
        "topic": _bounded_text(classification.get("topic"), limit=160),
        "confidence": _score(classification.get("confidence")),
    }

    user_state = _section(raw, "user_state")
    advice["user_state"] = {
        "depth": _score(user_state.get("depth")),
        "goal": _score(user_state.get("goal")),
        "familiarity": _score(user_state.get("familiarity")),
        "evidence": _bounded_strings(user_state.get("evidence")),
    }

    breakout = _section(raw, "breakout")
    breakout_type = _enum(breakout.get("type"), BREAKOUT_TYPES, "none")
    directions = _bounded_strings(breakout.get("directions"), maximum=3)
    reason = _bounded_text(breakout.get("reason"))
    needed = breakout.get("needed") is True
    repaired = breakout.get("type") is not None and breakout.get("type") not in BREAKOUT_TYPES
    if not needed or breakout_type == "none" or not reason or not directions:
        repaired = repaired or needed or breakout_type != "none"
        needed = False
        breakout_type = "none"
    advice["breakout"] = {
        "needed": needed,
        "type": breakout_type,
        "score": _score(breakout.get("score")),
        "reason": reason,
        "directions": directions,
        "evidence": _bounded_strings(breakout.get("evidence")),
    }

    latent_need = _section(raw, "latent_need")
    latent_text = _bounded_text(latent_need.get("need"))
    latent_detected = latent_need.get("detected") is True and bool(latent_text)
    advice["latent_need"] = {
        "detected": latent_detected,
        "need": latent_text if latent_detected else "",
        "missing_dimensions": _bounded_strings(latent_need.get("missing_dimensions"), maximum=5),
        "confidence": _score(latent_need.get("confidence")),
    }

    profile_shift = _section(raw, "profile_shift")
    shift_from = _bounded_text(profile_shift.get("from"), limit=160)
    shift_to = _bounded_text(profile_shift.get("to"), limit=160)
    shift_detected = (
        profile_shift.get("detected") is True and bool(shift_from) and bool(shift_to) and shift_from != shift_to
    )
    advice["profile_shift"] = {
        "detected": shift_detected,
        "from": shift_from if shift_detected else "",
        "to": shift_to if shift_detected else "",
        "evidence": _bounded_strings(profile_shift.get("evidence")),
        "confidence": _score(profile_shift.get("confidence")),
    }

    strategy = _section(raw, "response_strategy")
    repaired = repaired or any(
        value is not None and value not in allowed
        for value, allowed in (
            (strategy.get("answer_depth"), _ANSWER_DEPTHS),
            (strategy.get("answer_scope"), _ANSWER_SCOPES),
            (strategy.get("goal_mode"), _GOAL_MODES),
            (strategy.get("terminology"), _TERMINOLOGY),
            (strategy.get("breakout_integration"), _BREAKOUT_INTEGRATIONS),
        )
    )
    advice["response_strategy"] = {
        "answer_depth": _enum(strategy.get("answer_depth"), _ANSWER_DEPTHS, "balanced"),
        "answer_scope": _enum(strategy.get("answer_scope"), _ANSWER_SCOPES, "local"),
        "goal_mode": _enum(strategy.get("goal_mode"), _GOAL_MODES, "explain"),
        "terminology": _enum(strategy.get("terminology"), _TERMINOLOGY, "explain_key_terms"),
        "breakout_integration": _enum(strategy.get("breakout_integration"), _BREAKOUT_INTEGRATIONS, "none"),
        "instructions": [],
    }

    map_updates = _section(raw, "map_updates")
    proposed_map = _proposed_map(map_updates.get("proposed_map"))
    proposed_node_ids = {node["id"] for node in proposed_map["nodes"]} if proposed_map is not None else set()
    advice["map_updates"] = {
        "proposed_map": proposed_map,
        "visited_nodes": _bounded_strings(map_updates.get("visited_nodes"), maximum=20),
        "branch_additions": _branch_additions(map_updates.get("branch_additions"), proposed_node_ids),
    }
    normalized_evidence = _bounded_strings(diagnostics.get("evidence"))
    advice["diagnostics"] = {"source": source, "evidence": normalized_evidence}
    normalized_sections_changed = discarded_non_string_key or any(
        raw.get(key) != advice[key] for key in advice if key != "diagnostics"
    )
    diagnostics_changed = (
        not isinstance(raw.get("diagnostics"), dict) or diagnostics.get("evidence") != normalized_evidence
    )
    if (repaired or normalized_sections_changed or diagnostics_changed) and source == "live":
        source = "repaired"
    advice["diagnostics"]["source"] = source
    return advice


def render_advice_prompt(advice: dict[str, Any]) -> str:
    validated = validate_advice(advice)
    if validated["diagnostics"]["source"] == "unavailable" or not validated["classification"]["is_learning"]:
        return ""

    classification = validated["classification"]
    breakout = validated["breakout"]
    latent_need = validated["latent_need"]
    profile_shift = validated["profile_shift"]
    strategy = validated["response_strategy"]

    def quoted(value: object) -> str:
        return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")

    lines = ["## 旁路监督建议", "[SUPERVISOR-DATA-BEGIN]"]
    if classification["domain"] or classification["topic"]:
        lines.append(f"domain={quoted(classification['domain'])}; topic={quoted(classification['topic'])}")
    if breakout["needed"]:
        lines.append(f"breakout_type={quoted(breakout['type'])}; reason={quoted(breakout['reason'])}")
        lines.append(f"directions={quoted(breakout['directions'])}")
    if latent_need["detected"]:
        line = f"latent_need={quoted(latent_need['need'])}"
        if latent_need["missing_dimensions"]:
            line += f"; missing_dimensions={quoted(latent_need['missing_dimensions'])}"
        lines.append(line)
    if profile_shift["detected"]:
        lines.append(
            f"profile_shift_from={quoted(profile_shift['from'])}; profile_shift_to={quoted(profile_shift['to'])}"
        )
    lines.append(
        f"answer_depth={quoted(strategy['answer_depth'])}; "
        f"answer_scope={quoted(strategy['answer_scope'])}; "
        f"goal_mode={quoted(strategy['goal_mode'])}; "
        f"terminology={quoted(strategy['terminology'])}; "
        f"breakout_integration={quoted(strategy['breakout_integration'])}"
    )
    lines.append("[SUPERVISOR-DATA-END]")
    lines.extend(
        (
            "- 先回答用户当前问题。",
            "- 不要向用户提及副 Agent、监督评分或画像判断。",
            "- 不要强迫用户转换话题。",
        )
    )
    return "\n".join(lines)
