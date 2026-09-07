"""Versioned, deterministic positive-negative rule packs."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

VALID_DIRECTIONS = frozenset({"positive", "negative", "red_line"})
DEFAULT_VERSION = "6.0-shadow"
# Rules are part of the positive-negative-list skill package, not deployment
# configuration.  Keeping them beside the skill avoids requiring a second
# process configuration file just to classify a private-chat report.
_CONFIG_DIR = Path(__file__).resolve().parents[2] / "skills" / "positive-negative-list"


@dataclass(frozen=True)
class RuleEntry:
    id: str
    direction: str
    category: str
    title: str
    text: str
    mirror_rule_id: str | None
    conditions: tuple[str, ...]
    exceptions: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    source_locator: str
    keywords: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    red_line_id: str | None = None
    trigger_conditions: tuple[str, ...] = ()
    escalation_conditions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "direction": self.direction,
            "category": self.category,
            "title": self.title,
            "text": self.text,
            "mirror_rule_id": self.mirror_rule_id,
            "conditions": list(self.conditions),
            "exceptions": list(self.exceptions),
            "evidence_requirements": list(self.evidence_requirements),
            "source_locator": self.source_locator,
        }
        if self.red_line_id:
            result["red_line_id"] = self.red_line_id
            result["trigger_conditions"] = list(self.trigger_conditions)
            result["escalation_conditions"] = list(self.escalation_conditions)
        return result


@dataclass(frozen=True)
class RulePack:
    version: str
    entries: tuple[RuleEntry, ...]
    status: str = "shadow"
    source: str = ""

    def query(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip() or not isinstance(limit, int) or limit <= 0:
            return []
        normalized = _normalize(query)
        cjk = re.findall(r"[\u4e00-\u9fff]", normalized)
        terms = tuple(
            dict.fromkeys((normalized, *normalized.split(), *("".join(cjk[i : i + 2]) for i in range(len(cjk) - 1))))
        )
        scored: list[tuple[int, int, RuleEntry]] = []
        for index, entry in enumerate(self.entries):
            haystack = _normalize(
                " ".join((entry.id, entry.title, entry.text, entry.category, *entry.keywords, *entry.aliases))
            )
            score = sum(2 if term and term in _normalize(entry.title) else 1 for term in terms if term in haystack)
            if score:
                scored.append((score, index, entry))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [entry.as_dict() for _, _, entry in scored[:limit]]


def load_rule_pack(version: str = DEFAULT_VERSION) -> RulePack:
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+(?:-[a-z0-9-]+)?", version):
        raise ValueError("invalid rule pack version")
    path = _CONFIG_DIR / f"{version}.yaml"
    if not path.is_file():
        raise ValueError(f"unknown rule pack version: {version}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != version:
        raise ValueError("rule pack version metadata mismatch")
    entries: list[RuleEntry] = []
    for item in raw.get("entries", []):
        if not isinstance(item, dict):
            raise ValueError("rule entry must be an object")
        entries.append(_entry_from_mapping(item))
    pack = RulePack(
        version=version, entries=tuple(entries), status=str(raw.get("status", "")), source=str(raw.get("source", ""))
    )
    validate_rule_pack(pack)
    return pack


def query_rules(query: str, version: str = DEFAULT_VERSION, limit: int = 8) -> list[dict[str, Any]]:
    return load_rule_pack(version).query(query, limit)


def validate_rule_pack(pack: RulePack) -> None:
    if not isinstance(pack, RulePack) or not pack.version:
        raise ValueError("invalid rule pack")
    if any(entry.direction not in VALID_DIRECTIONS for entry in pack.entries):
        raise ValueError("direction must be positive, negative, or red_line")
    ids = [entry.id for entry in pack.entries]
    if len(ids) != len(set(ids)):
        raise ValueError("rule IDs must be unique")
    by_id = {entry.id: entry for entry in pack.entries}
    for entry in pack.entries:
        if not all((getattr(entry, field) or ()) for field in ("conditions", "exceptions", "evidence_requirements")):
            raise ValueError(f"rule fields missing: {entry.id}")
        if entry.direction == "red_line":
            if (
                entry.mirror_rule_id
                or not entry.red_line_id
                or not entry.trigger_conditions
                or not entry.escalation_conditions
            ):
                raise ValueError(f"red line entry invalid: {entry.id}")
        else:
            if entry.red_line_id or entry.trigger_conditions or entry.escalation_conditions:
                raise ValueError(f"ordinary entry carries red-line metadata: {entry.id}")
            mirror = by_id.get(entry.mirror_rule_id or "")
            if mirror is None or mirror.mirror_rule_id != entry.id or mirror.direction == entry.direction:
                raise ValueError(f"mirror invalid: {entry.id}")


def _entry_from_mapping(item: dict[str, Any]) -> RuleEntry:
    def seq(name: str) -> tuple[str, ...]:
        value = item.get(name, ())
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise ValueError(f"{name} must be a non-empty string list")
        return tuple(value)

    required = ("id", "direction", "category", "title", "text", "source_locator")
    if any(not isinstance(item.get(name), str) or not item[name] for name in required):
        raise ValueError("rule entry required fields missing")
    return RuleEntry(
        id=item["id"],
        direction=item["direction"],
        category=item["category"],
        title=item["title"],
        text=item["text"],
        mirror_rule_id=item.get("mirror_rule_id"),
        conditions=seq("conditions"),
        exceptions=seq("exceptions"),
        evidence_requirements=seq("evidence_requirements"),
        source_locator=item["source_locator"],
        keywords=seq("keywords") if item.get("keywords") else (),
        aliases=seq("aliases") if item.get("aliases") else (),
        red_line_id=item.get("red_line_id"),
        trigger_conditions=seq("trigger_conditions") if item.get("trigger_conditions") else (),
        escalation_conditions=seq("escalation_conditions") if item.get("escalation_conditions") else (),
    )


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
