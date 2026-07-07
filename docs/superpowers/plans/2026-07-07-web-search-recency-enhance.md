# Web Search & Recency 增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增强 haitun-workspace 系统提示词,让 agent 对"命名实体当前归属/成员"这类高频变动事实默认主动联网,并加入知识截止时间锚点与反向边界。

**Architecture:** 就地增强(不新建 section)。规则文本改在 stable prefix 的 `WEB_SEARCH_RECENCY_SECTION`;知识截止时间作为易变值并入 dynamic suffix 的 `_build_datetime_section()`,走环境变量 `HAITUN_KNOWLEDGE_CUTOFF` 注入。

**Tech Stack:** Python 3(workspace `systems/` 模块),pytest,ruff。

## Global Constraints

- 工作区: `examples/haitun-workspace`;工作目录 worktree `~/psi-agent-websearch`,分支 `enhance/web-search-recency`。
- `prompt_sections.py` 顶部有 `# ruff: noqa: E501`,长行免检 —— 规则文本可保持单行长句,匹配现有风格。
- 提示词英文文案与现有 `WEB_SEARCH_RECENCY_SECTION` 风格一致(编号项、`**加粗引导词**`、破折号)。
- 截止时间用环境变量 `HAITUN_KNOWLEDGE_CUTOFF`(类比现有 `HAITUN_TIMEZONE`);未设时输出中性兜底文案,**绝不**编造假日期。
- CI 同时跑 `ruff check` 和 `ruff format --check`,两个都要过(引号/格式规范化差异)。
- 分支只 push 到 `enhance/web-search-recency`,是否合入由用户决定。

---

### Task 1: 增强 `WEB_SEARCH_RECENCY_SECTION` 规则文本

在现有第 1 条补入"命名实体当前状态/归属/成员"高危类别 + 强默认措辞,并在段末新增反向边界条目(第 6 条)。

**Files:**
- Modify: `examples/haitun-workspace/systems/prompt_sections.py:223-231`
- Test: `examples/haitun-workspace/tests/test_web_search_recency.py`(新建)

**Interfaces:**
- Consumes: 无(纯常量文本)。
- Produces: 模块常量 `WEB_SEARCH_RECENCY_SECTION: str`,签名不变;新增子串保证包含关键词 `current status, affiliation, or membership` 与 `Stable facts that do not change over time`。

- [ ] **Step 1: Write the failing test**

新建 `examples/haitun-workspace/tests/test_web_search_recency.py`:

```python
"""Tests for the enhanced WEB_SEARCH_RECENCY_SECTION prompt text."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SYSTEMS_DIR = WORKSPACE_ROOT / "systems"
if str(SYSTEMS_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMS_DIR))

ps: Any = importlib.import_module("prompt_sections")


def test_section_names_entity_membership_category():
    text = ps.WEB_SEARCH_RECENCY_SECTION
    assert "current status, affiliation, or membership" in text
    # roster / lineup wording present so sports/esports lineups are covered
    assert "roster" in text and "lineup" in text


def test_section_has_default_must_verify_wording():
    text = ps.WEB_SEARCH_RECENCY_SECTION
    assert "default to verifying online" in text
    assert "do not answer from memory" in text


def test_section_has_reverse_boundary():
    text = ps.WEB_SEARCH_RECENCY_SECTION
    assert "Stable facts that do not change over time" in text
    # gray-area tiebreak leans toward searching
    assert "lean toward a quick search" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/psi-agent-websearch/examples/haitun-workspace && python -m pytest tests/test_web_search_recency.py -v`
Expected: FAIL — assertions on missing substrings (`current status, affiliation, or membership` 等尚未加入)。

- [ ] **Step 3: Modify the section text**

把 `prompt_sections.py:223-231` 整段替换为(第 1 条追加高危类别+强默认句,末尾新增第 6 条反向边界):

```python
WEB_SEARCH_RECENCY_SECTION = """\
## Web Search & Recency
Your built-in knowledge is frozen at training time and goes stale. When an answer depends on facts that change over time, prefer a live web search over answering from memory.
1. **Search first for time-sensitive facts.** Prices, exchange/tax rates, version numbers and release notes, rankings/leaderboards, "latest"/"current"/"newest" anything, who currently holds a role, recent events, dates, deadlines, availability — verify these online before answering. This includes the **current status, affiliation, or membership of named entities**: who someone currently works for or plays for, a team/organization's current roster or member list, sports or esports lineups, and match results or schedules. For "who is on X now / current roster / current champion / latest lineup" questions, default to verifying online and do not answer from memory — even when a plausible-looking answer comes to mind. If the user's question turns on a fact that may have changed since your training cutoff, treat searching as the default, not the exception.
2. **State what is verified vs. from memory.** Make the basis of each claim clear: mark facts you confirmed this turn as verified (with the source), and flag anything you are answering from prior knowledge as unverified/from memory and possibly outdated. Never present a remembered figure as if it were freshly checked.
3. **Cite your sources.** For every fact you pulled from the web, give the source — page/site title plus the URL, and the publication or "as of" date when it matters. Prefer primary/official sources (vendor docs, release pages, official announcements) over aggregators. If you could not find a source, say so instead of guessing.
4. **Cross-check when it matters or sources conflict.** For high-stakes or fast-moving facts, confirm with 2+ independent sources. If sources disagree, do not silently pick one — report the discrepancy, prefer the most authoritative and most recent, and note the date of each.
5. **Respect the clock.** Note the current date when recency is relevant, prefer the newest reliable information, and watch for stale pages (check publish/updated dates). If live lookup is unavailable, answer from memory but explicitly caveat that it may be out of date and could not be verified.
6. **Do not over-search stable facts.** Stable facts that do not change over time — basic math, settled definitions, general how-to, and code you can reason about locally — do not need a lookup. When you are unsure whether something is time-sensitive, lean toward a quick search rather than guessing from memory.\
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/psi-agent-websearch/examples/haitun-workspace && python -m pytest tests/test_web_search_recency.py -v`
Expected: PASS(3 项全过)。

- [ ] **Step 5: Lint**

Run: `cd ~/psi-agent-websearch && ruff check examples/haitun-workspace/systems/prompt_sections.py examples/haitun-workspace/tests/test_web_search_recency.py && ruff format --check examples/haitun-workspace/systems/prompt_sections.py examples/haitun-workspace/tests/test_web_search_recency.py`
Expected: 无错误。若 `ruff format --check` 报格式差异,运行 `ruff format <file>` 后重跑测试。

- [ ] **Step 6: Commit**

```bash
cd ~/psi-agent-websearch
git add examples/haitun-workspace/systems/prompt_sections.py examples/haitun-workspace/tests/test_web_search_recency.py
git commit -m "feat(haitun): add entity-membership category + reverse boundary to Web Search & Recency

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 知识截止锚点注入 `_build_datetime_section`

在 dynamic suffix 的日期段追加一行知识截止时间,与当前日期并排,让模型能对比"这事在截止后可能变过吗"。

**Files:**
- Modify: `examples/haitun-workspace/systems/system.py:671-677`
- Test: `examples/haitun-workspace/tests/test_datetime_section.py`(新建)

**Interfaces:**
- Consumes: 环境变量 `HAITUN_KNOWLEDGE_CUTOFF`(可选,如 `2026-01`)。
- Produces: `_build_datetime_section() -> str` 返回值新增最后一行,以 `Knowledge cutoff:` 开头;已设时含该值,未设时含 `unknown`。函数签名不变。

- [ ] **Step 1: Write the failing test**

新建 `examples/haitun-workspace/tests/test_datetime_section.py`:

```python
"""Tests for the knowledge-cutoff line in _build_datetime_section."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SYSTEMS_DIR = WORKSPACE_ROOT / "systems"
if str(SYSTEMS_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMS_DIR))

system: Any = importlib.import_module("system")


def test_cutoff_line_present_when_env_set(monkeypatch):
    monkeypatch.setenv("HAITUN_KNOWLEDGE_CUTOFF", "2026-01")
    out = system._build_datetime_section()
    assert "Knowledge cutoff: 2026-01" in out
    assert "verify online" in out


def test_cutoff_line_neutral_when_env_unset(monkeypatch):
    monkeypatch.delenv("HAITUN_KNOWLEDGE_CUTOFF", raising=False)
    out = system._build_datetime_section()
    assert "Knowledge cutoff: unknown" in out
    # never fabricate a fake date when unset
    assert "2026-01" not in out


def test_current_date_still_present(monkeypatch):
    monkeypatch.delenv("HAITUN_KNOWLEDGE_CUTOFF", raising=False)
    out = system._build_datetime_section()
    assert "## Current Date & Time" in out
    assert "Date:" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/psi-agent-websearch/examples/haitun-workspace && python -m pytest tests/test_datetime_section.py -v`
Expected: FAIL — `Knowledge cutoff:` 行尚未加入(`test_cutoff_line_present_when_env_set` / `test_cutoff_line_neutral_when_env_unset` 失败)。

若导入 `system` 因缺依赖报错(如 `psi_agent` 未装),先跑 `cd ~/psi-agent-websearch/examples/haitun-workspace && python -c "import sys; sys.path.insert(0,'systems'); import system"` 诊断;该 workspace 的 venv 已装 `psi_agent`,正常应可导入。

- [ ] **Step 3: Modify `_build_datetime_section`**

把 `system.py:671-677` 替换为:

```python
def _build_datetime_section() -> str:
    """Build the ## Current Date & Time section.

    Reads HAITUN_TIMEZONE (default UTC) for the timezone label and
    HAITUN_KNOWLEDGE_CUTOFF (optional, e.g. "2026-01") for the knowledge
    cutoff anchor. When the cutoff is unset, emit a neutral line rather
    than fabricating a date.
    """
    tz = os.environ.get("HAITUN_TIMEZONE", "UTC")
    now = datetime.now()
    cutoff = os.environ.get("HAITUN_KNOWLEDGE_CUTOFF", "").strip()
    if cutoff:
        cutoff_line = (
            f"Knowledge cutoff: {cutoff} (facts that may have changed after this "
            "date are not reliable from memory — verify online)."
        )
    else:
        cutoff_line = (
            "Knowledge cutoff: unknown — treat any fact that may have changed "
            "recently as possibly stale and verify online."
        )
    return (
        f"## Current Date & Time\nDate: {now.strftime('%Y-%m-%d')}\n"
        f"Time: {now.strftime('%H:%M:%S')}\nTime zone: {tz}\n{cutoff_line}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/psi-agent-websearch/examples/haitun-workspace && python -m pytest tests/test_datetime_section.py -v`
Expected: PASS(3 项全过)。

- [ ] **Step 5: Lint**

Run: `cd ~/psi-agent-websearch && ruff check examples/haitun-workspace/systems/system.py examples/haitun-workspace/tests/test_datetime_section.py && ruff format --check examples/haitun-workspace/systems/system.py examples/haitun-workspace/tests/test_datetime_section.py`
Expected: 无错误。若 `ruff format --check` 报差异,运行 `ruff format <file>` 后重跑测试。

- [ ] **Step 6: Commit**

```bash
cd ~/psi-agent-websearch
git add examples/haitun-workspace/systems/system.py examples/haitun-workspace/tests/test_datetime_section.py
git commit -m "feat(haitun): inject knowledge-cutoff anchor into datetime section

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 全量校验

确认两处改动共存无回归,整体测试与 lint 通过。

**Files:** 无新增(校验任务)。

- [ ] **Step 1: Run the two new test files together**

Run: `cd ~/psi-agent-websearch/examples/haitun-workspace && python -m pytest tests/test_web_search_recency.py tests/test_datetime_section.py -v`
Expected: 6 项全 PASS。

- [ ] **Step 2: Run the full workspace test suite (regression)**

Run: `cd ~/psi-agent-websearch/examples/haitun-workspace && python -m pytest tests/ -q`
Expected: 全通过,无因改动引入的新失败。若有与本改动无关的既有失败(如缺外部 CLI 的 apple/xfyun 测试),记录说明,不视为本任务回归。

- [ ] **Step 3: Lint the full workspace**

Run: `cd ~/psi-agent-websearch && ruff check examples/haitun-workspace && ruff format --check examples/haitun-workspace`
Expected: 无错误(至少本次改动涉及的文件干净)。

- [ ] **Step 4: (可选)人工冒烟**

依赖模型服务时,启动 gateway 并重放"BLG/HLE 当前阵容"类问题,确认 agent 未经"用联网功能确认"提醒即主动调用 `search`。此项依赖上游模型,不作为合入硬门槛。

## Self-Review

**Spec coverage:**
- 改动 A(截止锚点)→ Task 2 ✓
- 改动 B(扩类别+强默认)→ Task 1 Step 3 第 1 条 ✓
- 改动 C(反向边界)→ Task 1 Step 3 第 6 条 ✓
- 单元测试(cutoff 已设/未设)→ Task 2 test ✓
- 文本校验(关键词存在)→ Task 1 test ✓
- ruff check + format --check → 各 Task Step 5 + Task 3 ✓
- 人工冒烟(可选,非硬门槛)→ Task 3 Step 4 ✓

**Placeholder scan:** 无 TBD/TODO;每个改代码步骤都给了完整代码。

**Type consistency:** `_build_datetime_section() -> str` 签名两处一致;测试断言的子串与 Step 3 文案逐字对应(`current status, affiliation, or membership`、`roster`、`lineup`、`default to verifying online`、`do not answer from memory`、`Stable facts that do not change over time`、`lean toward a quick search`、`Knowledge cutoff: 2026-01`、`Knowledge cutoff: unknown`)。
