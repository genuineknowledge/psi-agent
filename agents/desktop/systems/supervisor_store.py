from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import anyio
import yaml
from anyio import to_thread
from loguru import logger

_DOMAIN_UNSAFE = re.compile(r"[^a-z0-9]+")
_USER_HASH = re.compile(r"[0-9a-f]{64}")
_HISTORY_LIMIT = 20
_LOCK_GUARD = anyio.Lock()
_USER_LOCKS: dict[str, anyio.Lock] = {}
_DOMAIN_LOCKS: dict[str, anyio.Lock] = {}


def _node_terms(node: dict[str, Any]) -> set[str]:
    values = [node.get("id"), node.get("label")]
    aliases = node.get("aliases")
    if isinstance(aliases, list):
        values.extend(aliases)
    return {
        _DOMAIN_UNSAFE.sub("-", value.lower()).strip("-")
        for value in values
        if isinstance(value, str) and value.strip()
    }


def merge_map(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge map nodes conservatively using identifiers, labels, and aliases."""
    merged = dict(existing) if isinstance(existing, dict) else {}
    revision = merged.get("map_revision", 0)
    merged["map_revision"] = (revision if isinstance(revision, int) else 0) + 1
    merged["schema_version"] = "2.0"
    merged["domain_id"] = incoming.get("domain_id", merged.get("domain_id", "general"))
    timestamp = datetime.now(UTC).isoformat()
    merged.setdefault("first_seen", timestamp)
    merged["last_seen"] = timestamp
    raw_nodes = merged.get("nodes")
    nodes = [dict(node) for node in raw_nodes if isinstance(node, dict)] if isinstance(raw_nodes, list) else []
    incoming_nodes = incoming.get("nodes")
    if isinstance(incoming_nodes, list):
        for raw_node in incoming_nodes:
            if not isinstance(raw_node, dict):
                continue
            node = dict(raw_node)
            terms = _node_terms(node)
            matched = next((candidate for candidate in nodes if terms & _node_terms(candidate)), None)
            if matched is None:
                node.setdefault("aliases", [])
                node.setdefault("confidence", 0.5)
                node.setdefault("source_count", 1)
                node.setdefault("first_seen", timestamp)
                node["last_seen"] = timestamp
                nodes.append(node)
                continue
            aliases = matched.get("aliases")
            alias_values = [value for value in aliases if isinstance(value, str)] if isinstance(aliases, list) else []
            raw_node_aliases = node.get("aliases")
            node_aliases = raw_node_aliases if isinstance(raw_node_aliases, list) else []
            for value in (node.get("id"), node.get("label"), *node_aliases):
                if isinstance(value, str) and value not in alias_values and value != matched.get("id"):
                    alias_values.append(value)
            matched["aliases"] = alias_values
            matched["source_count"] = int(matched.get("source_count", 1)) + 1
            matched["confidence"] = min(1.0, float(matched.get("confidence", 0.5)) + 0.1)
            matched["last_seen"] = timestamp
    merged["nodes"] = nodes
    raw_edges = merged.get("edges")
    edges = [edge for edge in raw_edges if isinstance(edge, dict)] if isinstance(raw_edges, list) else []
    incoming_edges = incoming.get("edges")
    if isinstance(incoming_edges, list):
        seen = {(edge.get("source"), edge.get("target"), edge.get("type")) for edge in edges}
        for edge in incoming_edges:
            if isinstance(edge, dict):
                key = (edge.get("source"), edge.get("target"), edge.get("type"))
                if key not in seen:
                    edges.append(dict(edge))
                    seen.add(key)
    merged["edges"] = edges
    return merged


class SupervisorStore:
    """Persist shared supervisor maps and isolated per-user state."""

    def __init__(self, workspace: anyio.Path) -> None:
        self.workspace = workspace
        self.root = workspace / "wiki" / "supervisor"

    @staticmethod
    def safe_domain(domain_id: str) -> str:
        safe = _DOMAIN_UNSAFE.sub("-", domain_id.lower()).strip("-")
        if not safe:
            raise ValueError("domain must contain ASCII letters or digits")
        return safe

    def map_path(self, domain_id: str) -> anyio.Path:
        return self.root / "maps" / f"{self.safe_domain(domain_id)}.yaml"

    @staticmethod
    def validate_user_hash(user_hash: str) -> str:
        if _USER_HASH.fullmatch(user_hash) is None:
            raise ValueError("user_hash must be exactly 64 lowercase hexadecimal characters")
        return user_hash

    def heatmap_path(self, user_hash: str, domain_id: str) -> anyio.Path:
        safe_user_hash = self.validate_user_hash(user_hash)
        return self.root / "users" / safe_user_hash / "domains" / f"{self.safe_domain(domain_id)}.yaml"

    def latest_advice_path(self, user_hash: str) -> anyio.Path:
        return self.root / "users" / self.validate_user_hash(user_hash) / "latest-advice.json"

    def participation_path(self, user_hash: str) -> anyio.Path:
        return self.root / "users" / self.validate_user_hash(user_hash) / "participation.json"

    def metrics_path(self, user_hash: str) -> anyio.Path:
        return self.root / "users" / self.validate_user_hash(user_hash) / "metrics.jsonl"

    async def load_map(self, domain_id: str) -> dict[str, Any] | None:
        return await self._load_yaml(self.map_path(domain_id))

    async def save_map(self, domain_id: str, data: dict[str, Any]) -> None:
        content = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        await self._atomic_write(self.map_path(domain_id), content)

    async def load_heatmap(self, user_hash: str, domain_id: str) -> dict[str, Any]:
        safe_domain = self.safe_domain(domain_id)
        loaded = await self._load_yaml(self.heatmap_path(user_hash, safe_domain))
        if loaded is not None:
            return loaded
        return {
            "user": user_hash,
            "domain": safe_domain,
            "question_count": 0,
            "visited_nodes": [],
            "nodes": {},
            "repeated_surface_questions": 0,
            "cognitive_history": [],
            "intent_history": [],
            "history": [],
            "active_branches": {},
            "last_seen": "",
        }

    async def save_heatmap(self, user_hash: str, domain_id: str, data: dict[str, Any]) -> None:
        content = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        await self._atomic_write(self.heatmap_path(user_hash, domain_id), content)

    async def load_latest_advice(self, user_hash: str) -> dict[str, Any] | None:
        try:
            content = await self.latest_advice_path(user_hash).read_text(encoding="utf-8")
            loaded = json.loads(content)
        except FileNotFoundError, json.JSONDecodeError, UnicodeError:
            return None
        return loaded if isinstance(loaded, dict) else None

    async def save_latest_advice(self, user_hash: str, data: dict[str, Any]) -> None:
        content = json.dumps(data, ensure_ascii=False, indent=2)
        await self._atomic_write(self.latest_advice_path(user_hash), content)

    async def load_participation(self, user_hash: str) -> dict[str, Any]:
        try:
            content = await self.participation_path(user_hash).read_text(encoding="utf-8")
            loaded = json.loads(content)
        except FileNotFoundError, json.JSONDecodeError, UnicodeError:
            loaded = None
        if isinstance(loaded, dict):
            return loaded
        return {"eligible_turns": 0, "warmup_status": "new", "last_supervised_turn": 0}

    async def save_participation(self, user_hash: str, data: dict[str, Any]) -> None:
        content = json.dumps(data, ensure_ascii=False, indent=2)
        await self._atomic_write(self.participation_path(user_hash), content)

    async def load_metrics(self, user_hash: str) -> list[dict[str, Any]]:
        try:
            content = await self.metrics_path(user_hash).read_text(encoding="utf-8")
        except FileNotFoundError, UnicodeError:
            return []
        metrics: list[dict[str, Any]] = []
        for line in content.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                metrics.append(item)
        return metrics

    async def append_metric(self, user_hash: str, metric: dict[str, Any]) -> None:
        existing = await self.load_metrics(user_hash)
        existing.append(dict(metric))
        content = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in existing)
        await self._atomic_write(self.metrics_path(user_hash), content)

    @asynccontextmanager
    async def user_lock(self, user_hash: str) -> AsyncIterator[None]:
        safe_user_hash = self.validate_user_hash(user_hash)
        lock = await self._get_lock(_USER_LOCKS, f"{self.workspace}:{safe_user_hash}")
        logger.debug(f"Acquiring supervisor user lock: {safe_user_hash}")
        async with lock:
            logger.debug(f"Acquired supervisor user lock: {safe_user_hash}")
            try:
                yield
            finally:
                logger.debug(f"Releasing supervisor user lock: {safe_user_hash}")

    @asynccontextmanager
    async def domain_lock(self, domain_id: str) -> AsyncIterator[None]:
        safe_domain = self.safe_domain(domain_id)
        lock = await self._get_lock(_DOMAIN_LOCKS, f"{self.workspace}:{safe_domain}")
        logger.debug(f"Acquiring supervisor domain lock: {safe_domain}")
        async with lock:
            logger.debug(f"Acquired supervisor domain lock: {safe_domain}")
            try:
                yield
            finally:
                logger.debug(f"Releasing supervisor domain lock: {safe_domain}")

    @staticmethod
    async def _get_lock(locks: dict[str, anyio.Lock], key: str) -> anyio.Lock:
        async with _LOCK_GUARD:
            lock = locks.get(key)
            if lock is None:
                lock = anyio.Lock()
                locks[key] = lock
            return lock

    @staticmethod
    async def _load_yaml(path: anyio.Path) -> dict[str, Any] | None:
        try:
            content = await path.read_text(encoding="utf-8")
            loaded = yaml.safe_load(content)
        except FileNotFoundError, UnicodeError, yaml.YAMLError:
            return None
        return loaded if isinstance(loaded, dict) else None

    @staticmethod
    async def _atomic_write(target: anyio.Path, content: str) -> None:
        await target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".tmp-{uuid4().hex[:12]}")
        try:
            await temporary.write_text(content, encoding="utf-8")
            for attempt in range(3):
                try:
                    await to_thread.run_sync(os.replace, str(temporary), str(target))
                    break
                except PermissionError:
                    if attempt == 2:
                        raise
                    await anyio.sleep(0.02 * (attempt + 1))
        finally:
            with anyio.CancelScope(shield=True):
                with suppress(FileNotFoundError):
                    await temporary.unlink()


def update_heatmap(
    heatmap: dict[str, Any],
    *,
    node_ids: list[str],
    cognitive_level: str,
    intent: str,
    surface: bool,
    branch_id: str = "",
    requested_depth: str = "",
) -> dict[str, Any]:
    """Return a heatmap update while preserving complete historical evidence."""
    updated = dict(heatmap)
    question_count = updated.get("question_count", 0)
    updated["question_count"] = (question_count if isinstance(question_count, int) else 0) + 1

    raw_nodes = updated.get("nodes")
    nodes = dict(raw_nodes) if isinstance(raw_nodes, dict) else {}
    visited: list[str] = []
    for node_id in node_ids:
        if not isinstance(node_id, str) or not node_id:
            continue
        raw_node = nodes.get(node_id)
        node = dict(raw_node) if isinstance(raw_node, dict) else {}
        count = node.get("count", 0)
        count = (count if isinstance(count, int) else 0) + 1
        node["count"] = count
        node["heat"] = min(1.0, count / 5)
        nodes[node_id] = node
        visited.append(node_id)
    updated["nodes"] = nodes

    prior_visited = updated.get("visited_nodes")
    combined = list(prior_visited) if isinstance(prior_visited, list) else []
    updated["visited_nodes"] = (combined + visited)[-_HISTORY_LIMIT:]

    surface_count = updated.get("repeated_surface_questions", 0)
    if not isinstance(surface_count, int):
        surface_count = 0
    updated["repeated_surface_questions"] = surface_count + int(surface)
    updated["cognitive_history"] = _append_history(updated.get("cognitive_history"), cognitive_level)
    updated["intent_history"] = _append_history(updated.get("intent_history"), intent)
    raw_history = updated.get("history")
    history = list(raw_history) if isinstance(raw_history, list) else []
    raw_branches = updated.get("active_branches")
    active_branches = dict(raw_branches) if isinstance(raw_branches, dict) else {}
    timestamp = datetime.now(UTC).isoformat()
    if branch_id:
        previous_branch = active_branches.get(branch_id)
        previous_depth = previous_branch.get("active_depth", "") if isinstance(previous_branch, dict) else ""
        if previous_depth == "deep" and requested_depth == "simple":
            transition = "rollback"
        elif previous_depth == "simple" and requested_depth == "deep":
            transition = "advance"
        else:
            transition = "steady"
        event = {
            "timestamp": timestamp,
            "branch_id": branch_id,
            "requested_depth": requested_depth,
            "cognitive_level": cognitive_level,
            "intent": intent,
            "transition": transition,
            "surface": surface,
        }
        history.append(event)
        branch = dict(previous_branch) if isinstance(previous_branch, dict) else {}
        branch["active_depth"] = requested_depth
        branch["last_seen"] = timestamp
        if transition == "rollback":
            branch["rolled_back_from"] = previous_depth
            branch["rollback_count"] = int(branch.get("rollback_count", 0)) + 1
        active_branches[branch_id] = branch
    updated["history"] = history
    updated["active_branches"] = active_branches
    updated["last_seen"] = timestamp
    return updated


def _append_history(history: Any, value: str) -> list[str]:
    values = [item for item in history if isinstance(item, str)] if isinstance(history, list) else []
    if value:
        values.append(value)
    return values
