"""Validate the todo-peer-contrast skill (dynamic layer 3: peer contrast + scale-up).

CEO 口径: peer 对比「不为主、天然可拉对比表」; 「体现好后, 带人的 scale up」是组织贡献
外溢。本文是这两件事的唯一口径: 产出必须是客观对比表与 scale-up 信号, 不是排名奖惩。
These tests guard the parts whose removal would silently turn the skill back into a ranking
engine: the two views (同级对比表 / 带人 scale-up), the factual-not-ranking ruling, the
same-mentor peer benchmark, the min-cycles sample discipline, the no-cross-group rule, and
the upstream integration points (#798 org-tree / #800 growth).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = WORKSPACE_ROOT / "skills"
TOOLS_DIR = WORKSPACE_ROOT / "tools"

SKILL = "todo-peer-contrast"


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


def _skill_text() -> str:
    return (SKILLS_DIR / SKILL / "SKILL.md").read_text(encoding="utf-8")


def _body() -> str:
    return _split_frontmatter(_skill_text())[1]


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
    for phrase in ("同级", "对比表", "带人", "LOAD"):
        assert phrase in description, f"description must contain the trigger phrase {phrase}"


def test_states_both_views() -> None:
    """动态三层 = 同级对比表 + 带人 scale-up 观察, 缺一个就不是完整口径."""
    body = _body()
    assert "同级对比表" in body, "view A (同级对比表) must be spelled out"
    assert "scale-up" in body, "view B (带人 scale-up) must be spelled out"


def test_contrast_is_factual_never_a_ranking() -> None:
    """CEO: peer 对比不为主; 产出是事实呈现, 不是排名奖惩."""
    body = _body()
    assert "不为主" in body, "must record that peer contrast is not the priority"
    assert "事实呈现" in body, "the table must be labelled factual presentation"
    assert "不排名奖惩" in body, "must forbid ranking / reward / punishment conclusions"


def test_peer_benchmark_is_same_mentor() -> None:
    body = _body()
    assert "same_level_by" in body, "must state how the peer benchmark is derived"
    assert "mentor" in body, "the current benchmark must be same-mentor"
    assert "严禁跨组" in body, "must forbid automatic cross-group contrast"
    assert "跨层" in body, "must forbid automatic cross-level contrast"


def test_sample_discipline_is_recorded() -> None:
    body = _body()
    assert "min_cycles" in body, "must reference the config min-cycles threshold"
    assert "样本不足" in body, "insufficient samples must be reported, not interpreted"


def test_scale_up_is_a_signal_not_a_verdict() -> None:
    body = _body()
    assert "客观信号" in body, "scale-up output must be objective signals"
    assert "拍板" in body or "上下文" in body, "scale-up judgement belongs to mentor/上级"


def test_notes_upstream_integration_points() -> None:
    body = _body()
    assert "#798" in body, "must note the org-tree integration point"
    assert "#800" in body, "must note the growth-profile integration point"


def test_judgment_criteria_live_in_config() -> None:
    body = _body()
    assert "config/todo-sop.yaml" in body, "must point the judgment criteria at config/todo-sop.yaml"
    assert "peer" in body, "must point at the config peer section"


def test_only_references_real_tools() -> None:
    real = _public_tool_names()
    assert "feishu_mentor_ledger_cycle_table" in real, "the cycle-table tool must exist"

    referenced = set(re.findall(r"\b(feishu_[a-z_]+|wiki_[a-z_]+)\b", _skill_text()))
    non_tools = {"feishu_context"}
    concrete = {n for n in referenced if not n.endswith("_") and n not in non_tools}
    unknown = concrete - real
    assert not unknown, f"skill references tool names that don't exist: {sorted(unknown)}"


def test_indexed_in_agents_md() -> None:
    agents = (WORKSPACE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert f"`{SKILL}`" in agents, f"{SKILL} must be listed in the AGENTS.md skills index"
