"""Validate the todo-writing-standard skill (the TODO filing standard as configuration).

The skill is a pure SKILL.md rulebook (no new Python surface). Its whole value claim is
that the standard and the execution loop are *physically separated* into five sections,
so that adapting to another company means replacing definitions only — the engine
section stays byte-identical and no new skill gets stacked on.

That claim is only worth something if the separation is actually enforced, so these
tests guard exactly the parts whose removal would silently turn the skill back into a
hardcoded one-off:

- the five sections all exist;
- the engine section contains no concrete level names, numbers, column names or tokens
  (the single assertion that keeps "replace the params, behaviour changes" true);
- the five discipline rules each carry all five required fields;
- 按时 defers to company-todo-fill-check instead of re-deriving the leave-check order;
- mentor check is only ever reminded, never stamped on the member's behalf;
- 生成 → 成员确认 → 写表 survives, and 澄清 forbids inventing missing acceptance data;
- the neighbouring skills route into and out of this one.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = WORKSPACE_ROOT / "skills"
TOOLS_DIR = WORKSPACE_ROOT / "tools"

SKILL = "todo-writing-standard"

# The five sections. The whole extensibility claim rests on these being separate places
# in the document, so their headings are part of the contract.
SECTIONS = ("内容 schema 段", "规则集段", "载体段", "参数段", "引擎段")

# The five discipline rules, and the five fields each rule row must carry.
RULES = ("按时", "按质", "按量", "按优先级", "mentor check")
RULE_FIELDS = ("触发条件", "判定对象", "判定逻辑", "违规文案", "报告去向")

# Params that must live in the params section (and nowhere else, see the engine test).
PARAMS = ("5 条", "24h", "14:30", "14:50")


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
    """Collect public async tool function names from tools/*.py via AST."""
    names: set[str] = set()
    for py in TOOLS_DIR.glob("*.py"):
        if py.name.startswith("_"):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef):
                names.add(node.name)
    return names


def _skill_text(name: str = SKILL) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")


def _body(name: str = SKILL) -> str:
    return _split_frontmatter(_skill_text(name))[1]


def _section(heading_fragment: str, *, body: str | None = None) -> str:
    """Return one ``##`` section's text, located by a fragment of its heading.

    Includes any ``###`` subsections, and stops at the next ``##``.
    """
    text = _body() if body is None else body
    blocks = text.split("\n## ")
    match = next((b for b in blocks if b.splitlines()[0].startswith(heading_fragment)), "")
    assert match, f"missing section heading starting with {heading_fragment!r}"
    return match


def _subsection(heading_fragment: str, *, within: str) -> str:
    """Return one ``###`` subsection's text, located by a fragment of its heading.

    Matching on the *heading line* rather than anywhere in the block matters: the
    parent section usually opens with a summary table that mentions every subsection's
    keyword, so a substring search would happily return that table instead and the
    assertion would pass without the subsection existing at all.
    """
    blocks = within.split("\n### ")
    match = next((b for b in blocks[1:] if b.splitlines()[0].startswith(heading_fragment)), "")
    assert match, f"missing subsection heading starting with {heading_fragment!r}"
    return match


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
    for phrase in ("规范", "检查", "Use when"):
        assert phrase in description, f"description must mention {phrase}"
    # the two neighbours it must not swallow
    for neighbour in ("company-todo-fill-check", "todo-completion-standard"):
        assert neighbour in description, f"description must route {neighbour} away"


def test_all_five_sections_exist() -> None:
    body = _body()
    for section in SECTIONS:
        assert section in body, f"missing the {section}"
    # each must be a real heading, not just a mention in prose
    headings = re.findall(r"^## (.+)$", body, flags=re.MULTILINE)
    for section in SECTIONS:
        assert any(h.startswith(section) for h in headings), f"{section} must be its own ## section"


def test_engine_section_hardcodes_nothing_replaceable() -> None:
    """The one assertion that keeps the extensibility claim honest.

    If a level name, a quota, a time point or a column name leaks into the engine
    section, then editing the params section stops changing behaviour — and that is
    exactly the failure mode ("changed the number, nothing happened") this skill
    exists to prevent. The engine may only *refer* to the other sections.
    """
    engine = _section("引擎段")
    leaks = [*PARAMS, "大目标", "小目标", "SHEET_ID"]
    for leak in leaks:
        assert leak not in engine, f"engine section must not hardcode {leak!r}; refer to the section"
    # and it must actually refer to them instead
    for reference in ("schema 段", "参数段", "载体段", "规则集段"):
        assert reference in engine, f"engine section must defer to the {reference}"


def test_engine_section_declares_itself_unchanged_on_adaptation() -> None:
    engine = _section("引擎段")
    assert "一字不动" in engine, "engine section must state it stays byte-identical"


def test_extensibility_yardstick_is_stated() -> None:
    """Replacing definitions must not mean stacking another skill — the stated test."""
    block = _subsection("当前是哪一份 schema", within=_section("五段结构"))
    assert "小方案" in block, "must name the already-decided evolution (TODO → 小方案)"
    assert "不新增 skills" in block or "不新增 skill" in block, (
        "the yardstick must be: no new skill gets added when the content structure changes"
    )


def test_each_discipline_rule_carries_all_five_fields() -> None:
    """A rule missing 报告去向 or 违规文案 silently becomes unenforceable."""
    rules_section = _section("规则集段")
    for field in RULE_FIELDS:
        assert field in rules_section, f"the rule shape must name {field}"
    for rule in RULES:
        assert rule in rules_section, f"missing discipline rule {rule}"


def test_rules_judge_the_board_and_nothing_else() -> None:
    rules_section = _section("规则集段")
    assert "判定对象全部是看板表" in rules_section, "all rules must be judged against the board"


def test_shizhi_rule_defers_the_leave_check_instead_of_restating_it() -> None:
    """Re-deriving the leave order here is how the two skills drift apart.

    company-todo-fill-check owns the 查假 order; this skill must point at it and map its
    three outcomes onto 违规 / 不违规 / 不判违规, not grow a second copy of the rule.
    """
    rules_section = _section("规则集段")
    assert "company-todo-fill-check" in rules_section, "按时 must defer to the fill-check skill"
    for outcome in ("缺写", "请假免填", "待人工确认"):
        assert outcome in rules_section, f"must map the fill-check outcome {outcome}"
    # the direction of the classic failure has to stay written down
    assert "加重考核" in rules_section, "must record that judging before checking leave over-penalises"


def test_mentor_check_is_reminded_never_stamped() -> None:
    """Stamping check on the mentor's behalf turns review into a rubber stamp, invisibly."""
    body = _body()
    assert "不代 check" in body or "不代替 mentor" in body, "must forbid stamping check for a mentor"
    rules_section = _section("规则集段")
    assert "只提醒 mentor" in rules_section, "mentor check overdue must only remind the mentor"


def test_mentor_check_marker_is_the_board_column_only() -> None:
    """The marker form was pinned by the user: a column on the board, not chat talk."""
    body = _body()
    assert "mentor check 列" in body, "the check marker must be a dedicated board column"


def test_counting_uncertainty_reports_instead_of_guessing() -> None:
    """按量 is the flagged unreliable-by-model rule; guessing a count is the failure."""
    block = _subsection("按量", within=_section("规则集段"))
    assert "待人工确认" in block, "an uncertain count must be reported, not guessed"


def test_on_demand_sinking_is_per_rule_and_not_prebuilt() -> None:
    block = _subsection("按需下沉", within=_section("五段结构"))
    assert "不预先" in block, "sinking must be on demand, not pre-built"
    assert "参数段" in block or "规则集段" in block, (
        "a sunk rule must still read its params from the definition sections"
    )


def test_priority_rule_pins_the_four_quadrant_order() -> None:
    """SOP 总则: TODO list 按优先级排序(重要且紧急 > 重要不紧急 > 紧急不重要 > 不重要不紧急)。"""
    params = _section("参数段")
    for quadrant in ("重要且紧急", "重要不紧急", "紧急不重要", "不重要不紧急"):
        assert quadrant in params, f"params must pin the quadrant {quadrant}"
    block = _subsection("按优先级", within=_section("规则集段"))
    assert "待人工确认" in block, "an uncertain ordering must be reported, not guessed"
    assert "金字塔" in block, "must note the 70/25/5 pyramid is not yet in scope"


def test_priority_importance_and_consistency_check_are_wired_in() -> None:
    """SOP v1.4: importance by external-outcome tier; a TODO's priority must match its goal's."""
    body = _body()
    assert "priority.importance" in body, "importance judgment must read config priority.importance"
    assert "urgency_trap" in body, "urgency must be judged against config urgency_trap"
    block = _subsection("按优先级", within=_section("规则集段"))
    assert "一致性" in block, "the rule must check todo-goal importance consistency"
    assert "待人工确认" in block, "an uncertain consistency verdict must go to human confirmation"


def test_params_section_pins_the_confirmed_values() -> None:
    params = _section("参数段")
    for value in PARAMS:
        assert value in params, f"params section must pin {value}"
    assert "私聊" in params, "report delivery (私聊) was pinned by the user"


def test_schedules_use_the_existing_tool_with_prompt_fire() -> None:
    """fire=tool skips the LLM, but both jobs need to read the board first."""
    params = _section("参数段")
    assert "schedule_manage" in params, "the two jobs must use the existing schedule_manage tool"
    assert "fire=prompt" in params, "both jobs must fire as prompt (they need the model)"
    assert "fire=tool" in params, "must explain why fire=tool does not fit"
    assert "1,3,5" in params, "cron must encode the Mon/Wed/Fri rhythm"


def test_named_tools_all_exist() -> None:
    """A skill naming a tool that does not exist fails at runtime, not at review."""
    body = _body()
    available = _public_tool_names()
    for tool in ("feishu_sheet_read", "feishu_sheet_write", "feishu_leave_query", "schedule_manage"):
        assert tool in body, f"skill must name the {tool} tool it drives"
        assert tool in available, f"{tool} is named by the skill but missing from tools/"


def test_help_writing_asks_instead_of_inventing() -> None:
    """Inventing an acceptance standard is invisible to the member who confirms it."""
    engine = _section("引擎段")
    assert "澄清" in engine, "help-writing must have a clarify step"
    assert "不瞎编" in engine, "the clarify step must forbid inventing missing information"


def test_generate_confirm_write_is_unconditional() -> None:
    engine = _section("引擎段")
    assert "成员确认" in engine, "writing must go through member confirmation"
    assert "没有例外" in engine, "the confirm-before-write rule must be stated as absolute"


def test_report_has_both_views_and_every_violation_cites_evidence() -> None:
    engine = _section("引擎段")
    assert "boss" in engine and "mentor 视图" in engine, "the report needs both views"
    assert "带依据" in engine, "every violation must carry its evidence"


def test_remediation_covers_the_three_violation_types() -> None:
    engine = _section("引擎段")
    block = _subsection("补救", within=engine)
    assert "不改表" in block, "over-quota must yield advice only, never a silent table edit"


def test_boundary_keeps_filing_apart_from_completion() -> None:
    """Reading a missing entry as "nothing got done" is the cross-skill failure."""
    body = _body()
    assert "填报缺失不是完成度证据" in body, "must forbid using a missing entry as completion evidence"
    assert "todo-completion-standard" in body, "must route completion questions away"


def test_separates_itself_from_the_todo_management_system() -> None:
    """The filing layer and the delivery layer share one board — conflating them misjudges.

    Both this skill and the company-todo-* trio act on the same board in the same cycle.
    Two specific collisions have to stay written down, because each one silently produces
    a wrong verdict rather than an error:

    - mentor's sign-off exists in two places (this skill's board column vs the trio's
      ledger 打分/评语). Reading one as the other means judging 「写得对不对」 by
      「活干得怎么样」, or vice versa.
    - the trio's audit fires at the same minute as this skill's reminder.
    """
    section = _section("与 TODO 管理体系的分工")
    for skill in ("company-todo-sync", "company-todo-audit", "company-todo-review"):
        assert skill in section, f"must delimit itself against {skill}"
    # the two-layer split: filing side vs execution side
    assert "填报侧" in section and "执行侧" in section, "must name which side each layer owns"
    # neither direction of the equivalence may be asserted
    assert "填得规范不等于做完了" in section, "format compliance is not delivery"
    # the mentor marker lives in two places; conflating them is the silent misread
    assert "mentor check 列" in section and "台账" in section, (
        "must keep the board check column apart from the ledger score/comment"
    )


def test_reminder_schedule_collision_with_audit_is_recorded() -> None:
    """Same cron minute as todo-cycle-audit: both wake the session onto the same board."""
    body = _body()
    assert "30 14 * * 1,3,5" in body, "must record the colliding cron"
    assert "company-todo-audit" in body, "must name the skill it collides with"
    params = _section("参数段")
    assert 'schedule_manage(action="list")' in params or 'action="list"' in params, (
        "must tell the reader to check for the existing schedule before creating"
    )


def test_card_dsl_is_cited_as_the_same_engine_idea() -> None:
    """card-dsl is now in main, so the shared 「vocabulary + engine」 claim is checkable."""
    block = _subsection("当前是哪一份 schema", within=_section("五段结构"))
    assert "card-dsl" in block, "must cite card-dsl as the same engine/definition split"


def test_neighbour_skills_route_into_this_one() -> None:
    """Both adjacent entry points must know this skill exists, or the seam leaks."""
    fill_check = _body("company-todo-fill-check")
    assert SKILL in fill_check, "company-todo-fill-check must route format questions here"

    board = _body("feishu-todo-board-sync")
    assert SKILL in board, "feishu-todo-board-sync must route help-writing here"

    completion = _body("todo-completion-standard")
    assert SKILL in completion, "todo-completion-standard must distinguish itself from this skill"


def test_completion_standard_states_the_two_are_independent() -> None:
    """Format compliance and completion are orthogonal; conflating them was the risk."""
    completion = _body("todo-completion-standard")
    block = next((b for b in completion.split("\n## ") if SKILL in b), "")
    assert block, f"{SKILL} must be mentioned in a routing section"
    assert "完成度" in block, "must say format compliance is not completion evidence"


def test_indexed_in_agents_md() -> None:
    agents = (WORKSPACE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert f"`{SKILL}`" in agents, f"{SKILL} must be listed in the AGENTS.md skills index"


def test_sop_v11_clauses_are_in_the_rulebook() -> None:
    """真知TODO list SOP v1.1 增补必须落地成可执行规则, 不能只在文档里出现。"""
    body = _body()
    for phrase in ("不能复制粘贴", "删除线", "小方案", "不宜过多"):
        assert phrase in body, f"missing SOP v1.1 clause {phrase}"
