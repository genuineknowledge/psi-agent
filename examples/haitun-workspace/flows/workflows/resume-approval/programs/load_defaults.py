"""Load local standards, one configured role requirement, and Feishu destinations."""

from __future__ import annotations

import json
import os
import secrets
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

_REQUIRED_FEISHU_KEYS = (
    "app_token",
    "base_url",
    "talent_pool_table_id",
    "interview_table_id",
    "report_document_id",
    "user_key",
    "identity",
)
_REFERENCE_DOCUMENT_KEYS = (
    ("resume_scoring", "resume_scoring_document_url", "resume_scoring_document_token"),
    ("role_information", "role_information_document_url", "role_information_document_token"),
)
_REQUIRED_FEISHU_ENVIRONMENT_KEYS = (
    "PSI_FEISHU_APP_ID",
    "PSI_FEISHU_APP_SECRET",
)


def validate_reference_document_configuration(
    defaults: Mapping[str, Any], environment: Mapping[str, str]
) -> list[dict[str, str]]:
    """Validate two distinct Wiki nodes or Base document pages without credentials."""
    missing_credentials = [
        key
        for key in _REQUIRED_FEISHU_ENVIRONMENT_KEYS
        if not isinstance(environment.get(key), str) or not environment[key].strip()
    ]
    if missing_credentials:
        raise ValueError("Required Feishu environment variables are missing: " + ", ".join(missing_credentials))

    documents: list[dict[str, str]] = []
    source_tokens: set[str] = set()
    document_tokens: set[str] = set()
    for purpose, key, document_token_key in _REFERENCE_DOCUMENT_KEYS:
        configured_url = defaults.get(key)
        if not isinstance(configured_url, str) or not configured_url.strip():
            raise ValueError(f"{key} must be a non-empty HTTPS Wiki URL")
        configured_url = configured_url.strip()
        parsed = urlsplit(configured_url)
        path_parts = parsed.path.split("/")
        valid_path = len(path_parts) == 3 and path_parts[0] == "" and path_parts[1] == "wiki" and path_parts[2]
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or not valid_path
        ):
            raise ValueError(f"{key} must use https://<host>/wiki/<token> with an optional table=ldx... query")
        wiki_token = path_parts[2]
        if not all(character.isalnum() or character in "-_" for character in wiki_token):
            raise ValueError(f"{key} contains an invalid Wiki token")
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if query:
            if len(query) != 1 or query[0][0] != "table":
                raise ValueError(f"{key} must contain only the table=ldx... query")
            document_page_id = query[0][1]
            if not document_page_id.startswith("ldx") or not document_page_id[3:].isalnum():
                raise ValueError(f"{key} must reference a Base document page whose ID starts with ldx")
            document_token = defaults.get(document_token_key)
            if (
                not isinstance(document_token, str)
                or len(document_token.strip()) < 27
                or not all(character.isalnum() or character in "-_" for character in document_token.strip())
            ):
                raise ValueError(f"{document_token_key} must be a valid Docx token")
            document_token = document_token.strip()
            if document_page_id in source_tokens or document_token in document_tokens:
                raise ValueError("Reference document purposes must use distinct document pages and tokens")
            source_tokens.add(document_page_id)
            document_tokens.add(document_token)
            documents.append(
                {
                    "purpose": purpose,
                    "configured_url": configured_url,
                    "document_token": document_token,
                    "document_page_id": document_page_id,
                }
            )
        else:
            if wiki_token in source_tokens:
                raise ValueError("Reference document purposes must use distinct Wiki tokens")
            source_tokens.add(wiki_token)
            documents.append({"purpose": purpose, "configured_url": configured_url})
    return documents


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath((child, parent)) == parent
    except ValueError:
        return False


def _load_inputs() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("Program stdin must be a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict) or not isinstance(inputs.get("resume_files"), list):
        raise TypeError("Program inputs must contain resume_files as a list")
    if not inputs["resume_files"]:
        raise ValueError("At least one resume file is required")
    return inputs


def _load_json_file(workspace_root: str, relative_path: str) -> Any:
    path = os.path.realpath(os.path.join(workspace_root, relative_path))
    if not _inside(path, workspace_root) or not os.path.isfile(path):
        raise FileNotFoundError(f"Required local configuration is missing: {relative_path}")
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def _select_role(payload: Any, role_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("roles"), list):
        raise TypeError("role requirements must contain a roles list")
    matches = [role for role in payload["roles"] if isinstance(role, dict) and role.get("role_id") == role_id]
    if len(matches) != 1:
        raise ValueError(f"default_role_id must match exactly one role: {role_id}")
    role = matches[0]
    if role.get("status") != "active":
        raise ValueError(f"Configured role is not active: {role_id}")
    for key in ("role_id", "name", "location", "hard_requirements", "preferences"):
        if key not in role:
            raise ValueError(f"Role {role_id} is missing required field: {key}")
    enriched_role = dict(role)
    openings = role.get("openings")
    if isinstance(openings, list):
        enriched_openings: list[Any] = []
        for opening in openings:
            if not isinstance(opening, dict):
                enriched_openings.append(opening)
                continue
            opening_id = opening.get("role_id")
            enriched_opening = dict(opening)
            if isinstance(opening_id, str) and opening_id.strip():
                for source_name, output_name, item_name in (
                    ("requirements", "requirement_items", "requirement"),
                    ("preferences", "preference_items", "preference"),
                ):
                    configured_items = opening.get(source_name)
                    if isinstance(configured_items, list) and all(
                        isinstance(item, str) and item.strip() for item in configured_items
                    ):
                        enriched_opening[output_name] = [
                            {
                                "id": f"opening:{opening_id}:{item_name}:{index}",
                                "description": item,
                            }
                            for index, item in enumerate(configured_items, start=1)
                        ]
            enriched_openings.append(enriched_opening)
        enriched_role["openings"] = enriched_openings
    return enriched_role


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    del inputs
    resolved_workspace = os.path.realpath(workspace_root if workspace_root is not None else os.getcwd())
    bundle_dir = "flows/workflows/resume-approval"
    defaults = _load_json_file(resolved_workspace, f"{bundle_dir}/resume-approval.defaults.json")
    if not isinstance(defaults, dict):
        raise TypeError("resume-approval.defaults.json must be an object")
    reference_document_config = validate_reference_document_configuration(defaults, os.environ)

    role_file = defaults.get("role_requirements_file")
    role_id = defaults.get("default_role_id")
    if not all(isinstance(item, str) and item.strip() for item in (role_file, role_id)):
        raise ValueError("role_requirements_file and default_role_id must be non-empty text")
    role_payload = _load_json_file(resolved_workspace, role_file)
    target_role = _select_role(role_payload, role_id)

    feishu_config = defaults.get("feishu_config")
    if not isinstance(feishu_config, dict):
        raise TypeError("feishu_config must be an object")
    missing = [key for key in _REQUIRED_FEISHU_KEYS if not feishu_config.get(key)]
    if missing:
        raise ValueError(f"feishu_config is missing required values: {', '.join(missing)}")

    prefix = defaults.get("batch_prefix", "resume")
    if not isinstance(prefix, str) or not prefix.strip():
        raise ValueError("batch_prefix must be non-empty text")
    china_standard_time = timezone(timedelta(hours=8))
    timestamp = datetime.now(china_standard_time).strftime("%Y%m%d-%H%M%S")
    batch_id = f"{prefix.strip()}-{timestamp}-{secrets.token_hex(3)}"
    return {
        "target_role": target_role,
        "batch_id": batch_id,
        "feishu_config": feishu_config,
        "reference_document_config": reference_document_config,
    }


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
