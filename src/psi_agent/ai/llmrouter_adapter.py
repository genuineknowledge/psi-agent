from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import anyio
import yaml
from anyio.to_thread import run_sync
from llmrouter import prompts as llm_prompts
from llmrouter.models.llmmultiroundrouter import LLMMultiRoundRouter

_LLMROUTER_ENV_LOCK = threading.Lock()
_ADAPTER_CONFIG_LOCK = threading.Lock()
_ACTIVE_ADAPTER_CONFIG: tuple[str, str, str] | None = None
_ACTIVE_ADAPTER_COUNT = 0
_REQUIRED_PROMPTS = ("agent_decomp_route.yaml", "agent_decomp_cot.yaml", "agent_prompt.yaml")


@dataclass(frozen=True)
class RouteTarget:
    addr: str
    model_name: str
    description: str


@dataclass(frozen=True)
class RouteDecision:
    target: RouteTarget
    routes: tuple[tuple[str, str], ...]
    votes: dict[str, int]
    source: str = "llmrouter_majority"


def _required_text(item: dict[str, Any], key: str, location: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def parse_upstreams(raw: list[str]) -> list[RouteTarget]:
    if not raw:
        raise ValueError("--upstream must provide at least one JSON object")
    targets: list[RouteTarget] = []
    model_names: set[str] = set()
    allowed = {"addr", "model_name", "description"}
    for index, encoded in enumerate(raw):
        location = f"upstream[{index}]"
        try:
            value: Any = json.loads(encoded)
        except json.JSONDecodeError as exc:
            message = f"{location} must be valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}"
            if encoded.lstrip().startswith("{") and '"' not in encoded:
                message += (
                    r"; PowerShell removed the JSON quotes; escape each inner quote as \" "
                    r"(for example: {\"addr\":\"http://127.0.0.1:8101\",...})"
                )
            raise ValueError(message) from exc
        if not isinstance(value, dict):
            raise ValueError(f"{location} must be a JSON object")
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"{location} has unsupported fields: {sorted(unknown)!r}")
        target = RouteTarget(
            addr=_required_text(value, "addr", location),
            model_name=_required_text(value, "model_name", location),
            description=_required_text(value, "description", location),
        )
        if target.model_name in model_names:
            raise ValueError(f"duplicate upstream model_name: {target.model_name!r}")
        model_names.add(target.model_name)
        targets.append(target)
    return targets


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            item_type = item.get("type")
            if isinstance(text, str):
                parts.append(text)
            elif item_type in {"image", "image_url", "input_image"}:
                parts.append("[IMAGE]")
            elif item_type in {"audio", "input_audio"}:
                parts.append("[AUDIO]")
            elif item_type in {"file", "input_file"}:
                parts.append("[FILE]")
    return "\n".join(part for part in parts if part)


def serialize_context(messages: Any, *, max_chars: int) -> str:
    if max_chars <= 0:
        raise ValueError("router_context_chars must be positive")
    if not isinstance(messages, list):
        return ""
    system = ""
    blocks: list[str] = []
    has_user = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if not isinstance(role, str):
            continue
        if role == "system" and not system:
            system = _content_text(message.get("content"))
        elif role in {"user", "assistant"}:
            text = _content_text(message.get("content"))
            if role == "assistant":
                tool_calls = message.get("tool_calls")
                names: list[str] = []
                if isinstance(tool_calls, list):
                    for call in tool_calls:
                        if not isinstance(call, dict):
                            continue
                        function = call.get("function")
                        if not isinstance(function, dict):
                            continue
                        name = function.get("name")
                        if isinstance(name, str):
                            names.append(name)
                if names:
                    text = f"{text}\n[TOOLS] {', '.join(names)}".strip()
            if text:
                has_user = has_user or role == "user"
                blocks.append(f"[{role.upper()}]\n{text}")
        elif role == "tool":
            blocks.append("[TOOLS]\nTool results exist; result bodies are omitted.")
    if not has_user:
        return ""
    if system:
        blocks.insert(0, f"[SYSTEM]\n{system}")
    while len("\n\n".join(blocks)) > max_chars and len(blocks) > 1:
        blocks.pop(1 if blocks[0].startswith("[SYSTEM]") else 0)
    result = "\n\n".join(blocks)
    if len(result) > max_chars:
        marker = "[TRUNCATED]"
        result = result[: max_chars - len(marker)] + marker
    return result


class LLMRouterAdapter:
    def __init__(
        self,
        *,
        router_model: str,
        router_base_url: str,
        router_api_key: str,
        targets: list[RouteTarget],
        runtime_root: str,
    ) -> None:
        self.router_model = router_model
        self.router_base_url = router_base_url
        self.router_api_key = router_api_key
        self.targets = targets
        self.runtime_dir = anyio.Path(runtime_root) / f"llmrouter-{uuid.uuid4().hex}"
        self.runtime_yaml = self.runtime_dir / "runtime.yaml"
        self.llm_data = self.runtime_dir / "llm_data.json"
        self.router: Any = None
        self._closed = False
        self._registered = False
        self._activity = threading.Condition()
        self._active_workers = 0

    @staticmethod
    def _build_router_sync(runtime_yaml: str, router_model: str, router_base_url: str) -> Any:
        custom_tasks = Path(str(files("psi_agent.ai").joinpath("custom_tasks")))
        for filename in _REQUIRED_PROMPTS:
            prompt = custom_tasks / filename
            if not prompt.is_file():
                raise FileNotFoundError(f"Required LLMRouter prompt not found: {prompt}")
        with _LLMROUTER_ENV_LOCK:
            llm_prompts._PROJECT_ROOT = custom_tasks.parent
            llm_prompts._CUSTOM_TASKS_DIR = custom_tasks
            router = LLMMultiRoundRouter(yaml_path=runtime_yaml)
            base_model = getattr(router, "base_model", None)
            if base_model != router_model:
                raise RuntimeError(f"LLMRouter base_model mismatch: expected {router_model!r}, got {base_model!r}")
            llm_data = getattr(router, "llm_data", None)
            if not isinstance(llm_data, dict):
                raise RuntimeError("LLMRouter llm_data must be a mutable dictionary")
            base_entry = llm_data.get(router_model)
            if base_entry is None:
                llm_data[router_model] = {
                    "model": router_model,
                    "api_endpoint": router_base_url,
                }
            elif isinstance(base_entry, dict):
                base_entry.setdefault("model", router_model)
                base_entry["api_endpoint"] = router_base_url
            else:
                raise RuntimeError(f"LLMRouter llm_data entry for {router_model!r} must be a dictionary")
            return router

    async def start(self) -> None:
        global _ACTIVE_ADAPTER_CONFIG, _ACTIVE_ADAPTER_COUNT

        if self.router is not None:
            return
        config = (self.router_model, self.router_base_url, self.router_api_key)
        with _ADAPTER_CONFIG_LOCK:
            if _ACTIVE_ADAPTER_CONFIG is not None and config != _ACTIVE_ADAPTER_CONFIG:
                raise RuntimeError("only one distinct LLMRouter model configuration is allowed per process")
            _ACTIVE_ADAPTER_CONFIG = config
            _ACTIVE_ADAPTER_COUNT += 1
            self._registered = True
        try:
            await self.runtime_dir.mkdir(parents=True, exist_ok=True)
            descriptions = {
                target.model_name: {"feature": target.description, "model": target.model_name}
                for target in self.targets
            }
            await self.llm_data.write_text(
                json.dumps(descriptions, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            runtime = {
                "data_path": {"llm_data": str(self.llm_data)},
                "base_model": self.router_model,
                "use_local_llm": False,
                "api_endpoint": self.router_base_url,
            }
            await self.runtime_yaml.write_text(
                yaml.safe_dump(runtime, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            self.router = await run_sync(
                self._build_router_sync,
                str(self.runtime_yaml),
                self.router_model,
                self.router_base_url,
            )
        except BaseException:
            with anyio.CancelScope(shield=True):
                await self._remove_runtime()
                self._unregister()
            raise

    def _route_sync(self, context: str) -> Any:
        try:
            with _LLMROUTER_ENV_LOCK:
                previous = os.environ.get("API_KEYS")
                os.environ["API_KEYS"] = self.router_api_key
                try:
                    return self.router._decompose_and_route(context)
                finally:
                    if previous is None:
                        os.environ.pop("API_KEYS", None)
                    else:
                        os.environ["API_KEYS"] = previous
        finally:
            with self._activity:
                self._active_workers -= 1
                self._activity.notify_all()

    async def route(self, context: str) -> RouteDecision:
        if self.router is None or self._closed:
            raise RuntimeError("LLMRouterAdapter is not running")
        with self._activity:
            self._active_workers += 1
        try:
            raw_routes = await run_sync(self._route_sync, context, abandon_on_cancel=True)
        except BaseException:
            # Once scheduled, an abandoned worker owns the decrement in _route_sync.
            raise
        routes: list[tuple[str, str]] = []
        target_by_model_name = {target.model_name: target for target in self.targets}
        if isinstance(raw_routes, list):
            for item in raw_routes:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    continue
                sub_query, model_name = item
                if (
                    isinstance(sub_query, str)
                    and isinstance(model_name, str)
                    and model_name in target_by_model_name
                ):
                    routes.append((sub_query, model_name))
        if not routes:
            raise RuntimeError("LLMRouter returned no valid candidate routes")
        votes: dict[str, int] = {}
        for _, model_name in routes:
            votes[model_name] = votes.get(model_name, 0) + 1
        maximum = max(votes.values())
        winner = next(model_name for _, model_name in routes if votes[model_name] == maximum)
        return RouteDecision(
            target=target_by_model_name[winner],
            routes=tuple(routes),
            votes=votes,
        )

    async def close(self) -> None:
        self._closed = True
        await run_sync(self._wait_for_workers_sync)
        self.router = None
        await self._remove_runtime()
        self._unregister()

    async def _remove_runtime(self) -> None:
        if await self.runtime_yaml.exists():
            await self.runtime_yaml.unlink()
        if await self.llm_data.exists():
            await self.llm_data.unlink()
        if await self.runtime_dir.exists():
            await self.runtime_dir.rmdir()

    def _wait_for_workers_sync(self) -> None:
        with self._activity:
            while self._active_workers:
                self._activity.wait()

    def _unregister(self) -> None:
        global _ACTIVE_ADAPTER_CONFIG, _ACTIVE_ADAPTER_COUNT

        if not self._registered:
            return
        with _ADAPTER_CONFIG_LOCK:
            _ACTIVE_ADAPTER_COUNT -= 1
            if _ACTIVE_ADAPTER_COUNT == 0:
                _ACTIVE_ADAPTER_CONFIG = None
            self._registered = False
