"""Per-user background learning supervisor orchestration."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

import anyio
from loguru import logger
from supervisor_protocol import empty_advice, extract_json_object, validate_advice
from supervisor_store import SupervisorStore, merge_map, update_heatmap

PlanFn = Callable[..., Awaitable[dict[str, Any]]]
StartFn = Callable[..., Awaitable[dict[str, Any]]]
StopFn = Callable[..., Awaitable[dict[str, Any]]]
WaitFn = Callable[..., Awaitable[dict[str, Any]]]
ChatFn = Callable[..., Awaitable[dict[str, Any]]]
_TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
_FAST_ADVICE_TTL = timedelta(minutes=10)


def is_cache_eligible(
    advice: dict[str, Any] | None,
    user_message: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """Accept only fresh, same-user/profile/topic Advice cache."""
    if not isinstance(advice, dict):
        return False
    diagnostics = advice.get("diagnostics")
    if not isinstance(diagnostics, dict) or diagnostics.get("source") not in {"live", "repaired", "cache"}:
        return False
    if advice.get("user_id_hash") != hash_identity(resolve_identity(user_message)):
        return False
    profile_id = user_message.get("profile_id")
    if isinstance(profile_id, str) and advice.get("profile_id") != profile_id:
        return False
    created = diagnostics.get("created_at") or advice.get("created_at")
    if not isinstance(created, str):
        return False
    try:
        timestamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return False
    if (now or datetime.now(UTC)) - timestamp > _FAST_ADVICE_TTL:
        return False
    question = user_message.get("content")
    classification = advice.get("classification")
    topic = classification.get("topic") if isinstance(classification, dict) else ""
    if not isinstance(question, str) or not isinstance(topic, str) or not topic:
        return False
    lowered = question.lower()
    if any(signal in lowered for signal in ("不要深入", "简单解释", "换个话题", "broaden", "reframe")):
        return False
    return topic.replace("_", " ").lower() in lowered or topic.lower() in lowered


def hash_identity(value: str) -> str:
    """Return the exact, stable SHA-256 hex digest of an identity."""
    return hashlib.sha256(value.encode()).hexdigest()


def resolve_identity(message: dict[str, Any]) -> str:
    """Resolve local identity in privacy-preserving precedence order."""
    for key in ("user_id", "profile_id", "session_id"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "local"


def is_learning_question(text: str) -> bool:
    """Cheap conservative filter for Chinese and English learning questions."""
    value = text.strip().lower()
    if not value:
        return False
    signals = (
        "什么",
        "为什么",
        "如何",
        "怎么",
        "解释",
        "学习",
        "理解",
        "区别",
        "原理",
        "教程",
        "了解",
        "框架",
        "领域",
        "概念",
        "深入",
        "推导",
        "机制",
        "比较",
        "对比",
        "整理",
        "起草",
        "构思",
        "审查",
        "分析",
        "文献库",
        "sop",
        "what ",
        "why ",
        "how ",
        "explain",
        "learn",
        "understand",
        "difference",
        "tutorial",
        "framework",
        "concept",
        "derive",
        "mechanism",
        "compare",
    )
    return "?" in value or "\N{FULLWIDTH QUESTION MARK}" in value or any(signal in value for signal in signals)


def _load_tool_module(filename: str, name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "tools" / filename
    namespace: dict[str, Any] = {"__file__": str(path), "__name__": name}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    return namespace


@dataclass(frozen=True, slots=True)
class SupervisorHandle:
    user_id_hash: str
    session_id: str
    ai_socket: str
    channel_socket: str
    reuse_parent_ai: bool
    ai_process_id: str
    session_process_id: str


class SupervisorManager:
    def __init__(
        self,
        workspace: anyio.Path,
        *,
        plan_fn: PlanFn | None = None,
        start_fn: StartFn | None = None,
        stop_fn: StopFn | None = None,
        wait_fn: WaitFn | None = None,
        chat_fn: ChatFn | None = None,
    ) -> None:
        self.workspace = workspace
        self.store = SupervisorStore(workspace)
        self._plan_fn = plan_fn
        self._start_fn = start_fn
        self._stop_fn = stop_fn
        self._wait_fn = wait_fn
        self._chat_fn = chat_fn
        self._handles: dict[str, SupervisorHandle] = {}
        self._handle_locks: dict[str, anyio.Lock] = {}
        self._locks_guard = anyio.Lock()

    async def _dependencies(self) -> tuple[PlanFn, StartFn, StopFn, WaitFn, ChatFn]:
        if None in (self._plan_fn, self._start_fn, self._stop_fn, self._wait_fn, self._chat_fn):
            if str(_TOOLS_DIR) not in sys.path:
                sys.path.insert(0, str(_TOOLS_DIR))
            helpers = _load_tool_module("_subagent_helpers.py", "_supervisor_subagent_helpers")
            registry = _load_tool_module("_background_process_registry.py", "_supervisor_background_registry")
            self._plan_fn = self._plan_fn or helpers["plan_subagent"]
            self._start_fn = self._start_fn or registry["start_process"]
            self._stop_fn = self._stop_fn or registry["stop_process"]
            self._wait_fn = self._wait_fn or helpers["wait_socket"]
            self._chat_fn = self._chat_fn or helpers["chat_subagent"]
        assert self._plan_fn and self._start_fn and self._stop_fn and self._wait_fn and self._chat_fn
        return self._plan_fn, self._start_fn, self._stop_fn, self._wait_fn, self._chat_fn

    async def _cleanup_processes(self, process_ids: list[str]) -> None:
        _, _, stop_fn, _, _ = await self._dependencies()
        with anyio.CancelScope(shield=True):
            for process_id in process_ids:
                if not process_id:
                    continue
                try:
                    await stop_fn(process_id=process_id, workspace_raw=str(self.workspace))
                except Exception as exc:
                    logger.warning(f"Supervisor child cleanup failed: {type(exc).__name__}")

    async def _handle_lock(self, user_hash: str) -> anyio.Lock:
        async with self._locks_guard:
            return self._handle_locks.setdefault(user_hash, anyio.Lock())

    async def ensure_supervisor(self, user_hash: str, *, restart: bool = False) -> SupervisorHandle | None:
        plan_fn, start_fn, _, wait_fn, _ = await self._dependencies()
        lock = await self._handle_lock(user_hash)
        async with lock:
            cached = self._handles.get(user_hash)
            if cached is not None and not restart:
                probe = await wait_fn(cached.channel_socket, timeout_seconds=0.5)
                if probe.get("ok") is True:
                    return cached
            if cached is not None:
                cleanup = [cached.session_process_id]
                if not cached.reuse_parent_ai and cached.ai_process_id:
                    cleanup.append(cached.ai_process_id)
                await self._cleanup_processes(cleanup)
            self._handles.pop(user_hash, None)
            session_id = f"supervisor-{user_hash[:16]}"
            child_workspace = anyio.Path(__file__).parent.parent.parent / "haitun-supervisor-workspace"
            plan = await plan_fn(
                session_id=session_id,
                workspace_raw=str(self.workspace),
                child_workspace_raw=str(child_workspace),
            )
            logger.info(
                "Supervisor plan completed: "
                f"ok={plan.get('ok') is True} "
                f"reuse_parent_ai={plan.get('reuse_parent_ai') is True} "
                f"binding_source={str(plan.get('binding_source', ''))!r}"
            )
            if plan.get("ok") is not True:
                logger.warning("Supervisor planning failed")
                return None
            shell = str(plan.get("shell", "auto"))
            owned_process_ids: list[str] = []
            try:
                if plan.get("reuse_parent_ai") is not True:
                    ai_process_id = str(plan.get("ai_process_id", ""))
                    with anyio.CancelScope(shield=True):
                        started = await start_fn(
                            command=str(plan.get("ai_command", "")),
                            workspace_raw=str(self.workspace),
                            process_id=ai_process_id,
                            shell=shell,
                        )
                        if started.get("ok") is True:
                            owned_process_ids.append(ai_process_id)
                    logger.info(f"Supervisor child AI start completed: ok={started.get('ok') is True}")
                    if started.get("ok") is not True:
                        logger.warning("Supervisor child AI failed to start")
                        return None
                if not (await wait_fn(str(plan.get("ai_socket", "")))).get("ok"):
                    logger.warning("Supervisor child AI readiness check failed")
                    return None
                session_process_id = str(plan.get("session_process_id", ""))
                with anyio.CancelScope(shield=True):
                    started = await start_fn(
                        command=str(plan.get("session_command", "")),
                        workspace_raw=str(self.workspace),
                        process_id=session_process_id,
                        shell=shell,
                    )
                    if started.get("ok") is True:
                        owned_process_ids.append(session_process_id)
                logger.info(f"Supervisor child Session start completed: ok={started.get('ok') is True}")
                if started.get("ok") is not True:
                    logger.warning("Supervisor child Session failed to start")
                    return None
                channel_socket = str(plan.get("channel_socket", ""))
                if not (await wait_fn(channel_socket)).get("ok"):
                    logger.warning("Supervisor child Session readiness check failed")
                    return None
                handle = SupervisorHandle(
                    user_id_hash=user_hash,
                    session_id=session_id,
                    ai_socket=str(plan.get("ai_socket", "")),
                    channel_socket=channel_socket,
                    reuse_parent_ai=plan.get("reuse_parent_ai") is True,
                    ai_process_id=str(plan.get("ai_process_id", "")),
                    session_process_id=session_process_id,
                )
                self._handles[user_hash] = handle
                owned_process_ids.clear()
                logger.info(
                    "Supervisor handle ready: "
                    f"reuse_parent_ai={handle.reuse_parent_ai} binding_source={str(plan.get('binding_source', ''))!r}"
                )
                return handle
            finally:
                if owned_process_ids:
                    with anyio.CancelScope(shield=True):
                        await self._cleanup_processes(list(reversed(owned_process_ids)))

    @staticmethod
    def _profile(raw: object) -> dict[str, float]:
        source = raw if isinstance(raw, dict) else {}
        result: dict[str, float] = {}
        for key in ("depth", "goal", "familiarity"):
            value = source.get(key)
            score = float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0
            result[key] = min(1.0, max(0.0, score))
        return result

    @staticmethod
    def _map_summary(domain_map: dict[str, Any] | None) -> dict[str, Any] | None:
        if domain_map is None:
            return None
        nodes = domain_map.get("nodes")
        edges = domain_map.get("edges")
        return {
            "domain_id": domain_map.get("domain_id", ""),
            "label": domain_map.get("label", ""),
            "nodes": nodes[:50] if isinstance(nodes, list) else [],
            "edges": edges[:100] if isinstance(edges, list) else [],
        }

    @staticmethod
    def _heatmap_summary(heatmap: dict[str, Any]) -> dict[str, Any]:
        return {
            key: heatmap.get(key)
            for key in (
                "question_count",
                "visited_nodes",
                "repeated_surface_questions",
                "cognitive_history",
                "intent_history",
            )
        }

    async def supervise(self, user_message: dict[str, Any]) -> dict[str, Any] | None:
        question = user_message.get("content")
        session_id = user_message.get("session_id")
        kind = user_message.get("kind")
        if not isinstance(question, str) or not is_learning_question(question):
            return None
        if isinstance(kind, str) and kind.startswith("schedule"):
            return None
        if isinstance(session_id, str) and session_id.startswith("supervisor-"):
            return None
        identity = resolve_identity(user_message)
        user_hash = hash_identity(identity)
        profile_id = user_message.get("profile_id") if isinstance(user_message.get("profile_id"), str) else ""
        session_hash = hash_identity(session_id) if isinstance(session_id, str) else hash_identity("")
        async with self.store.user_lock(user_hash):
            previous = await self.store.load_latest_advice(user_hash)
            if isinstance(previous, dict) and is_cache_eligible(previous, user_message):
                cached = dict(previous)
                diagnostics = dict(cached.get("diagnostics", {}))
                diagnostics["source"] = "cache"
                diagnostics["cache_reason"] = "same_user_profile_topic_fresh"
                cached["diagnostics"] = diagnostics
                logger.debug("Supervisor cache hit: source=cache")
                return cached
            prior_domain = "general"
            if previous is not None:
                classification = previous.get("classification")
                if isinstance(classification, dict) and isinstance(classification.get("domain"), str):
                    candidate = classification["domain"]
                    if re.fullmatch(r"[a-z0-9-]+", candidate):
                        prior_domain = candidate
            domain_map = await self.store.load_map(prior_domain)
            heatmap = await self.store.load_heatmap(user_hash, prior_domain)
            handle = await self.ensure_supervisor(user_hash)
            if handle is None:
                logger.warning("Supervisor unavailable: no healthy child handle")
                return empty_advice()
            payload = {
                "event": "supervise_learning_turn",
                "user_id_hash": user_hash,
                "profile_id": profile_id,
                "session_id_hash": session_hash,
                "turn_index": max(0, user_message.get("turn_index", 0))
                if isinstance(user_message.get("turn_index"), int)
                else 0,
                "user_question": question,
                "stage_profile": self._profile(user_message.get("stage_profile")),
                "existing_map": self._map_summary(domain_map),
                "heatmap": self._heatmap_summary(heatmap),
                "previous_supervision": validate_advice(previous) if previous is not None else None,
            }
            _, _, _, _, chat_fn = await self._dependencies()
            advice: dict[str, Any] | None = None
            for attempt in range(2):
                try:
                    result = await chat_fn(
                        channel_socket=handle.channel_socket,
                        message=json.dumps(payload, ensure_ascii=False),
                    )
                except Exception as exc:
                    logger.warning(f"Supervisor child chat failed: {type(exc).__name__}")
                    result = {"ok": False, "text": ""}
                raw = extract_json_object(str(result.get("text", ""))) if result.get("ok") is True else None
                if raw is not None:
                    advice = validate_advice(raw)
                    break
                if attempt == 0:
                    handle = await self.ensure_supervisor(user_hash, restart=True)
                    if handle is None:
                        break
            if advice is None:
                logger.warning("Supervisor unavailable for hashed user")
                return empty_advice()
            diagnostics = dict(advice.get("diagnostics", {}))
            diagnostics.setdefault("created_at", datetime.now(UTC).isoformat())
            advice["diagnostics"] = diagnostics
            await self._apply_updates(user_hash, advice, heatmap)
            await self.store.save_latest_advice(user_hash, advice)
            return advice

    async def before_turn(self, user_message: dict[str, Any]) -> dict[str, Any] | None:
        """Skip a user's first eligible turn and require supervision thereafter."""
        identity = resolve_identity(user_message)
        user_hash = hash_identity(identity)
        async with self.store.user_lock(user_hash):
            state = await self.store.load_participation(user_hash)
            turns = state.get("eligible_turns", 0)
            turns = turns if isinstance(turns, int) else 0
            state["eligible_turns"] = turns + 1
            if turns == 0:
                state["warmup_status"] = "requested"
                await self.store.save_participation(user_hash, state)
                logger.info("Supervisor first-turn warmup requested")
                await self.store.append_metric(
                    user_hash,
                    {
                        "turn_index": 1,
                        "first_turn": True,
                        "supervisor_required": False,
                        "source": "warmup-requested",
                        "elapsed_ms": 0,
                    },
                )
                return None
            state["last_supervised_turn"] = turns + 1
            await self.store.save_participation(user_hash, state)
        started = perf_counter()
        advice = await self.supervise(user_message)
        source = advice.get("diagnostics", {}).get("source", "unavailable") if advice else "unavailable"
        await self.store.append_metric(
            user_hash,
            {
                "turn_index": turns + 1,
                "first_turn": False,
                "supervisor_required": True,
                "source": source,
                "elapsed_ms": round((perf_counter() - started) * 1000),
            },
        )
        return advice

    async def prime(self, user_message: dict[str, Any]) -> dict[str, Any] | None:
        """Warm a first-turn supervisor without receiving the assistant response."""
        identity = resolve_identity(user_message)
        user_hash = hash_identity(identity)
        state = await self.store.load_participation(user_hash)
        if state.get("warmup_status") != "requested":
            return None
        started = perf_counter()
        advice = await self.supervise(user_message)
        successful = bool(advice and advice.get("diagnostics", {}).get("source") != "unavailable")
        state["warmup_status"] = "completed" if successful else "failed"
        await self.store.save_participation(user_hash, state)
        await self.store.append_metric(
            user_hash,
            {
                "turn_index": 1,
                "first_turn": True,
                "supervisor_required": False,
                "source": "warmup",
                "warmup_status": state["warmup_status"],
                "elapsed_ms": round((perf_counter() - started) * 1000),
            },
        )
        logger.info(f"Supervisor first-turn warmup finished: status={state['warmup_status']}")
        return advice

    async def _apply_updates(self, user_hash: str, advice: dict[str, Any], prior_heatmap: dict[str, Any]) -> None:
        classification = advice["classification"]
        domain = classification["domain"] if re.fullmatch(r"[a-z0-9-]+", classification["domain"]) else "general"
        updates = advice["map_updates"]
        topic = classification.get("topic", "")
        topic = topic if isinstance(topic, str) and topic else f"{domain}-overview"
        seed_node_id = re.sub(r"[^a-z0-9-]+", "-", topic.lower()).strip("-") or f"{domain}-overview"
        async with self.store.domain_lock(domain):
            domain_map = await self.store.load_map(domain)
            proposed = updates["proposed_map"]
            if domain_map is None and isinstance(proposed, dict):
                domain_map = merge_map(None, proposed)
            elif domain_map is None:
                domain_map = merge_map(
                    None,
                    {
                        "domain_id": domain,
                        "nodes": [{"id": seed_node_id, "label": topic, "aliases": []}],
                        "edges": [],
                    },
                )
            if domain_map is not None:
                node_ids = {node.get("id") for node in domain_map.get("nodes", []) if isinstance(node, dict)}
                additions_nodes: list[dict[str, Any]] = []
                additions_edges: list[dict[str, Any]] = []
                for addition in updates["branch_additions"]:
                    if addition["parent_id"] not in node_ids:
                        continue
                    additions_nodes.extend(addition["nodes"])
                    additions_edges.extend(addition["edges"])
                    node_ids.update(node["id"] for node in addition["nodes"])
                domain_map = merge_map(
                    domain_map,
                    {"domain_id": domain, "nodes": additions_nodes, "edges": additions_edges},
                )
                await self.store.save_map(domain, domain_map)
        heatmap = (
            prior_heatmap if prior_heatmap.get("domain") == domain else await self.store.load_heatmap(user_hash, domain)
        )
        state = advice["user_state"]
        visited_nodes = updates["visited_nodes"] or [seed_node_id]
        updated = update_heatmap(
            heatmap,
            node_ids=visited_nodes,
            cognitive_level=str(state["depth"]),
            intent=advice["response_strategy"]["goal_mode"],
            surface=not advice["breakout"]["needed"],
            branch_id=f"{domain}/{topic}",
            requested_depth=advice["response_strategy"]["answer_depth"],
        )
        await self.store.heatmap_path(user_hash, domain).parent.mkdir(parents=True, exist_ok=True)
        await self.store.save_heatmap(user_hash, domain, updated)
