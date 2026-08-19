from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
LOADER_PATH = WORKFLOW_ROOT / "programs" / "load_defaults.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("resume_approval_load_defaults", LOADER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _credentials() -> dict[str, str]:
    return {
        "PSI_FEISHU_APP_ID": "test-app-id",
        "PSI_FEISHU_APP_SECRET": "test-app-secret",
    }


def test_public_defaults_example_defines_two_distinct_bitable_document_pages() -> None:
    """Removing a Base page or its Docx token must fail checked-in configuration validation."""
    loader = _load_module()
    defaults = json.loads((WORKFLOW_ROOT / "resume-approval.defaults.inputs.example.json").read_text(encoding="utf-8"))

    documents = loader.validate_reference_document_configuration(defaults, _credentials())

    assert documents == [
        {
            "purpose": "resume_scoring",
            "configured_url": defaults["resume_scoring_document_url"],
            "document_token": defaults["resume_scoring_document_token"],
            "document_page_id": defaults["resume_scoring_document_url"].split("table=", 1)[1],
        },
        {
            "purpose": "role_information",
            "configured_url": defaults["role_information_document_url"],
            "document_token": defaults["role_information_document_token"],
            "document_page_id": defaults["role_information_document_url"].split("table=", 1)[1],
        },
    ]


def test_public_role_example_matches_the_defaults_and_enriches_openings() -> None:
    """The deployable sample must remain synthetic while exercising opening enrichment."""
    loader = _load_module()
    defaults = json.loads((WORKFLOW_ROOT / "resume-approval.defaults.inputs.example.json").read_text(encoding="utf-8"))
    payload = json.loads((WORKFLOW_ROOT / "role-requirements.inputs.example.json").read_text(encoding="utf-8"))

    role = loader._select_role(payload, defaults["default_role_id"])

    assert role["role_id"] == "example-all-openings-v1"
    assert role["status"] == "active"
    assert len(role["openings"]) == 2
    engineer = role["openings"][0]
    assert engineer["requirement_items"][0]["id"].startswith("opening:example-software-engineer:requirement:")
    assert engineer["preference_items"][0]["id"].startswith("opening:example-software-engineer:preference:")
    serialized = json.dumps(payload, ensure_ascii=False).casefold()
    private_terms = ("zhen" + "zhi", "\u771f" + "\u77e5", "\u5408" + "\u80a5")
    assert all(term not in serialized for term in private_terms)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("resume_scoring_document_url", ""),
        ("resume_scoring_document_url", "http://example.feishu.cn/wiki/token"),
        ("resume_scoring_document_url", "https://example.feishu.cn/docx/token"),
        ("role_information_document_url", "https://example.feishu.cn/wiki/token?download=1"),
    ],
)
def test_document_configuration_rejects_missing_or_non_wiki_urls(key: str, value: str) -> None:
    """Weakening URL validation must not allow an empty or non-Wiki source into a batch."""
    loader = _load_module()
    defaults = {
        "resume_scoring_document_url": "https://example.feishu.cn/wiki/scoring-token",
        "role_information_document_url": "https://example.feishu.cn/wiki/roles-token",
        key: value,
    }

    with pytest.raises(ValueError, match=key):
        loader.validate_reference_document_configuration(defaults, _credentials())


def test_document_configuration_rejects_duplicate_wiki_tokens() -> None:
    """Removing the uniqueness check must not let one document satisfy both purposes."""
    loader = _load_module()
    defaults = {
        "resume_scoring_document_url": "https://example.feishu.cn/wiki/shared-token",
        "role_information_document_url": "https://another.feishu.cn/wiki/shared-token",
    }

    with pytest.raises(ValueError, match="distinct"):
        loader.validate_reference_document_configuration(defaults, _credentials())


def test_document_configuration_accepts_bitable_document_pages_with_explicit_docx_tokens() -> None:
    """Removing Base document-page support must not force the workflow back to standalone Wiki nodes."""
    loader = _load_module()
    defaults = {
        "resume_scoring_document_url": ("https://example.feishu.cn/wiki/base-wiki-token?table=ldxScoringPage001"),
        "resume_scoring_document_token": "ScoringDocxToken123456789012345",
        "role_information_document_url": ("https://example.feishu.cn/wiki/base-wiki-token?table=ldxRolePage000001"),
        "role_information_document_token": "RoleDocxToken123456789012345678",
    }

    assert loader.validate_reference_document_configuration(defaults, _credentials()) == [
        {
            "purpose": "resume_scoring",
            "configured_url": defaults["resume_scoring_document_url"],
            "document_token": defaults["resume_scoring_document_token"],
            "document_page_id": "ldxScoringPage001",
        },
        {
            "purpose": "role_information",
            "configured_url": defaults["role_information_document_url"],
            "document_token": defaults["role_information_document_token"],
            "document_page_id": "ldxRolePage000001",
        },
    ]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("resume_scoring_document_token", ""),
        ("resume_scoring_document_token", "too-short"),
        (
            "resume_scoring_document_url",
            "https://example.feishu.cn/wiki/base-wiki-token?table=tblNotADocumentPage",
        ),
        (
            "role_information_document_url",
            "https://example.feishu.cn/wiki/base-wiki-token?table=ldxRolePage000001&view=extra",
        ),
    ],
)
def test_bitable_document_page_configuration_rejects_unusable_sources(key: str, value: str) -> None:
    """Loosening page/token checks must not defer a deterministic configuration error to Feishu."""
    loader = _load_module()
    defaults = {
        "resume_scoring_document_url": ("https://example.feishu.cn/wiki/base-wiki-token?table=ldxScoringPage001"),
        "resume_scoring_document_token": "ScoringDocxToken123456789012345",
        "role_information_document_url": ("https://example.feishu.cn/wiki/base-wiki-token?table=ldxRolePage000001"),
        "role_information_document_token": "RoleDocxToken123456789012345678",
        key: value,
    }

    with pytest.raises(ValueError, match=key.replace("_token", "")):
        loader.validate_reference_document_configuration(defaults, _credentials())


@pytest.mark.parametrize("missing_key", ["PSI_FEISHU_APP_ID", "PSI_FEISHU_APP_SECRET"])
def test_document_configuration_requires_credentials_without_exposing_values(missing_key: str) -> None:
    """Bypassing credential checks must not defer a predictable failure to an API request."""
    loader = _load_module()
    defaults = {
        "resume_scoring_document_url": "https://example.feishu.cn/wiki/scoring-token",
        "role_information_document_url": "https://example.feishu.cn/wiki/roles-token",
    }
    credentials = _credentials()
    credentials[missing_key] = ""

    with pytest.raises(ValueError) as error:
        loader.validate_reference_document_configuration(defaults, credentials)

    assert missing_key in str(error.value)
    assert "test-app-id" not in str(error.value)
    assert "test-app-secret" not in str(error.value)


def test_defaults_loader_emits_safe_reference_configuration(tmp_path: Path, monkeypatch) -> None:
    """Reintroducing local standards must not make online-document loading workspace-dependent."""
    loader = _load_module()
    bundle = tmp_path / "flows" / "workflows" / "resume-approval"
    bundle.mkdir(parents=True)
    (tmp_path / "roles.json").write_text(
        json.dumps(
            {
                "roles": [
                    {
                        "role_id": "role-1",
                        "name": "测试岗位",
                        "location": "远程",
                        "hard_requirements": [],
                        "preferences": [],
                        "status": "active",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    defaults = {
        "role_requirements_file": "roles.json",
        "default_role_id": "role-1",
        "resume_scoring_document_url": "https://example.feishu.cn/wiki/scoring-token",
        "role_information_document_url": "https://example.feishu.cn/wiki/roles-token",
        "batch_prefix": "test",
        "feishu_config": {
            "app_token": "app",
            "base_url": "https://example.feishu.cn/base/app",
            "talent_pool_table_id": "talent",
            "interview_table_id": "interview",
            "report_document_id": "report",
            "user_key": "user",
            "identity": "bot",
        },
    }
    (bundle / "resume-approval.defaults.json").write_text(json.dumps(defaults, ensure_ascii=False), encoding="utf-8")
    for key, value in _credentials().items():
        monkeypatch.setenv(key, value)

    result = loader.run({}, str(tmp_path))

    assert "standard_files" not in result
    assert result["reference_document_config"] == [
        {
            "purpose": "resume_scoring",
            "configured_url": "https://example.feishu.cn/wiki/scoring-token",
        },
        {
            "purpose": "role_information",
            "configured_url": "https://example.feishu.cn/wiki/roles-token",
        },
    ]
