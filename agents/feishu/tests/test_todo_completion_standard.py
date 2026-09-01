"""Validate the todo-completion-standard skill (the single standard for judging "is it done").

The skill is a pure SKILL.md rulebook (no new Python surface). It exists because the
same batch of people once got judged by two different yardsticks: one person's item
vanishing from later reports was read as "done", another person's identical vanishing
was read as "never closed" — only because the first got checked against the task
system and the second did not.

These tests guard exactly the parts whose removal would silently bring that back:
the four-and-only-four verdicts, the E1-E3 evidence ladder, the vanishing ruling, the
symmetry rule, and the back-pointers from the two upstream skills that route into it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = WORKSPACE_ROOT / "skills"
TOOLS_DIR = WORKSPACE_ROOT / "tools"

SKILL = "todo-completion-standard"

# The four verdicts. Any other wording ("基本完成", "应该做完了") is where double
# standards hide, so the skill must name these and forbid the rest.
# The skill spells the middle verdict with a fullwidth solidus; it is built from its
# codepoint here so the literal stays unambiguous to ruff (RUF001) while still matching
# the skill text byte for byte.
INFERRED = "推断已完成" + chr(0xFF0F) + "待确认"
VERDICTS = ("已完成", INFERRED, "进行中", "未闭环")


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
    assert "完成情况" in description, "description must contain the natural trigger phrase"
    for phrase in ("LOAD", "闭环"):
        assert phrase in description, f"description must mention {phrase}"


def test_states_the_four_verdicts_and_forbids_inventing_others() -> None:
    body = _body()
    for verdict in VERDICTS:
        assert verdict in body, f"missing verdict {verdict}"
    # the escape hatch that must stay closed
    assert "基本完成" in body, "must explicitly forbid vague self-invented verdicts"


def test_vanishing_from_later_reports_is_one_ruling_for_everyone() -> None:
    """The exact conflict this skill was written to settle.

    Vanishing may not be promoted to 已完成 (no hard evidence) nor demoted to 未闭环
    (vanishing is a real signal), and the verdict may not differ between people in
    one batch.
    """
    body = _body()
    assert "消失" in body, "must address what a vanished item means"
    # the ruling and the signal have to live in the same section, or the ruling can be
    # edited away while the words survive elsewhere
    ruling = next(
        (block for block in body.split("\n## ") if "消失" in block and "推断已完成" in block),
        "",
    )
    assert ruling, f"the vanishing ruling must tie 消失 to {INFERRED} in one section"
    assert "一律" in ruling, "the ruling must state it applies to everyone alike"


def test_states_the_e1_e3_evidence_ladder() -> None:
    body = _body()
    for tier in ("E1", "E2", "E3"):
        assert tier in body, f"missing evidence tier {tier}"
    # E1 is the assignee's own completed_at — task.status is the classic wrong read
    assert "completed_at" in body
    assert "assignee_related" in body
    assert "task.status" in body, "must warn that task.status is not per-assignee completion"


def test_symmetry_rule_and_unavailable_downgrade() -> None:
    """Asymmetric evidence depth is the mechanism behind the original double standard."""
    body = _body()
    assert "unavailable" in body, "must define what to do when an evidence tier is missing"
    assert "同一条链" in body or "同一条取证链" in body, "must require the same ladder per person"
    assert "同一深度" in body, "a missing tier must downgrade the whole batch, not one person"


def test_prior_impression_is_excluded_from_evidence() -> None:
    body = _body()
    assert "自评" in body, "self-assessment must be named as a non-evidence input"
    assert "背景" in body, "prior impressions belong in a background section, not the verdict"


def test_report_format_requires_verdict_tier_and_source() -> None:
    body = _body()
    assert "证据等级" in body, "each reported row must carry its evidence tier"
    assert "依据" in body, "each reported row must carry where the evidence came from"
    assert "还缺什么" in body, "the report must close with what is missing to conclude"


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


def test_separates_vanishing_from_never_filled() -> None:
    """A blank cell reads two ways, and confusing them is the heaviest misjudgement.

    An entire period left blank is a fill-check question (it may be approved leave);
    only an item missing from an otherwise-filled report is a "vanishing". Reading a
    person's leave-blank period as "nothing closed" is the failure this guards.
    """
    body = _body()
    heading = "### 消失 vs 没填"
    assert heading in body, "the standard must separate 消失 from 没填 in its own section"
    # take just that section: from its heading to the next one at the same or higher level
    rest = body.split(heading, 1)[1]
    section = re.split(r"\n#{2,3} ", rest, maxsplit=1)[0]
    assert "company-todo-fill-check" in section, "must route whole-period blanks to fill-check"
    assert "顺序" in section, "must state fill-check comes before judging a vanished item"


def test_fill_check_hands_completion_questions_over() -> None:
    """The two skills must know about each other, or the seam between them leaks."""
    fill_check = _body("company-todo-fill-check")
    assert SKILL in fill_check, "company-todo-fill-check must defer completion verdicts here"
    assert INFERRED in fill_check, "it must name the verdict a vanished item gets"
    # a missing report entry is not evidence about whether the work got done
    assert "缺写" in fill_check


def test_upstream_skills_point_here() -> None:
    """Both entry points must route into the standard instead of ruling on their own."""
    board = _body("feishu-todo-board-sync")
    assert SKILL in board, "feishu-todo-board-sync must defer completion verdicts to the standard"
    assert "只" in board and "搬运" in board, "board-sync must say it does not judge completion"

    task = _body("feishu-task")
    assert SKILL in task, "feishu-task must point at the standard for verdicts"
    assert "E1" in task, "feishu-task must mark completed_at as the E1 hard evidence"


def test_indexed_in_agents_md() -> None:
    agents = (WORKSPACE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert f"`{SKILL}`" in agents, f"{SKILL} must be listed in the AGENTS.md skills index"
