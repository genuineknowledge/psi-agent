# 正负面清单判断卡整合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 吸收 PR #843 的判断卡交互，同时把会议纪要候选从“逐句写入”改为“事件包整理 → 分析 → 写入测试表”。

**Architecture:** 在现有正负面清单工具链上增加候选事件包状态和批量卡片投影；不引入第二套台账。卡片内部状态放 AppData，最终写入继续调用现有测试表适配器，正式总表只通过读取适配器访问。

**Tech Stack:** Python 3.14、pytest、现有 Feishu card/send/edit 工具、现有 `_positive_negative_list` 模型与测试表适配器。

## Global Constraints

- 正式总表只读；所有新增记录和反馈只写机器人独立测试表。
- 不增加测试表字段；内部状态只保存在 AppData。
- 不显示 open_id、内部规则 ID、游标或状态码。
- 不新增依赖，不复制 PR #843 的独立 ledger 写入路径。
- 每个行为事件先完成证据检查和去重，再允许写入者确认。

### Task 1: 建立事件包模型与候选卡状态

**Files:**
- Modify: `agents/feishu/tools/_positive_negative_list/models.py`
- Create: `agents/feishu/tools/_positive_negative_list/candidate_batches.py`
- Test: `agents/feishu/tests/test_positive_negative_pipeline_contract.py`

**Interfaces:**
- `CandidateEvent`：保存候选文本、来源、涉事人、时间、场合、影响、证据状态和事件包状态。
- `CandidateBatchStore.save/load`：AppData 下批次幂等读写。
- `merge_candidates(batch, source_index, target_index)`：合并来源句并保留来源。

- [ ] 写失败测试：两句同人同场合候选只能合并为一个事件包；评价性句子标记为待补行为事实；相同来源键重复提交返回同一批次。
- [ ] 运行专项测试确认失败。
- [ ] 实现最小模型、状态存储和合并逻辑。
- [ ] 运行测试确认通过。

### Task 2: 实现“候选整理卡”，禁止逐句写入

**Files:**
- Create: `agents/feishu/tools/positive_negative_candidate_card.py`
- Modify: `agents/feishu/tools/_positive_negative_list/candidate_batches.py`
- Test: `agents/feishu/tests/test_positive_negative_pipeline_contract.py`

**Interfaces:**
- `positive_negative_candidate_card(...)`：发卡或处理回调。
- 卡片动作：`keep_candidate`、`merge_candidate`、`request_evidence`、`ignore_candidate`。
- 任何候选动作不得调用测试表写入；只有批次整体进入 `ready_for_analysis` 后才允许后续分析工具处理。

- [ ] 写失败测试：卡片没有“确认记录/直接写入”按钮；点击任一候选动作后 records.json 和测试表写入次数仍为零。
- [ ] 写失败测试：重复点击不改变终态；合并后卡片显示一个事件包和来源句列表。
- [ ] 运行测试确认失败。
- [ ] 复用 PR #843 的 schema 2.0 原位更新、表单和姓名展示模式实现。
- [ ] 运行卡片测试确认通过。

### Task 3: 将事件包接入现有分析与确认写入链路

**Files:**
- Modify: `agents/feishu/tools/positive_negative_list.py`
- Modify: `agents/feishu/tools/positive_negative_list_confirm.py`
- Modify: `agents/feishu/tools/_positive_negative_list/dedupe.py`
- Test: `agents/feishu/tests/test_positive_negative_pipeline_contract.py`

**Interfaces:**
- 事件包完成后生成单条 `CaseDraft`，仍调用现有确认卡和测试表写入器。
- 负面记录继续要求正确做法、立即补救、预防措施。
- 写入目标只能由 `configured_table_adapter()` 返回的测试适配器决定。

- [ ] 写失败测试：批次确认只能生成一条 CaseDraft；缺少四要素或证据时拒绝生成确认卡。
- [ ] 写失败测试：正式总表适配器被调用时只允许 list/get，不允许 create/update。
- [ ] 运行测试确认失败。
- [ ] 实现事件包到 CaseDraft 的映射与测试表目标保护。
- [ ] 运行专项测试确认通过。

### Task 4: 吸收反馈/复盘卡的安全部分

**Files:**
- Modify: `agents/feishu/tools/_positive_negative_list/notifications.py`
- Modify: `agents/feishu/tools/positive_negative_case_review.py`
- Test: `agents/feishu/tests/test_positive_negative_pipeline_contract.py`

**Interfaces:**
- 保留 PR #843 的结果反馈卡、补充说明和复盘按钮语义，但写入仅限测试表/私有 AppData。
- 通知和复盘动作重复点击幂等；失败时不刷终态。

- [ ] 写失败测试：确认写入测试表后触发一次提醒和一次复盘；重试不重复写表或发消息。
- [ ] 写失败测试：补充说明不会调用正式总表更新。
- [ ] 运行测试确认失败。
- [ ] 实现最小安全适配。
- [ ] 运行专项测试确认通过。

### Task 5: 更新 Skill 与回归验证

**Files:**
- Modify: `agents/feishu/skills/positive-negative-list/SKILL.md`
- Test: `agents/feishu/tests/test_positive_negative_pipeline_contract.py`

- [ ] 增加“会议纪要候选不是行为记录”的硬规则和总分回复模板。
- [ ] 增加测试表/正式总表边界说明。
- [ ] 运行 Ruff、专项测试和相关工具发现测试。
- [ ] 运行 `git diff --check`，确认没有字段或配置文件新增。
