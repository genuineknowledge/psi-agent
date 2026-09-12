---
name: poc-l2-reproduce
description: "POC L2 wiring / static operable asserts (assert_path/tool). LOAD when 实验方法, 数据来源, 复现步骤, L2 接线自测. Call poc_l2_probe. For 海豚功能对话 UAT / 自然语境验收 use haitun-feature-uat + poc_feature_uat instead. NEVER claim 数据已验真 without probe. NOT for L1 padding. NOT for proposal-acceptance-poc document-only对照."
category: productivity
---

# POC L2 · 可操作则复现

承接 L1 对照实验结论: **L1 不做海豚特化**; 差异化在 **L2**.
权威约定见本 skill 与工具 `poc_l2_probe`。

**对话式功能 UAT**（A→B 发用户话术对照 Pass）不在本 skill：见 [`haitun-feature-uat`](../haitun-feature-uat/SKILL.md) / `poc_feature_uat`。

## When to use

- 材料或对话出现: 「实验方法」「数据来源」「复现步骤」「评测协议」「怎么跑」
  「按步骤验收」「L2 自测」等取数/复现线索.
- 用户要求: 按文档重跑、核对数据是否站得住、用 playbook 自测能力包.
- 仓库内方案 SOP 闭环自测:
  `skills/poc-l2-reproduce/playbooks/proposal-sop-closed-loop.json`.

## When not to use

- 只审报告话术/灌水/标准是否拧巴 → **不要当 L1 skill**(本期无海豚 L1 特化;
  基模可读文档即可). 不要用本 skill 替代「灌水审查」.
- 仅有检测文档、只要契约对照、且**没有**可跟走方法 / playbook →
  [`proposal-acceptance-poc`](../proposal-acceptance-poc/SKILL.md).
- 用户要「组织验收通过」盖章 → **拒绝**; 只报告 L2 剧本结果.

## Instructions

```text
1. 判定: 是否有结构化 playbook, 或能否把方法收成 assert_* 步骤?
2. 无 → 明确「L2 不适用」+ 缺什么; 禁止说「数据已验真」.
3. 有 → poc_l2_probe(playbook_path=... 或 playbook_json=...)
4. 按返回的 verdict / gaps 向用户汇报; 附 disclaimer 语义.
5. 回合结束前自查: 有无把 fail / N/A 说成通过.
```

### 固定话术(不适用)

> **L2 不适用(本回合未做复现验真).** 原因: \<gaps\>.
> 文档审查通过 ≠ 数据已验真. 需要结构化 playbook 或可执行步骤后才能跑 L2.

### 与 acceptance-poc

| | acceptance-poc | 本文 |
|--|----------------|------|
| 主业 | 场景/样本/及格线 vs 检测文档 | playbook 可操作复现/自测 |
| 重跑 | 不做 | 能则做 |

## 初版限制(刻意为之)

- v1 只跑 `assert_path` / `assert_file_contains` / `assert_tool` /
  `unsupported`.
- 不对**当前会话**发同步 chat(会死锁); 自然语言 `method_text`  alone → N/A.
- 盲评真人 / 特权系统 / 公开刷榜 → 标 `unsupported`, 整体 N/A 或列出缺口.
