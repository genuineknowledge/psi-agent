"""Validate the dynamic layer-1 additions to the company TODO audit chain.

Dynamic layer 1 (前后连续性, 事情搞没搞定) is already judged on main by
`company-todo-audit` (闭环五要素 + 回流) and `todo-completion-standard` (四档 + E1-E3).
What remains is to make those verdicts *accumulate* and *comparable*: every audit run
must dump structured eval records (`.todo-eval` + a Feishu 评测记录 bitable) and emit a
per-person previous-vs-current continuity summary (新开/承接/消失/回流/顺延). These
tests guard the parts whose removal would silently return the system to "check results
only live in a chat bubble": the eval schema, the idempotent dump, the six summary
counters, the 消失 → 推断已完成/待确认 ruling (not 未闭环, never 失实), and the AGENTS.md
index line.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = WORKSPACE_ROOT / "skills"
TOOLS_DIR = WORKSPACE_ROOT / "tools"

AUDIT = "company-todo-audit"

# The completion-standard verdict uses a fullwidth solidus (U+FF0F) in the skill; build it
# from its codepoint so the literal stays unambiguous to ruff (RUF001) while still matching
# the skill text byte for byte.
UNCERTAIN = "推断已完成" + chr(0xFF0F) + "待确认"

EVAL_FIELDS = (
    "date",
    "cycle",
    "person",
    "item",
    "item_type",
    "verdict",
    "missing_elements",
    "evidence_level",
    "evidence_refs",
    "rules_hit",
)


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


def _skill_text(name: str = AUDIT) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")


def _body(name: str = AUDIT) -> str:
    return _split_frontmatter(_skill_text(name))[1]


def test_audit_skill_exists_and_has_frontmatter() -> None:
    fm, body = _split_frontmatter(_skill_text())
    assert fm.get("name") == AUDIT, "frontmatter name must equal dir name"
    assert fm.get("description", "").strip(), f"{AUDIT} needs a non-empty description"
    assert body.strip(), f"{AUDIT} needs a non-empty body"


def test_adds_dynamic_layer_eval_dump() -> None:
    body = _body()
    assert ".todo-eval" in body, "audit must dump eval records under .todo-eval/"
    assert "评测记录" in body, "audit must sync eval records to the Feishu 评测记录 bitable"
    assert "feishu_bitable_create_records" in body, "the bitable sync must name its tool"
    assert "幂等" in body, "the dump must be idempotent per (cycle, person, item)"


def test_eval_fields_cover_the_schema() -> None:
    body = _body()
    for field in EVAL_FIELDS:
        assert field in body, f"eval record must carry the {field} field"


def test_continuity_summary_counts_six_items() -> None:
    body = _body()
    assert "前后对比" in body, "audit must emit a per-person continuity summary"
    for phrase in ("新开", "承接", "消失", "已闭环", "回流", "请假顺延"):
        assert phrase in body, f"summary must count {phrase}"
    # 消失 must route to completion-standard's uncertain verdict, not to closure failure
    assert UNCERTAIN in body, "消失 must be ruled 推断已完成/待确认"
    assert "消失 ≠ 未闭环" in body, "vanishing is not closure failure"
    assert "不计逾期" in body, "leave-extension must not count toward overdue"


def test_audit_e2_evidence_intake_is_wired() -> None:
    """Iteration 2 B3: E1-less / uncertain rows get third-party confirmation evidence."""
    body = _body()
    assert "feishu_message_search" in body, "audit must name the E2 search tool"
    assert "feishu_thread_read" in body, "audit must name the thread reader"
    assert "user_key" in body, "message search is user-token-only"
    assert "unavailable" in body, "failed E2 lookups must report unavailable"
    assert "不替代" in body and "五要素要件" in body, "E2 evidence must not replace closure requirements"


def test_only_references_real_tools() -> None:
    real = _public_tool_names()
    assert "feishu_bitable_create_records" in real
    assert "wiki_read" in real

    referenced = set(re.findall(r"\b(feishu_[a-z_]+|wiki_[a-z_]+)\b", _skill_text()))
    # feishu_task_get is named in the skill as *the tool that does NOT exist*; it is not a reference.
    non_tools = {"feishu_context", "feishu_task_get"}
    concrete = {n for n in referenced if not n.endswith("_") and n not in non_tools}
    unknown = concrete - real
    assert not unknown, f"skill references tool names that don't exist: {sorted(unknown)}"


def test_indexed_in_agents_md() -> None:
    agents = (WORKSPACE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert f"`{AUDIT}`" in agents, f"{AUDIT} must be listed in the AGENTS.md skills index"
    assert "评测落盘" in agents, "the index line must mention the eval dump"
    assert "前后对比摘要" in agents, "the index line must mention the continuity summary"


def test_audit_connects_sop_v11_strike_through() -> None:
    """SOP v1.1 删除线验收与五要素的衔接必须写明且不豁免硬要件。"""
    body = _body()
    assert "SOP v1.1" in body and "删除线" in body
    assert "不豁免" in body, "strike-through must not waive the five closure elements"
    assert "completed_at" in body, "E1 remains the completion hard evidence"
