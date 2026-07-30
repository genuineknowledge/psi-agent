"""Contract tests for the project-deep-learning skill.

The skill is intentionally a Markdown-only orchestration recipe.  These tests
guard the parts most likely to regress silently: discovery metadata, the
learning loop, evidence boundaries, and its integration with existing Haitun
capabilities.
"""

import importlib.util
import sys
from pathlib import Path

import anyio
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "project-deep-learning"
SKILL_PATH = WORKSPACE_ROOT / "skills" / SKILL_NAME / "SKILL.md"
SYSTEM_PATH = WORKSPACE_ROOT / "systems" / "system.py"

REQUIRED_TOOLS = {
    "read_document": "read_document.py",
    "feishu_docs_search": "feishu_docs.py",
    "feishu_doc_read": "feishu_doc.py",
    "feishu_wiki_list_spaces": "feishu_wiki.py",
    "feishu_wiki_list_nodes": "feishu_wiki.py",
    "feishu_wiki_get_node": "feishu_wiki.py",
    "memory_answer_context": "memory_answer_context.py",
    "memory_add": "memory_add.py",
    "schedule_manage": "schedule_manage.py",
}
REQUIRED_SKILLS = {
    "taskflow",
    "feishu-blocker-routing",
    "feishu-work-handoff-delegate",
    "feishu-mentor-feedback",
    "llm-wiki",
    "ontology",
    "feishu-schedule-message",
}


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    assert text.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
    _, frontmatter, body = text.split("---", 2)
    values: dict[str, str] = {}
    for raw_line in frontmatter.splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values, body


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_skill_exists_and_has_discoverable_frontmatter() -> None:
    assert SKILL_PATH.is_file()
    frontmatter, body = _split_frontmatter(_skill_text())
    assert frontmatter.get("name") == SKILL_NAME
    description = frontmatter.get("description", "").lower()
    assert description
    assert "project" in description
    assert any(term in description for term in ("deep", "master", "learning"))
    assert any(term in description for term in ("intern", "new hire", "employee"))
    assert body.strip()


def test_skill_defines_the_complete_deep_learning_loop() -> None:
    body = _split_frontmatter(_skill_text())[1]
    required_phrases = (
        "项目证据包",
        "业务网",
        "掌握度",
        "学习切片",
        "检验",
        "回补",
        "成长记录",
        "断奶",
    )
    for phrase in required_phrases:
        assert phrase in body, f"missing deep-learning phase: {phrase}"


def test_skill_distinguishes_evidence_from_inference_and_unknowns() -> None:
    body = _split_frontmatter(_skill_text())[1]
    for phrase in ("已验证事实", "合理推断", "待验证事项"):
        assert phrase in body
    assert "不得编造" in body
    assert "负责人" in body
    assert "WARN" in body
    assert "推导链" in body


def test_skill_covers_existing_haitun_capabilities() -> None:
    body = _split_frontmatter(_skill_text())[1]
    expected_references = (
        "feishu_docs_search",
        "feishu_doc_read",
        "feishu_wiki",
        "llm-wiki",
        "ontology",
        "taskflow",
        "memory_answer_context",
        "memory_add",
        "feishu-blocker-routing",
        "feishu-mentor-feedback",
    )
    for reference in expected_references:
        assert reference in body, f"missing integration reference: {reference}"


def test_referenced_tools_and_skills_exist() -> None:
    body = _split_frontmatter(_skill_text())[1]
    for tool_name, file_name in REQUIRED_TOOLS.items():
        tool_path = WORKSPACE_ROOT / "tools" / file_name
        assert tool_name in body
        assert tool_path.is_file(), f"missing tool module: {file_name}"
        assert f"async def {tool_name}(" in tool_path.read_text(encoding="utf-8")

    for skill_name in REQUIRED_SKILLS:
        assert skill_name in body
        assert (WORKSPACE_ROOT / "skills" / skill_name / "SKILL.md").is_file()


@pytest.mark.anyio
async def test_system_skill_index_discovers_the_skill(tmp_path: Path) -> None:
    """Exercise the real Haitun indexer without writing into the agent package."""
    systems_dir = SYSTEM_PATH.parent
    module_name = f"haitun_project_learning_index_{id(tmp_path)}"
    spec = importlib.util.spec_from_file_location(module_name, SYSTEM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(systems_dir))
    try:
        spec.loader.exec_module(module)
        isolated_workspace = tmp_path / "agent"
        isolated_skill_dir = isolated_workspace / "skills" / SKILL_NAME
        isolated_skill_dir.mkdir(parents=True)
        (isolated_skill_dir / "SKILL.md").write_text(_skill_text(), encoding="utf-8")
        module._GLOBAL_AGENT_SKILLS_DIR = anyio.Path(str(tmp_path / "no-global-skills"))
        skills_xml = await module._build_skills_index(anyio.Path(str(isolated_workspace)))
    finally:
        if sys.path and sys.path[0] == str(systems_dir):
            sys.path.pop(0)
        sys.modules.pop(module_name, None)

    assert f'<skill name="{SKILL_NAME}"' in skills_xml
    assert "Project deep-learning coach" in skills_xml


def test_skill_requires_confirmation_before_external_contact_or_writes() -> None:
    body = _split_frontmatter(_skill_text())[1]
    assert "建议联系" in body
    assert "主动发送" in body
    assert "明确确认" in body
    assert "原始项目资料" in body


def test_skill_guards_document_ingestion_and_delivery_completeness() -> None:
    body = _split_frontmatter(_skill_text())[1]
    for phrase in (
        "read_document",
        "不要用通用 `read`",
        "精确路径",
        "路径不得用 `…`",
        "迁移任务交付门禁",
        "不能替代任务正文",
        "`source_role`",
        "以项目契约对字段的精确定义为准",
    ):
        assert phrase in body


def test_skill_has_outcome_based_weaning_criteria() -> None:
    body = _split_frontmatter(_skill_text())[1]
    assert "独立解释" in body
    assert "独立分析" in body
    assert "独立完成" in body
    assert "依据" in body
    assert "风险" in body
    assert "待验证" in body


def test_skill_defines_stable_cross_session_artifact_templates() -> None:
    body = _split_frontmatter(_skill_text())[1]
    for artifact in (
        "overview.md",
        "business-network.md",
        "mastery.md",
        "learning-log.md",
    ):
        assert artifact in body
    for stable_field in (
        "learner_goal:",
        "证据状态",
        "可观察证据",
        "当前脚手架阶段",
        "下一项最小实战任务",
        "使用来源",
    ):
        assert stable_field in body
    assert "每轮追加" in body
    assert "不重写旧轮次" in body


def test_skill_includes_frontier_learning_and_enterprise_graph_mechanisms() -> None:
    body = _split_frontmatter(_skill_text())[1]
    for mechanism in (
        "证据卡",
        "权威性",
        "访问边界",
        "stale",
        "多跳路径",
        "基线任务",
        "置信度",
        "预期学习增益最大",
        "角色扮演",
        "间隔复习",
        "主动微学习",
        "迁移任务",
        "提示次数是否下降",
    ):
        assert mechanism in body
    assert "实际联系仍需用户明确确认" in body
    assert "无限通知授权" in body


def test_agents_catalog_exposes_the_skill_and_document_reader() -> None:
    agents = (WORKSPACE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "project-deep-learning" in agents
    assert "read_document" in agents
    assert "独立交付" in agents
