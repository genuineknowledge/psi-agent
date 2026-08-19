"""Fetch and version two Feishu Wiki or Base document-page sources once per batch."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request, urlopen

_FEISHU_API_ROOT = "https://open.feishu.cn/open-apis"
_EXPECTED_PURPOSES = ("resume_scoring", "role_information")
_APP_ID_KEY = "PSI_FEISHU_APP_ID"
_APP_SECRET_KEY = "PSI_FEISHU_APP_SECRET"
RequestJson = Callable[
    [str, str, dict[str, str] | None, dict[str, str] | None],
    dict[str, Any],
]


class _SafeFetchError(ValueError):
    """An error whose message is intentionally safe for the workflow manifest."""


def wiki_token_from_url(configured_url: str) -> str:
    """Return the token from an exact HTTPS ``/wiki/<token>`` URL."""
    if not isinstance(configured_url, str) or not configured_url.strip():
        raise ValueError("invalid Wiki URL")
    parsed = urlsplit(configured_url.strip())
    parts = parsed.path.split("/")
    valid_path = len(parts) == 3 and parts[0] == "" and parts[1] == "wiki" and parts[2]
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not valid_path
    ):
        raise ValueError("invalid Wiki URL")
    token = parts[2]
    if not all(character.isalnum() or character in "-_" for character in token):
        raise ValueError("invalid Wiki URL")
    return token


def bitable_document_page_id_from_url(configured_url: str) -> str:
    """Return the ldx page id from an exact HTTPS Base document-page URL."""
    if not isinstance(configured_url, str) or not configured_url.strip():
        raise ValueError("invalid Base document-page URL")
    parsed = urlsplit(configured_url.strip())
    parts = parsed.path.split("/")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    valid_path = len(parts) == 3 and parts[0] == "" and parts[1] == "wiki" and parts[2]
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not valid_path
        or len(query) != 1
        or query[0][0] != "table"
    ):
        raise ValueError("invalid Base document-page URL")
    page_id = query[0][1]
    if not page_id.startswith("ldx") or not page_id[3:].isalnum():
        raise ValueError("invalid Base document-page URL")
    return page_id


def _validate_configuration(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != len(_EXPECTED_PURPOSES):
        raise _SafeFetchError("invalid_reference_document_configuration")
    configured: list[dict[str, str]] = []
    seen_purposes: set[str] = set()
    seen_source_tokens: set[str] = set()
    seen_document_tokens: set[str] = set()
    try:
        for item in value:
            if not isinstance(item, dict):
                raise ValueError
            purpose = item.get("purpose")
            configured_url = item.get("configured_url")
            if purpose not in _EXPECTED_PURPOSES or purpose in seen_purposes:
                raise ValueError
            if not isinstance(configured_url, str):
                raise ValueError
            configured_url = configured_url.strip()
            document_token = item.get("document_token")
            if document_token is None:
                source_token = wiki_token_from_url(configured_url)
                normalized = {
                    "purpose": purpose,
                    "configured_url": configured_url,
                    "wiki_token": source_token,
                }
            else:
                if (
                    not isinstance(document_token, str)
                    or len(document_token.strip()) < 27
                    or not all(character.isalnum() or character in "-_" for character in document_token.strip())
                ):
                    raise ValueError
                page_id = bitable_document_page_id_from_url(configured_url)
                if item.get("document_page_id") != page_id:
                    raise ValueError
                document_token = document_token.strip()
                if document_token in seen_document_tokens:
                    raise ValueError
                seen_document_tokens.add(document_token)
                source_token = page_id
                normalized = {
                    "purpose": purpose,
                    "configured_url": configured_url,
                    "document_token": document_token,
                    "document_page_id": page_id,
                }
            if source_token in seen_source_tokens:
                raise ValueError
            seen_purposes.add(purpose)
            seen_source_tokens.add(source_token)
            configured.append(normalized)
    except TypeError, ValueError:
        raise _SafeFetchError("invalid_reference_document_configuration") from None
    if seen_purposes != set(_EXPECTED_PURPOSES):
        raise _SafeFetchError("invalid_reference_document_configuration")
    configured.sort(key=lambda item: _EXPECTED_PURPOSES.index(item["purpose"]))
    return configured


def _default_request_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    payload: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {"Accept": "application/json", **(headers or {})}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Feishu API request failed") from error
    if not isinstance(decoded, dict):
        raise RuntimeError("Feishu API returned a non-object response")
    return decoded


def _blocked(error: str) -> dict[str, Any]:
    return {
        "reference_documents": [],
        "reference_document_manifest": {
            "schema_version": "1.0",
            "status": "blocked",
            "errors": [error],
        },
    }


def run(
    inputs: dict[str, Any],
    workspace_root: str | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    request_json: RequestJson | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Return both documents atomically, or a sanitized blocked manifest with no documents."""
    del workspace_root
    try:
        configured = _validate_configuration(inputs.get("reference_document_config"))
        resolved_environment = os.environ if environment is None else environment
        app_id = resolved_environment.get(_APP_ID_KEY)
        app_secret = resolved_environment.get(_APP_SECRET_KEY)
        if (
            not isinstance(app_id, str)
            or not app_id.strip()
            or not isinstance(app_secret, str)
            or not app_secret.strip()
        ):
            raise _SafeFetchError("missing_feishu_credentials")
        transport = _default_request_json if request_json is None else request_json
        try:
            authentication = transport(
                "POST",
                f"{_FEISHU_API_ROOT}/auth/v3/tenant_access_token/internal",
                None,
                {"app_id": app_id, "app_secret": app_secret},
            )
        except Exception:
            raise _SafeFetchError("tenant_authentication_failed") from None
        tenant_token = authentication.get("tenant_access_token")
        if authentication.get("code") != 0 or not isinstance(tenant_token, str) or not tenant_token:
            raise _SafeFetchError("tenant_authentication_failed")

        timestamp = fetched_at or datetime.now(UTC).isoformat()
        headers = {"Authorization": f"Bearer {tenant_token}"}
        documents: list[dict[str, str]] = []
        for item in configured:
            purpose = item["purpose"]
            configured_url = item["configured_url"]
            document_token = item.get("document_token")
            if document_token is None:
                wiki_token = item["wiki_token"]
                try:
                    node_response = transport(
                        "GET",
                        f"{_FEISHU_API_ROOT}/wiki/v2/spaces/get_node?token={wiki_token}",
                        headers,
                        None,
                    )
                except Exception:
                    raise _SafeFetchError(f"wiki_resolution_failed:{purpose}") from None
                node = (
                    node_response.get("data", {}).get("node", {}) if isinstance(node_response.get("data"), dict) else {}
                )
                if node_response.get("code") != 0 or not isinstance(node, dict):
                    raise _SafeFetchError(f"wiki_resolution_failed:{purpose}")
                if node.get("obj_type") != "docx":
                    raise _SafeFetchError(f"unsupported_document_type:{purpose}")
                document_token = node.get("obj_token")
                title = node.get("title")
                if (
                    not isinstance(document_token, str)
                    or not document_token
                    or not isinstance(title, str)
                    or not title.strip()
                ):
                    raise _SafeFetchError(f"invalid_wiki_node_metadata:{purpose}")
            else:
                try:
                    metadata_response = transport(
                        "GET",
                        f"{_FEISHU_API_ROOT}/docx/v1/documents/{document_token}",
                        headers,
                        None,
                    )
                except Exception:
                    raise _SafeFetchError(f"document_metadata_read_failed:{purpose}") from None
                metadata = (
                    metadata_response.get("data", {}).get("document", {})
                    if isinstance(metadata_response.get("data"), dict)
                    else {}
                )
                title = metadata.get("title") if isinstance(metadata, dict) else None
                if metadata_response.get("code") != 0 or not isinstance(title, str) or not title.strip():
                    raise _SafeFetchError(f"document_metadata_read_failed:{purpose}")
            try:
                content_response = transport(
                    "GET",
                    f"{_FEISHU_API_ROOT}/docx/v1/documents/{document_token}/raw_content",
                    headers,
                    None,
                )
            except Exception:
                raise _SafeFetchError(f"document_content_read_failed:{purpose}") from None
            data = content_response.get("data")
            content = data.get("content") if isinstance(data, dict) else None
            if content_response.get("code") != 0:
                raise _SafeFetchError(f"document_content_read_failed:{purpose}")
            if not isinstance(content, str) or not content.strip():
                raise _SafeFetchError(f"empty_document_content:{purpose}")
            document = {
                "purpose": purpose,
                "configured_url": configured_url,
                "document_token": document_token,
                "title": title.strip(),
                "content": content,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "fetched_at": timestamp,
            }
            if "document_page_id" in item:
                document["document_page_id"] = item["document_page_id"]
            else:
                document["wiki_token"] = item["wiki_token"]
            documents.append(document)
        return {
            "reference_documents": documents,
            "reference_document_manifest": {
                "schema_version": "1.0",
                "status": "complete",
                "errors": [],
            },
        }
    except _SafeFetchError as error:
        return _blocked(str(error))
    except Exception:
        return _blocked("reference_document_fetch_failed")


def _load_inputs() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("Program stdin must be a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise TypeError("Program stdin must contain an inputs object")
    return inputs


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stdout.write(json.dumps(run(_load_inputs()), ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
