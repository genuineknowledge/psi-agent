from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import anyio
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

if TYPE_CHECKING:
    from agents.desktop.tools._fusion_memory.embedding import (
        ModelCallError,
        cosine_similarity,
        embed_texts,
        extract_memory_items,
        load_model_config,
        rerank,
    )
    from agents.desktop.tools._fusion_memory.journal import EvidenceSpan
else:
    from _fusion_memory.embedding import (
        ModelCallError,
        cosine_similarity,
        embed_texts,
        extract_memory_items,
        load_model_config,
        rerank,
    )
    from _fusion_memory.journal import EvidenceSpan


def test_vector_clients_only_use_dashscope_key() -> None:
    config = load_model_config(
        {
            "DASHSCOPE_API_KEY": "dash-secret",
            "FUSION_MEMORY_EMBEDDING_API_KEY": "wrong-embedding",
            "FUSION_MEMORY_RERANKER_API_KEY": "wrong-rerank",
            "FUSION_MEMORY_MODEL_API_KEY": "llm-secret",
        }
    )
    assert config.embedding.api_key == "dash-secret"
    assert config.embedding.model == "text-embedding-v4"
    assert config.rerank.api_key == "dash-secret"
    assert config.rerank.model == "qwen3-rerank"
    assert config.llm is None


def test_llm_dedicated_key_can_reuse_agent_metadata_but_fallback_is_whole_group() -> None:
    dedicated = load_model_config(
        {
            "FUSION_MEMORY_MODEL_API_KEY": "llm-secret",
            "PSI_AI_PROVIDER": "openai",
            "PSI_AI_MODEL": "qwen-plus",
            "PSI_AI_API_KEY": "agent-secret",
            "PSI_AI_BASE_URL": "https://llm.example/v1",
        }
    )
    assert dedicated.llm is not None
    assert dedicated.llm.api_key == "llm-secret"
    fallback = load_model_config(
        {
            "PSI_AI_PROVIDER": "openai",
            "PSI_AI_MODEL": "qwen-plus",
            "PSI_AI_API_KEY": "agent-secret",
            "PSI_AI_BASE_URL": "https://llm.example/v1",
        }
    )
    assert fallback.llm is not None and fallback.llm.api_key == "agent-secret"
    assert load_model_config({"PSI_AI_MODEL": "qwen-plus", "PSI_AI_API_KEY": "agent-secret"}).llm is None
    no_mixing = load_model_config(
        {
            "FUSION_MEMORY_MODEL_PROVIDER": "deepseek",
            "PSI_AI_PROVIDER": "openai",
            "PSI_AI_MODEL": "agent-model",
            "PSI_AI_API_KEY": "agent-secret",
            "PSI_AI_BASE_URL": "https://agent.example/v1",
        }
    )
    assert no_mixing.llm is not None
    assert (no_mixing.llm.provider, no_mixing.llm.model) == ("openai", "agent-model")


def test_llm_dedicated_key_requires_complete_agent_group_before_reusing_metadata() -> None:
    incomplete_agent = load_model_config(
        {
            "FUSION_MEMORY_MODEL_API_KEY": "llm-secret",
            "PSI_AI_PROVIDER": "openai",
            "PSI_AI_MODEL": "qwen-plus",
            "PSI_AI_BASE_URL": "https://llm.example/v1",
        }
    )
    assert incomplete_agent.llm is None


def test_llm_dedicated_key_does_not_mix_partial_fusion_metadata_with_agent_group() -> None:
    config = load_model_config(
        {
            "FUSION_MEMORY_MODEL_API_KEY": "llm-secret",
            "FUSION_MEMORY_MODEL_PROVIDER": "deepseek",
            "PSI_AI_PROVIDER": "openai",
            "PSI_AI_MODEL": "qwen-plus",
            "PSI_AI_API_KEY": "agent-secret",
            "PSI_AI_BASE_URL": "https://llm.example/v1",
        }
    )
    assert config.llm is not None
    assert (config.llm.api_key, config.llm.provider, config.llm.model, config.llm.endpoint) == (
        "llm-secret",
        "openai",
        "qwen-plus",
        "https://llm.example/v1/chat/completions",
    )


def test_cosine_similarity_is_safe_for_empty_or_mismatched_vectors() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0


@pytest.mark.parametrize("status", [429, 500])
async def test_embedding_http_errors_are_sanitized(status: int) -> None:
    async def reject(_request: web.Request) -> web.Response:
        return web.json_response({"request": "secret input"}, status=status)

    server = TestServer(web.Application())
    server.app.router.add_post("/embedding", reject)
    await server.start_server()
    try:
        config = load_model_config({"DASHSCOPE_API_KEY": "dash-secret"})
        embedding = replace(config.embedding, endpoint=str(server.make_url("/embedding")))
        with pytest.raises(ModelCallError) as raised:
            await embed_texts(embedding, ["private request body"])
        assert raised.value.status == status
        assert "dash-secret" not in str(raised.value)
        assert "private request body" not in str(raised.value)
    finally:
        await server.close()


async def test_embedding_timeout_is_sanitized() -> None:
    async def slow(_request: web.Request) -> web.Response:
        await anyio.sleep(0.1)
        return web.json_response({"data": [{"embedding": [1.0]}]})

    server = TestServer(web.Application())
    server.app.router.add_post("/embedding", slow)
    await server.start_server()
    try:
        config = load_model_config({"DASHSCOPE_API_KEY": "dash-secret"})
        embedding = replace(
            config.embedding,
            endpoint=str(server.make_url("/embedding")),
            timeout_seconds=0.01,
        )
        with pytest.raises(ModelCallError) as raised:
            await embed_texts(embedding, ["private request body"])
        assert raised.value.status is None
        assert "dash-secret" not in str(raised.value)
        assert "private request body" not in str(raised.value)
    finally:
        await server.close()


async def test_malformed_embedding_rerank_and_llm_responses_fail_closed() -> None:
    async def malformed(request: web.Request) -> web.Response:
        if request.path == "/embedding":
            return web.json_response({"data": [{"embedding": ["not-a-number"]}]})
        if request.path == "/rerank":
            return web.json_response({"output": {"results": [{"index": 99, "relevance_score": 1.0}]}})
        return web.json_response({"choices": [{"message": {"content": "not-json"}}]})

    app = web.Application()
    app.router.add_post("/{path:.*}", malformed)
    server = TestServer(app)
    await server.start_server()
    try:
        base = str(server.make_url("/")).rstrip("/")
        config = load_model_config(
            {
                "DASHSCOPE_API_KEY": "dash-secret",
                "FUSION_MEMORY_EMBEDDING_ENDPOINT": f"{base}/embedding",
                "FUSION_MEMORY_RERANKER_ENDPOINT": f"{base}/rerank",
                "FUSION_MEMORY_MODEL_API_KEY": "llm-secret",
                "FUSION_MEMORY_MODEL_PROVIDER": "openai",
                "FUSION_MEMORY_MODEL_NAME": "model",
                "FUSION_MEMORY_MODEL_BASE_URL": f"{base}/llm",
            }
        )
        evidence = EvidenceSpan(
            "span-1",
            "workspace-a",
            "session-1",
            "turn-1",
            1,
            "assistant",
            "answer",
            "hash",
            None,
            "history:///s1#L1",
        )

        with pytest.raises(ModelCallError):
            await embed_texts(config, ["text"])
        with pytest.raises(ModelCallError):
            await rerank(config, "query", ["document"])
        with pytest.raises(ModelCallError):
            await extract_memory_items(config, [evidence])
    finally:
        await server.close()
