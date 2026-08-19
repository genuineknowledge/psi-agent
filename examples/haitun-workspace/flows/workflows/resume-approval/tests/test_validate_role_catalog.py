from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path

PROGRAM_PATH = Path(__file__).resolve().parents[1] / "programs" / "validate_role_catalog.py"
ROLE_CONTENT = """招聘需求总览
四、全部岗位汇总
AI应用开发工程师
正式
1
五、共性要求
示例城市
构建 AI 应用
Python
自驱
"""


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_role_catalog", PROGRAM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reference_document(content: str = ROLE_CONTENT) -> dict:
    return {
        "purpose": "role_information",
        "configured_url": "https://example.feishu.cn/wiki/role-wiki",
        "wiki_token": "role-wiki",
        "document_token": "doc-role-token",
        "title": "招聘需求总览",
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "fetched_at": "2026-08-09T04:00:00+00:00",
    }


def _valid_role() -> dict:
    return {
        "name": "AI应用开发工程师",
        "employment_type": "正式",
        "location": "示例城市",
        "headcount": 1,
        "status": "active",
        "responsibilities": ["构建 AI 应用"],
        "hard_requirements": ["Python"],
        "preferences": ["自驱"],
        "source_evidence": [
            {"section": "四、全部岗位汇总", "text": "AI应用开发工程师"},
            {"section": "五、共性要求", "text": "示例城市"},
            {"section": "五、共性要求", "text": "构建 AI 应用"},
            {"section": "五、共性要求", "text": "Python"},
            {"section": "五、共性要求", "text": "自驱"},
        ],
    }


def _run(module, roles: list[dict], content: str = ROLE_CONTENT) -> dict:
    return module.run(
        {
            "role_catalog_draft": {"schema_version": "1.0", "roles": roles},
            "reference_documents": [_reference_document(content)],
        }
    )


def test_empty_role_catalog_is_blocked() -> None:
    """Allowing zero active source roles must not let resume matching start."""
    module = _load_module()

    result = _run(module, [])

    assert result["role_catalog"]["roles"] == []
    assert result["role_catalog_manifest"]["status"] == "blocked"
    assert result["role_catalog_manifest"]["errors"] == ["role_catalog.roles_must_be_non_empty"]


def test_duplicate_normalized_role_names_are_blocked() -> None:
    """Removing duplicate detection must not create two identities for one source role."""
    module = _load_module()
    duplicate = copy.deepcopy(_valid_role())
    duplicate["name"] = "  AI应用开发工程师  "

    result = _run(module, [_valid_role(), duplicate])

    assert result["role_catalog_manifest"]["status"] == "blocked"
    assert "roles[1].duplicate_role_name" in result["role_catalog_manifest"]["errors"]


def test_role_without_exact_source_evidence_is_blocked() -> None:
    """Dropping evidence validation must not allow an unauditable role into matching."""
    module = _load_module()
    role = _valid_role()
    role["source_evidence"] = []

    result = _run(module, [role])

    assert result["role_catalog_manifest"]["status"] == "blocked"
    assert "roles[0].source_evidence_must_be_non_empty" in result["role_catalog_manifest"]["errors"]


def test_table_row_reconstruction_is_blocked_but_cell_evidence_is_accepted() -> None:
    """Keep the evidence gate literal while making newline-separated tables usable."""
    module = _load_module()
    content = "\n".join(
        [
            "四、全部岗位汇总",
            "#",
            "岗位",
            "类型",
            "人数",
            "目标时间",
            "1",
            "AI应用开发工程师",
            "正式",
            "1",
            "9月中",
            "五、共性要求",
            "Python",
        ]
    )
    row_reconstructed = _valid_role()
    row_reconstructed["source_evidence"] = [
        {"section": "四、全部岗位汇总", "text": "AI应用开发工程师 | 正式 | 1 | 9月中"},
        {"section": "五、共性要求", "text": "Python"},
    ]
    blocked = _run(module, [row_reconstructed], content)
    assert blocked["role_catalog_manifest"]["status"] == "blocked"
    assert "roles[0].source_evidence[0].text_not_in_source" in blocked["role_catalog_manifest"]["errors"]

    cell_evidence = _valid_role()
    cell_evidence.update(
        {
            "employment_type": "正式",
            "location": "未说明",
            "responsibilities": [],
            "hard_requirements": ["Python"],
            "preferences": [],
            "source_evidence": [
                {"section": "四、全部岗位汇总", "text": "AI应用开发工程师"},
                {"section": "四、全部岗位汇总", "text": "正式"},
                {"section": "四、全部岗位汇总", "text": "1"},
                {"section": "四、全部岗位汇总", "text": "9月中"},
                {"section": "五、共性要求", "text": "Python"},
            ],
        }
    )
    accepted = _run(module, [cell_evidence], content)
    assert accepted["role_catalog_manifest"]["status"] == "complete"


def test_inactive_source_role_cannot_be_marked_active() -> None:
    """Ignoring an explicit pause marker must not reactivate a closed position."""
    module = _load_module()
    content = ROLE_CONTENT.replace("AI应用开发工程师", "AI应用开发工程师\uff08暂停招聘\uff09")
    role = _valid_role()
    role["source_evidence"][0]["text"] = "AI应用开发工程师\uff08暂停招聘\uff09"

    result = _run(module, [role], content)

    assert result["role_catalog_manifest"]["status"] == "blocked"
    assert "roles[0].status_conflicts_with_source" in result["role_catalog_manifest"]["errors"]


def test_fabricated_role_name_is_blocked_even_when_other_evidence_is_real() -> None:
    """Removing the role-name source check must not let the model invent a position."""
    module = _load_module()
    role = _valid_role()
    role["name"] = "量子销售架构师"

    result = _run(module, [role])

    assert result["role_catalog_manifest"]["status"] == "blocked"
    assert "roles[0].name_not_in_source" in result["role_catalog_manifest"]["errors"]


def test_historical_candidate_matching_cannot_be_used_as_role_evidence() -> None:
    """Using a talent-matching example must not turn a candidate case into role evidence."""
    module = _load_module()
    content = ROLE_CONTENT + "六、人才库匹配\uff08待招5人\uff09\nAI应用开发工程师\n示例候选人\n89\n"
    role = _valid_role()
    role["source_evidence"][0]["section"] = "六、人才库匹配\uff08待招5人\uff09"

    result = _run(module, [role], content)

    assert result["role_catalog_manifest"]["status"] == "blocked"
    assert "roles[0].historical_candidate_evidence_forbidden" in result["role_catalog_manifest"]["errors"]


def test_valid_catalog_gets_deterministic_role_identity_and_source_revision() -> None:
    """Changing role identity or source revision must break the validated runtime contract."""
    module = _load_module()
    source_sha256 = "fe4368fa04674e0e8929d7a707be62a7e5bb4d5bd8a44d8d2656fd2976105704"

    result = _run(module, [_valid_role()])

    assert result["role_catalog_manifest"] == {
        "schema_version": "1.0",
        "status": "complete",
        "active_role_count": 1,
        "errors": [],
    }
    assert result["role_catalog"] == {
        "schema_version": "1.0",
        "source_document_sha256": source_sha256,
        "roles": [
            {
                "role_key": "role-77b511063f41c943b3331fae",
                **_valid_role(),
            }
        ],
    }
