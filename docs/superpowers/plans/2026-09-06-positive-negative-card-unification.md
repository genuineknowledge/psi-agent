# 正负面清单卡片统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将正负面清单候选整理与记录通知统一为 PR #843 的 schema 2.0 卡片风格，并让记录通知的「开始复盘」按钮进入现有私聊复盘流程。

**Architecture:** 保留现有候选事件包、正式总表写入和私有复盘状态机；只替换卡片渲染与通知传输层。候选整理阶段只做事件级纳入/忽略，补证延后到分析对话；记录通知改为独立的可回调卡片，回调复用 `positive_negative_case_review_start`。

**Tech Stack:** Python 3.14、飞书 Card 2.0 JSON、pytest、现有 `_feishu_impl.send_card_impl` 与 AppData 状态存储。

## Global Constraints

- 不触碰正在运行的海豚三号部署实例。
- 写入最终进入正负面清单正式总表（写入前预检通过后执行，不再使用机器人自建测试表）。
- 不增加正式表字段、不增加外部配置字段。
- 所有用户可见身份统一显示姓名，不显示 `ou_*`、规则 ID 或内部字段名。
- 候选句不是正式记录；必须先形成事件级候选，再进入分析和写入确认。
- 补证不作为候选整理卡的主动作，缺证在分析对话中轻量追问。
- 负面记录展示正确做法、立即补救、预防措施；复盘答案只保存私有 AppData。

### Task 1: 收敛候选整理卡动作与文案

**Files:**
- Modify: `agents/feishu/tools/positive_negative_candidate_card.py`
- Test: `agents/feishu/tests/test_positive_negative_pipeline_contract.py`

**Interfaces:**
- Preserve `render_candidate_card(batch) -> dict` and `positive_negative_candidate_card(...)`.
- Preserve callback actions `pn_candidate_keep_<index>` and `pn_candidate_ignore_<index>`.
- Stop generating merge/evidence buttons; existing persisted batches remain readable and old callbacks return a stable no-longer-supported status.

- [ ] **Step 1: Write failing tests**

Add assertions that rendered candidate cards use `schema == "2.0"`, contain exactly the visible row actions `纳入候选` and `暂时忽略`, do not contain `补充证据`/`合并到`, and keep the explanatory text that candidates are not formal records.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest agents/feishu/tests/test_positive_negative_pipeline_contract.py -k candidate -q`

Expected: FAIL because the current card still renders merge and evidence actions.

- [ ] **Step 3: Implement the minimal card change**

Change `_row_actions` to render one PR #843-style `column_set` with only `_button("纳入候选", ...)` and `_button("暂时忽略", ...)`. Remove evidence/merge parsing from the generated handler map, while retaining state compatibility for already stored batches. Change the footer to state `待整理：N 条 · 当前不写入任何表格`.

- [ ] **Step 4: Run focused tests**

Run: `pytest agents/feishu/tests/test_positive_negative_pipeline_contract.py -k candidate -q`

Expected: PASS for the new rendering and callback contract; update obsolete expectations for merge/evidence only.

- [ ] **Step 5: Commit**

```bash
git add agents/feishu/tools/positive_negative_candidate_card.py agents/feishu/tests/test_positive_negative_pipeline_contract.py
git commit -m "feat(haitun): simplify positive-negative candidate card actions"
```

### Task 2: Add a PR #843-style record-notice card with review entry

**Files:**
- Modify: `agents/feishu/tools/_positive_negative_list/notifications.py`
- Modify: `agents/feishu/tools/positive_negative_case_remind.py`
- Modify: `agents/feishu/tools/positive_negative_case_review.py`
- Test: `agents/feishu/tests/test_positive_negative_pipeline_contract.py`

**Interfaces:**
- Add `render_record_notice_card(record, subject_display, notice_id) -> dict` in notifications.
- Keep `NotificationSender.send_record_notice(...) -> NotificationResult`.
- Record notice card callback action is `pn_record_review_start`; handler is `positive_negative_case_review`.
- `positive_negative_case_review_start` accepts the callback payload and starts the existing private review draft.

- [ ] **Step 1: Write failing tests**

Add tests that monkeypatch card sending and assert record notice delivery uses `send_card_impl`, returns a message id, renders `schema: 2.0`, shows the resolved subject name, includes negative guidance fields, and includes exactly one `开始复盘` callback. Add a callback test asserting `pn_record_review_start` creates a private review draft and sends the existing three-part review prompt.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `pytest agents/feishu/tests/test_positive_negative_pipeline_contract.py -k 'record_notice or review' -q`

Expected: FAIL because the current implementation sends plain text and does not dispatch the new callback.

- [ ] **Step 3: Implement card rendering and delivery**

Build the card with PR #843 conventions: `schema: "2.0"`, `config.width_mode`, colored header, markdown summary, `hr`, and a bottom `column_set` containing only `开始复盘`. For negative records add `正确做法 · 建议`, `立即补救`, and `预防措施`; for positive records add a concise contribution note. Resolve names before rendering. Store notification receipts exactly as before, including the new card message id.

- [ ] **Step 4: Implement callback dispatch**

Parse the standard card action envelope, validate `record_id` and trusted subject identity, call `positive_negative_case_review_start` with the stored record, and return the review id/message id. Keep callback idempotent by returning the existing active review when the same record/subject is clicked again.

- [ ] **Step 5: Run focused tests**

Run: `pytest agents/feishu/tests/test_positive_negative_pipeline_contract.py -k 'record_notice or review' -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agents/feishu/tools/_positive_negative_list/notifications.py agents/feishu/tools/positive_negative_case_remind.py agents/feishu/tools/positive_negative_case_review.py agents/feishu/tests/test_positive_negative_pipeline_contract.py
git commit -m "feat(haitun): send positive-negative record notices as review cards"
```

### Task 3: Remove old generated card paths and verify the full contract

**Files:**
- Modify: `agents/feishu/skills/positive-negative-list/SKILL.md`
- Modify: `agents/feishu/tools/positive_negative_case_remind.py`
- Modify: `agents/feishu/tools/positive_negative_candidate_card.py`
- Test: `agents/feishu/tests/test_positive_negative_pipeline_contract.py`

**Interfaces:**
- Skill documentation must describe only the new candidate and record-notice card flows.
- Old persisted card state is not deleted destructively; old callbacks return a handled `unsupported_legacy_action` result.

- [ ] **Step 1: Write failing compatibility tests**

Add tests for old candidate actions returning `unsupported_legacy_action`, new handlers containing only supported actions, and the skill text not instructing plain-text record notices or old `确认记录/不做记录/修改内容` actions.

- [ ] **Step 2: Run compatibility tests and verify failure**

Run: `pytest agents/feishu/tests/test_positive_negative_pipeline_contract.py -k 'legacy or handlers or skill' -q`

Expected: FAIL against current old-action behavior/documentation.

- [ ] **Step 3: Implement compatibility cleanup**

Return a deterministic handled response for legacy action names without writing a row or sending a second card. Update the positive-negative skill to describe candidate整理, analysis-time补证, record-notice cards, and the review callback. Do not remove AppData files or touch production data.

- [ ] **Step 4: Run the full verification suite**

Run:

```bash
pytest agents/feishu/tests/test_positive_negative_pipeline_contract.py agents/feishu/tests/test_tool_discovery.py -q
ruff check agents/feishu/tools/positive_negative_candidate_card.py agents/feishu/tools/_positive_negative_list/notifications.py agents/feishu/tools/positive_negative_case_remind.py agents/feishu/tools/positive_negative_case_review.py
python3 -m compileall -q agents/feishu/tools
git diff --check
```

Expected: all tests pass, Ruff and compileall exit 0, and no whitespace errors are reported.

- [ ] **Step 5: Commit**

```bash
git add agents/feishu/skills/positive-negative-list/SKILL.md agents/feishu/tools agents/feishu/tests/test_positive_negative_pipeline_contract.py
git commit -m "docs(haitun): document unified positive-negative card flow"
```

## Completion Checklist

- Candidate整理 card matches PR #843 schema and exposes only 纳入候选 / 暂时忽略.
- Candidate card does not write a table and does not force补证.
- Record notices are cards, not plain text.
- Negative notices include correct behavior, immediate remedy, and prevention.
- 开始复盘 callback starts the existing private review dialogue.
- Old generated paths are no longer produced; legacy callbacks are safe and non-writing.
- No deployment instance is restarted or modified.
