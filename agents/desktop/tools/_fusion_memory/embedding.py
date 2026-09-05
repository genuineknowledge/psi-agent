from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import aiohttp

from .journal import EvidenceSpan

DEFAULT_EMBEDDING_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
DEFAULT_RERANK_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    api_key: str
    model: str
    endpoint: str
    timeout_seconds: float
    batch_size: int


@dataclass(frozen=True, slots=True)
class RerankConfig:
    api_key: str
    model: str
    endpoint: str
    timeout_seconds: float
    top_n: int


@dataclass(frozen=True, slots=True)
class LlmConfig:
    api_key: str
    provider: str
    model: str
    endpoint: str
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class ModelConfig:
    embedding: EmbeddingConfig
    rerank: RerankConfig
    llm: LlmConfig | None


@dataclass(frozen=True, slots=True)
class MemoryItemDraft:
    kind: str
    text: str
    confidence: float
    salience: float
    source_span_ids: tuple[str, ...]


class ModelCallError(RuntimeError):
    def __init__(self, operation: str, status: int | None = None) -> None:
        self.operation = operation
        self.status = status
        suffix = f" HTTP {status}" if status is not None else ""
        super().__init__(f"{operation} model call failed{suffix}")


def _positive_float(value: str | None, default: float) -> float:
    try:
        parsed = float(value or "")
    except ValueError:
        return default
    return parsed if math.isfinite(parsed) and parsed > 0 else default


def _bounded_int(value: str | None, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value or "")
    except ValueError:
        return default
    return min(high, max(low, parsed))


def _endpoint(base: str) -> str:
    value = base.strip().rstrip("/")
    return value if value.endswith("/chat/completions") else f"{value}/chat/completions"


def load_model_config(env: Mapping[str, str] | None = None) -> ModelConfig:
    values = env if env is not None else __import__("os").environ
    embedding = EmbeddingConfig(
        api_key=values.get("DASHSCOPE_API_KEY", "").strip(),
        model=values.get("FUSION_MEMORY_EMBEDDING_MODEL", "text-embedding-v4").strip(),
        endpoint=values.get("FUSION_MEMORY_EMBEDDING_ENDPOINT", DEFAULT_EMBEDDING_ENDPOINT).strip(),
        timeout_seconds=_positive_float(values.get("FUSION_MEMORY_EMBEDDING_TIMEOUT_SECONDS"), 15.0),
        batch_size=_bounded_int(values.get("FUSION_MEMORY_EMBEDDING_BATCH_SIZE"), 8, 1, 32),
    )
    rerank_config = RerankConfig(
        api_key=values.get("DASHSCOPE_API_KEY", "").strip(),
        model=values.get("FUSION_MEMORY_RERANKER_MODEL", "qwen3-rerank").strip(),
        endpoint=values.get("FUSION_MEMORY_RERANKER_ENDPOINT", DEFAULT_RERANK_ENDPOINT).strip(),
        timeout_seconds=_positive_float(values.get("FUSION_MEMORY_RERANKER_TIMEOUT_SECONDS"), 15.0),
        top_n=_bounded_int(values.get("FUSION_MEMORY_RERANKER_TOP_N"), 20, 1, 50),
    )

    dedicated = (
        values.get("FUSION_MEMORY_MODEL_PROVIDER", "").strip().lower(),
        values.get("FUSION_MEMORY_MODEL_NAME", "").strip(),
        values.get("FUSION_MEMORY_MODEL_API_KEY", "").strip(),
        values.get("FUSION_MEMORY_MODEL_BASE_URL", "").strip(),
    )
    agent = (
        values.get("PSI_AI_PROVIDER", "").strip().lower(),
        values.get("PSI_AI_MODEL", "").strip(),
        values.get("PSI_AI_API_KEY", "").strip(),
        values.get("PSI_AI_BASE_URL", "").strip(),
    )
    if all(dedicated):
        provider, model, llm_key, base_url = dedicated
    elif all(agent):
        provider, model, agent_key, base_url = agent
        llm_key = dedicated[2] or agent_key
    else:
        provider = model = llm_key = base_url = ""
    llm = None
    if llm_key and provider in {"openai", "openai-compatible", "deepseek", "dashscope"} and model and base_url:
        llm = LlmConfig(
            api_key=llm_key,
            provider=provider,
            model=model,
            endpoint=_endpoint(base_url),
            timeout_seconds=_positive_float(values.get("FUSION_MEMORY_MODEL_TIMEOUT_SECONDS"), 30.0),
        )
    return ModelConfig(embedding, rerank_config, llm)


async def _request_json(
    operation: str,
    endpoint: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout, raise_for_status=False) as session,
            session.post(endpoint, headers=headers, json=payload) as response,
        ):
            if response.status < 200 or response.status >= 300:
                raise ModelCallError(operation, response.status)
            body = await response.content.read(1_048_577)
            if len(body) > 1_048_576:
                raise ModelCallError(operation, response.status)
    except ModelCallError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise ModelCallError(operation) from exc
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelCallError(operation, response.status) from exc
    if not isinstance(decoded, dict):
        raise ModelCallError(operation, response.status)
    return decoded


def _vector(value: object) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, (int, float, str)) for item in value):
        return None
    try:
        numeric = cast(list[int | float | str], value)
        result = [float(item) for item in numeric]
    except TypeError, ValueError:
        return None
    return result if all(math.isfinite(item) for item in result) else None


async def embed_texts(config: ModelConfig | EmbeddingConfig, texts: list[str]) -> list[list[float]]:
    embedding = config.embedding if isinstance(config, ModelConfig) else config
    if not texts:
        return []
    if not embedding.api_key:
        raise ModelCallError("embedding")
    payload = {"model": embedding.model, "input": texts}
    decoded = await _request_json(
        "embedding", embedding.endpoint, embedding.api_key, payload, embedding.timeout_seconds
    )
    data = decoded.get("data")
    if not isinstance(data, list):
        data = (decoded.get("output") or {}).get("embeddings") if isinstance(decoded.get("output"), dict) else None
    if not isinstance(data, list) or len(data) != len(texts):
        raise ModelCallError("embedding")
    vectors: list[list[float]] = []
    for item in sorted(data, key=lambda entry: entry.get("index", 0) if isinstance(entry, dict) else 0):
        vector = _vector(item.get("embedding") if isinstance(item, dict) else item)
        if vector is None:
            raise ModelCallError("embedding")
        vectors.append(vector)
    return vectors


async def rerank(config: ModelConfig | RerankConfig, query: str, documents: list[str]) -> list[float]:
    rerank_config = config.rerank if isinstance(config, ModelConfig) else config
    if not documents:
        return []
    if not rerank_config.api_key:
        raise ModelCallError("rerank")
    payload = {
        "model": rerank_config.model,
        "input": {"query": query, "documents": documents},
        "parameters": {"top_n": rerank_config.top_n, "return_documents": False},
    }
    decoded = await _request_json(
        "rerank", rerank_config.endpoint, rerank_config.api_key, payload, rerank_config.timeout_seconds
    )
    output = decoded.get("output", decoded)
    results = output.get("results") if isinstance(output, dict) else None
    if not isinstance(results, list):
        raise ModelCallError("rerank")
    scores = [0.0] * len(documents)
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("index"), int):
            raise ModelCallError("rerank")
        index = item["index"]
        if index < 0 or index >= len(scores):
            raise ModelCallError("rerank")
        value = item.get("relevance_score", item.get("score"))
        try:
            score = float(value)
        except (TypeError, ValueError) as exc:
            raise ModelCallError("rerank") from exc
        if not math.isfinite(score):
            raise ModelCallError("rerank")
        scores[index] = score
    return scores


async def extract_memory_items(config: ModelConfig, spans: list[EvidenceSpan]) -> list[MemoryItemDraft]:
    if config.llm is None or not spans:
        return []
    source_ids = {span.span_id for span in spans}
    prompt = {
        "instruction": "Extract durable memory items from the visible conversation. Return JSON only.",
        "items_schema": {
            "items": [
                {
                    "kind": "fact|preference|decision|plan|event",
                    "text": "...",
                    "confidence": 0.0,
                    "salience": 0.0,
                    "source_span_ids": [],
                }
            ]
        },
        "spans": [{"span_id": span.span_id, "speaker": span.speaker, "content": span.content} for span in spans],
    }
    payload = {
        "model": config.llm.model,
        "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    decoded = await _request_json("llm", config.llm.endpoint, config.llm.api_key, payload, config.llm.timeout_seconds)
    choices = decoded.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelCallError("llm")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    try:
        raw_items = json.loads(content) if isinstance(content, str) else None
    except json.JSONDecodeError as exc:
        raise ModelCallError("llm") from exc
    raw_items = raw_items.get("items") if isinstance(raw_items, dict) else None
    if not isinstance(raw_items, list):
        raise ModelCallError("llm")
    drafts: list[MemoryItemDraft] = []
    for item in raw_items:
        if not isinstance(item, dict) or item.get("kind") not in {"fact", "preference", "decision", "plan", "event"}:
            continue
        text = item.get("text")
        ids = item.get("source_span_ids")
        if not isinstance(text, str) or not text.strip() or not isinstance(ids, list) or not ids:
            continue
        ids_tuple = tuple(dict.fromkeys(str(value) for value in ids))
        if not set(ids_tuple) <= source_ids:
            continue
        try:
            confidence = float(item.get("confidence"))
            salience = float(item.get("salience"))
        except TypeError, ValueError:
            continue
        if not (0 <= confidence <= 1 and 0 <= salience <= 1):
            continue
        drafts.append(MemoryItemDraft(item["kind"], text.strip(), confidence, salience, ids_tuple))
    return drafts


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left or not right:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0
