"""Validate the todo-alignment-check skill (static layer 3: alignment/value/outcome).

The skill is a pure SKILL.md rulebook (no new Python surface). It exists because
alignment and value are *relationship* questions that cannot be read off a TODO item:
a structurally-complete goal can still not align to its leader's goal, not name the
user value, or not carry an external outcome / competitor comparison — and only by
reconciling the claim against the org-tree leader goal, the group goal and the
acceptance state can that be judged. These tests guard the parts whose removal would
silently bring that back: the four-and-only-four verdicts, the claim-value
reconciliation, the A1-A6 SOP coverage, the reused acceptance-state machine, the
"not-aligned is a reminder, never 失实" rule, and the config-pending note.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = WORKSPACE_ROOT / "skills"
TOOLS_DIR = WORKSPACE_ROOT / "tools"

SKILL = "todo-alignment-check"

# The four alignment verdicts. The second uses a fullwidth solidus and the third
# fullwidth parentheses in the skill; they are built from their codepoints here so
# the literals stay unambiguous to ruff (RUF001) while still matching the skill text.
WATCH = "存疑" + chr(0xFF0F) + "待跟进"
UNALIGNED = "未对齐" + chr(0xFF08) + "价值/成果缺失" + chr(0xFF09)
VERDICTS = ("对齐", WATCH, UNALIGNED, "无法判定")


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    assert text.startswith("---\n"), "SKILL.md must start with a YAML frontmatter fence"
    end = text.index("\n---", 4)
    fm_block = text[4:end]
    body = text[end + 4 :]
    fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        if not line or line[0] in " \t":
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"')
    return fm, body


def _public_tool_names() -> set[str]:
    names: set[str] = set()
    for py in TOOLS_DIR.glob("*.py"):
        if py.name.startswith("_"):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and (
                node.name.startswith("feishu_") or node.name.startswith("wiki_")
            ):
                names.add(node.name)
    return names


def _skill_text(name: str = SKILL) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")


def _body(name: str = SKILL) -> str:
    return _split_frontmatter(_skill_text(name))[1]


def test_skill_file_exists() -> None:
    assert (SKILLS_DIR / SKILL / "SKILL.md").is_file(), f"missing skills/{SKILL}/SKILL.md"


def test_frontmatter_name_matches_dir_and_has_description() -> None:
    fm, body = _split_frontmatter(_skill_text())
    assert fm.get("name") == SKILL, "frontmatter name must equal dir name"
    assert fm.get("description", "").strip(), f"{SKILL} needs a non-empty description"
    assert fm.get("category", "").strip(), f"{SKILL} needs a category"
    assert body.strip(), f"{SKILL} needs a non-empty body"


def test_description_carries_the_trigger_phrases() -> None:
    description = _split_frontmatter(_skill_text())[0]["description"]
    for phrase in ("对齐", "用户价值", "外部成果", "验收", "LOAD"):
        assert phrase in description, f"description must contain the trigger phrase {phrase}"


def test_states_the_four_verdicts_and_forbids_inventing_others() -> None:
    body = _body()
    for verdict in VERDICTS:
        assert verdict in body, f"missing verdict {verdict}"
    assert "基本对齐" in body, "must explicitly forbid vague self-invented verdicts"


def test_states_the_dimensions_a1_to_a6() -> None:
    body = _body()
    for d in ("A1", "A2", "A3", "A4", "A5", "A6"):
        assert d in body, f"missing dimension {d}"
    for phrase in ("可对齐", "上级", "小组", "用户", "友商", "成果", "验收"):
        assert phrase in body, f"missing SOP-coverage phrase {phrase}"


def test_a5_uses_the_external_outcome_lexicon() -> None:
    """A5 must point at the config external-outcome lexicon and evidence terms."""
    body = _body()
    assert "external_outcome" in body, "A5 must reference the external_outcome config section"


def test_reuses_the_e1_e3_ladder() -> None:
    body = _body()
    for tier in ("E1", "E2", "E3"):
        assert tier in body, f"missing evidence tier {tier}"
    assert "completed_at" in body, "must cite E1 = the assignee's own completed_at"


def test_reuses_the_acceptance_state_machine() -> None:
    body = _body()
    assert "验收通过才算完成" in body, "must state the acceptance rule"
    assert "已申请验收" in body, "must spell out the acceptance state machine"
    assert "驳回" in body, "the acceptance state machine must include rejection"


def test_not_aligned_is_a_reminder_never_a_finding() -> None:
    body = _body()
    assert "未对齐" in body and "提示级结论" in body, "未对齐 must be a reminder, not a verdict of fault"
    assert "不判违规" in body, "未对齐 must not be a violation"
    assert "更不判失实" in body, "未对齐 must not be routed to truthfulness"


def test_judgment_criteria_live_in_config() -> None:
    body = _body()
    assert "config/todo-sop.yaml" in body, "must point the judgment criteria at config/todo-sop.yaml"
    assert "alignment" in body, "must point at the config alignment section"


def test_only_references_real_tools() -> None:
    real = _public_tool_names()
    assert "feishu_sync_org_tree" in real, "the org-tree tool must exist"
    assert "feishu_sheet_read" in real
    assert "wiki_read" in real

    referenced = set(re.findall(r"\b(feishu_[a-z_]+|wiki_[a-z_]+)\b", _skill_text()))
    non_tools = {"feishu_context"}
    concrete = {n for n in referenced if not n.endswith("_") and n not in non_tools}
    unknown = concrete - real
    assert not unknown, f"skill references tool names that don't exist: {sorted(unknown)}"


def test_indexed_in_agents_md() -> None:
    agents = (WORKSPACE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert f"`{SKILL}`" in agents, f"{SKILL} must be listed in the AGENTS.md skills index"
