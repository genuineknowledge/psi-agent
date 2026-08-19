from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

PROGRAM_PATH = Path(__file__).resolve().parents[1] / "programs" / "fetch_feishu_reference_documents.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("fetch_feishu_reference_documents", PROGRAM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _configuration() -> list[dict[str, str]]:
    return [
        {
            "purpose": "resume_scoring",
            "configured_url": "https://example.feishu.cn/wiki/scoring-wiki",
        },
        {
            "purpose": "role_information",
            "configured_url": "https://example.feishu.cn/wiki/roles-wiki",
        },
    ]


def _environment() -> dict[str, str]:
    return {
        "PSI_FEISHU_APP_ID": "test-app-id",
        "PSI_FEISHU_APP_SECRET": "test-app-secret",
    }


def _bitable_document_page_configuration() -> list[dict[str, str]]:
    return [
        {
            "purpose": "resume_scoring",
            "configured_url": "https://example.feishu.cn/wiki/base?table=ldxScoringPage001",
            "document_token": "ScoringDocxToken123456789012345",
            "document_page_id": "ldxScoringPage001",
        },
        {
            "purpose": "role_information",
            "configured_url": "https://example.feishu.cn/wiki/base?table=ldxRolePage000001",
            "document_token": "RoleDocxToken123456789012345678",
            "document_page_id": "ldxRolePage000001",
        },
    ]


class FakeFeishuTransport:
    def __init__(
        self,
        *,
        auth_code: int = 0,
        wiki_code: int = 0,
        object_type: str = "docx",
        empty_content_for: str | None = None,
    ) -> None:
        self.auth_code = auth_code
        self.wiki_code = wiki_code
        self.object_type = object_type
        self.empty_content_for = empty_content_for
        self.urls: list[str] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        payload: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        del method, headers, payload
        self.urls.append(url)
        if url.endswith("/auth/v3/tenant_access_token/internal"):
            return {"code": self.auth_code, "tenant_access_token": "tenant-token"}
        if "/wiki/v2/spaces/get_node?token=" in url:
            wiki_token = url.rsplit("=", 1)[1]
            return {
                "code": self.wiki_code,
                "data": {
                    "node": {
                        "obj_type": self.object_type,
                        "obj_token": f"doc-{wiki_token}",
                        "title": "简历评分标准" if wiki_token == "scoring-wiki" else "招聘需求总览",
                    }
                },
            }
        if "/docx/v1/documents/doc-scoring-wiki/raw_content" in url:
            content = "" if self.empty_content_for == "resume_scoring" else "评分标准正文"
            return {"code": 0, "data": {"content": content}}
        if "/docx/v1/documents/doc-roles-wiki/raw_content" in url:
            content = "" if self.empty_content_for == "role_information" else "招聘岗位正文"
            return {"code": 0, "data": {"content": content}}
        if url.endswith("/docx/v1/documents/ScoringDocxToken123456789012345"):
            return {
                "code": 0,
                "data": {"document": {"title": "简历评分标准"}},
            }
        if url.endswith("/docx/v1/documents/RoleDocxToken123456789012345678"):
            return {
                "code": 0,
                "data": {"document": {"title": "示例招聘需求总览"}},
            }
        if url.endswith("/docx/v1/documents/ScoringDocxToken123456789012345/raw_content"):
            return {"code": 0, "data": {"content": "评分标准正文"}}
        if url.endswith("/docx/v1/documents/RoleDocxToken123456789012345678/raw_content"):
            return {"code": 0, "data": {"content": "招聘岗位正文"}}
        raise AssertionError("Unexpected Feishu endpoint")


def _run(module, transport: FakeFeishuTransport, configuration=None):
    return module.run(
        {"reference_document_config": configuration or _configuration()},
        environment=_environment(),
        request_json=transport,
        fetched_at="2026-08-09T04:00:00+00:00",
    )


def test_wiki_token_parser_accepts_only_an_exact_wiki_path() -> None:
    """Relaxing path parsing must not silently accept a Docx or nested URL."""
    module = _load_module()

    assert module.wiki_token_from_url("https://tenant.feishu.cn/wiki/Oc5jw3RxKiOK5bkCaQ8cAz0YnLf") == (
        "Oc5jw3RxKiOK5bkCaQ8cAz0YnLf"
    )
    with pytest.raises(ValueError):
        module.wiki_token_from_url("https://tenant.feishu.cn/docx/Oc5jw3RxKiOK5bkCaQ8cAz0YnLf")


def test_bitable_document_pages_use_explicit_docx_tokens_without_wiki_resolution() -> None:
    """Removing the direct-token path must not make headless runs depend on resolving an ldx page."""
    module = _load_module()
    transport = FakeFeishuTransport()

    result = _run(module, transport, _bitable_document_page_configuration())

    assert result["reference_document_manifest"]["status"] == "complete"
    assert [item["title"] for item in result["reference_documents"]] == [
        "简历评分标准",
        "示例招聘需求总览",
    ]
    assert [item["document_page_id"] for item in result["reference_documents"]] == [
        "ldxScoringPage001",
        "ldxRolePage000001",
    ]
    assert not any("/wiki/v2/spaces/get_node" in url for url in transport.urls)


def test_tenant_token_failure_blocks_the_complete_document_batch() -> None:
    """Ignoring authentication failure must not produce a partial or complete manifest."""
    module = _load_module()

    result = _run(module, FakeFeishuTransport(auth_code=999))

    assert result["reference_documents"] == []
    assert result["reference_document_manifest"] == {
        "schema_version": "1.0",
        "status": "blocked",
        "errors": ["tenant_authentication_failed"],
    }


def test_wiki_permission_failure_is_sanitized_and_blocks_the_batch() -> None:
    """Passing through a denied Wiki node must not expose API details or continue."""
    module = _load_module()

    result = _run(module, FakeFeishuTransport(wiki_code=999))

    assert result["reference_documents"] == []
    assert result["reference_document_manifest"]["errors"] == ["wiki_resolution_failed:resume_scoring"]
    assert "test-app-secret" not in repr(result)
    assert "tenant-token" not in repr(result)


def test_non_docx_wiki_node_blocks_the_batch() -> None:
    """Removing the type guard must not allow unsupported Wiki objects into scoring."""
    module = _load_module()

    result = _run(module, FakeFeishuTransport(object_type="sheet"))

    assert result["reference_documents"] == []
    assert result["reference_document_manifest"]["errors"] == ["unsupported_document_type:resume_scoring"]


@pytest.mark.parametrize("purpose", ["resume_scoring", "role_information"])
def test_empty_docx_content_blocks_the_complete_batch(purpose: str) -> None:
    """Accepting blank content must not let a batch run without one business source."""
    module = _load_module()

    result = _run(module, FakeFeishuTransport(empty_content_for=purpose))

    assert result["reference_documents"] == []
    assert result["reference_document_manifest"]["errors"] == [f"empty_document_content:{purpose}"]


def test_duplicate_document_configuration_blocks_before_authentication() -> None:
    """Dropping uniqueness validation must not let one Wiki satisfy two purposes."""
    module = _load_module()
    duplicate = _configuration()
    duplicate[1]["configured_url"] = duplicate[0]["configured_url"]

    result = _run(module, FakeFeishuTransport(), duplicate)

    assert result["reference_documents"] == []
    assert result["reference_document_manifest"]["errors"] == ["invalid_reference_document_configuration"]


def test_successful_fetch_returns_two_versioned_documents() -> None:
    """Using the wrong body, purpose, token, or hash must break the versioned artifact contract."""
    module = _load_module()

    result = _run(module, FakeFeishuTransport())

    assert result["reference_document_manifest"] == {
        "schema_version": "1.0",
        "status": "complete",
        "errors": [],
    }
    assert result["reference_documents"] == [
        {
            "purpose": "resume_scoring",
            "configured_url": "https://example.feishu.cn/wiki/scoring-wiki",
            "wiki_token": "scoring-wiki",
            "document_token": "doc-scoring-wiki",
            "title": "简历评分标准",
            "content": "评分标准正文",
            "content_sha256": "54b84a40e484f23a2afa9564babd4a79065a9138bd108229e6e59b63be7f37b2",
            "fetched_at": "2026-08-09T04:00:00+00:00",
        },
        {
            "purpose": "role_information",
            "configured_url": "https://example.feishu.cn/wiki/roles-wiki",
            "wiki_token": "roles-wiki",
            "document_token": "doc-roles-wiki",
            "title": "招聘需求总览",
            "content": "招聘岗位正文",
            "content_sha256": "5e3c4a5af4dd61e514fd294a25987555d075937e4c65c9a5bb0299176ae0e48e",
            "fetched_at": "2026-08-09T04:00:00+00:00",
        },
    ]
