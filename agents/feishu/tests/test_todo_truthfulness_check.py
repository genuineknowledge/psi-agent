"""Validate the todo-truthfulness-check skill (static layer 2: the truthfulness red line).

The skill is a pure SKILL.md rulebook (no new Python surface). It exists because
truthfulness cannot be read off the words of a TODO item: "已完成" can be a real
delivery or something that never happened, and the only way to tell them apart is to
reconcile the claim against task-system progress and acceptance records (TPMF's
「方案执行与验收」loop). These tests guard the parts whose removal would silently
bring that back: the four-and-only-four truthfulness verdicts, the claim-fact
reconciliation reusing the E1-E3 ladder, the acceptance-state machine (验收通过才算
完成), the D1-D6 SOP coverage, the red-line rule that 失实 needs human confirmation,
and the back-pointers from the three sibling skills into it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = WORKSPACE_ROOT / "skills"
TOOLS_DIR = WORKSPACE_ROOT / "tools"

SKILL = "todo-truthfulness-check"

# The four truthfulness verdicts. The second one uses a fullwidth solidus and the
# third fullwidth parentheses in the skill; they are built from their codepoints
# here so the literals stay unambiguous to ruff (RUF001) while still matching the
# skill text byte for byte.
WATCH = "存疑" + chr(0xFF0F) + "待跟进"
SUSPECT = "失实" + chr(0xFF08) + "涉嫌弄虚作假" + chr(0xFF09)
VERDICTS = ("属实", WATCH, SUSPECT, "无法跟进")


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter dict, body). Only top-level ``key: value`` lines are parsed."""
    assert text.startswith("---\n"), "SKILL.md must start with a YAML frontmatter fence"
    end = text.index("\n---", 4)
    fm_block = text[4:end]
    body = text[end + 4 :]
    fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        if not line or line[0] in " \t":  # skip blanks and continuation/indented lines
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"')
    return fm, body


def _public_tool_names() -> set[str]:
    """Collect public async tool function names (feishu_*/wiki_*) from tools/*.py via AST."""
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
    """Discovery is the whole game: an unloaded rulebook enforces nothing."""
    description = _split_frontmatter(_skill_text())[0]["description"]
    for phrase in ("实事求是", "弄虚作假", "LOAD"):
        assert phrase in description, f"description must contain the trigger phrase {phrase}"


def test_states_the_four_verdicts_and_forbids_inventing_others() -> None:
    body = _body()
    for verdict in VERDICTS:
        assert verdict in body, f"missing verdict {verdict}"
    # the escape hatch that must stay closed
    assert "基本属实" in body, "must explicitly forbid vague self-invented verdicts"


def test_red_line_verdict_requires_human_confirmation() -> None:
    """失实 is a red-line finding: the machine only reports 涉嫌失实 + evidence."""
    body = _body()
    assert "人工确认" in body, "must require human confirmation for the red line"
    ruling = next(
        (block for block in body.split("\n## ") if "失实" in block and "人工确认" in block),
        "",
    )
    assert ruling, "the human-confirmation rule must live in a 失实 section"
    assert "涉嫌失实" in ruling, "the machine may only report suspicion, never the verdict alone"


def test_states_the_claim_fact_ladder_reusing_e1_e3() -> None:
    body = _body()
    for tier in ("E1", "E2", "E3"):
        assert tier in body, f"missing evidence tier {tier}"
    # E1 is the assignee's own completed_at — task.status is the classic wrong read
    assert "completed_at" in body
    assert "assignee_related" in body
    assert "task.status" in body, "must warn that task.status is not per-assignee completion"


def test_acceptance_comes_before_completion_in_both_skills() -> None:
    """SOP: 验收过了才算完成 — completion and truthfulness must both say it."""
    for name in (SKILL, "todo-completion-standard"):
        body = _body(name)
        assert "验收通过才算完成" in body, f"{name} must state the acceptance rule"
    body = _body()
    assert "已申请验收" in body, "must spell out the acceptance state machine"
    assert "驳回" in body, "the acceptance state machine must include rejection"


def test_dimensions_cover_the_sop_notes() -> None:
    """D1-D6 must be present and every SOP 注意事项 must have a home dimension."""
    body = _body()
    for d in ("D1", "D2", "D3", "D4", "D5", "D6"):
        assert d in body, f"missing dimension {d}"
    for phrase in (
        "可跟进",  # D1: 每条 TODO 必须有可核对载体
        "过去的时间点",  # D2: SOP「都不应该有过去的时间点」
        "进度",  # D3: 声明 vs 最新进度
        "一致性",  # D4: 前后一致性
        "用户",  # D5: 以用户为中心
        "友商",  # D5: TPMF 小组长突出友商比较
        "闭环",  # D5: TPMF 闭环
        "小组",  # D6: 个人与小组对齐
        "子目标",  # D6: 粒度过大拆子目标
    ):
        assert phrase in body, f"missing SOP-coverage phrase {phrase}"


def test_progress_tracking_linkage_is_spelled_out() -> None:
    """Truthfulness cannot be judged without following task progress (方案执行与验收)."""
    body = _body()
    assert "方案执行与验收" in body, "must name the linked TPMF module"
    assert "强制取证" in body, "completed claims must force the E1→E2→E3 chain"
    assert "进行中" in body, "in-progress claims must be checked against task state"


def test_rules_keep_unfill_and_vanishing_out_of_truthfulness() -> None:
    """缺写 and 消失 are not evidence of a lie — separating them is the anti-double-standard."""
    body = _body()
    assert "缺写 ≠ 失实" in body, "never-filling is not evidence of a fabricated item"
    assert "消失 ≠ 失实" in body, "vanishing is not evidence of a fabricated item"


def test_copy_paste_similarity_never_rules_fabrication() -> None:
    body = _body()
    assert "feishu_text_similarity" in body, "the skill must drive the similarity tool"
    assert "不判失实" in body, "a similarity hit must only go to 存疑, never to 失实"
    assert "matched_fragment" in body, "the hit must carry the matched text as evidence"
    assert "feishu_text_similarity" in _public_tool_names(), "the tool must actually exist"


def test_only_references_real_tools() -> None:
    real = _public_tool_names()
    # sanity: the collector actually found the toolset
    assert "feishu_doc_read" in real
    assert "feishu_sheet_read" in real

    referenced = set(re.findall(r"\b(feishu_[a-z_]+|wiki_[a-z_]+)\b", _skill_text()))
    non_tools = {"feishu_context"}
    concrete = {n for n in referenced if not n.endswith("_") and n not in non_tools}
    unknown = concrete - real
    assert not unknown, f"skill references tool names that don't exist: {sorted(unknown)}"


def test_sibling_skills_point_here_for_truthfulness() -> None:
    """Completion, fill-check and board-sync must route truthfulness questions here."""
    for name in ("todo-completion-standard", "company-todo-fill-check", "feishu-todo-board-sync"):
        body = _body(name)
        assert SKILL in body, f"{name} must point truthfulness questions to {SKILL}"
    completion = _body("todo-completion-standard")
    assert "验收状态" in completion, "completion standard must check acceptance state first"
    fill = _body("company-todo-fill-check")
    assert "缺写 ≠ 失实" in fill, "fill-check must keep 缺写 out of truthfulness verdicts"
    board = _body("feishu-todo-board-sync")
    assert "跟踪线索" in board, "board-sync must keep acceptance/task-link clues when copying"


def test_indexed_in_agents_md() -> None:
    agents = (WORKSPACE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert f"`{SKILL}`" in agents, f"{SKILL} must be listed in the AGENTS.md skills index"
