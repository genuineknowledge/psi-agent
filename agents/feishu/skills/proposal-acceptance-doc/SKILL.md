---
name: proposal-acceptance-doc
description: "交付/验收文档对照建议(非 binding). LOAD when user submits design/delivery/验收文档 against a proposal step with written standards, or after a writing-standard schedule 索要验收文档 and they paste the doc link. Compare doc to written acceptance criteria → short suggestion; human decides. NEVER 组织验收通过. NOT for POC评测报告(proposal-acceptance-poc). NOT for 只催进度/建定时(proposal-writing-standard). NOT for 形成期 v2.0 四期结构合规(proposal-writing-standard)."
category: productivity
---

# 方案 · 文档验收建议

针对业务类为 **交付物**、落表验收门=`文档` 的步骤: 用户交来交付说明 / 设计稿说明 / 验收材料文档后,
对照方案书面标准给**分钟级建议**. 人拍板; 海豚不盖章.
业务类识别见 writing-standard「三类里程碑」.

共享对照引擎见 [`proposal-acceptance-suggest`](../proposal-acceptance-suggest/SKILL.md);
本文是 **交付/验收文档到达后的特化入口**(与 POC 材料分流).

## When to use

- 「交付文档在这 / 验收材料链接 / 按方案标准看这篇说明够不够」.
- 跟进索要验收文档之后, 用户丢回文档.
- 步骤有书面验收标准 + 交付物是文档类, **不是** POC 跑批报告.

## When not to use

- 材料是 POC/评测/检测报告且步骤门为 POC →
  [`proposal-acceptance-poc`](../proposal-acceptance-poc/SKILL.md).
- 只要催交、还没文档 → [`proposal-writing-standard`](../proposal-writing-standard/SKILL.md)(同步/更新定时).
- 检查方案是否按 **执行跟进与验收 SOP v2.0** 四期写全、硬闸门过没 →
  [`proposal-writing-standard`](../proposal-writing-standard/SKILL.md)
  (形成期, 不是执行验收; 勿再按旧 11 章 RFC 查).
- 要盖章通过 → **拒绝 binding**.

## 纪律(硬)

1. **非 binding** — 文末请验收人决定; 禁止「验收通过」「可以结项」.
2. **标准优先** — 无书面标准 → 挡回补标准, 不凭感觉打分.
3. **禁止编造** — 未读到的内容标「材料未提及」.
4. **无观测不写符合** — 打不开链接 → 「无法判断 + 原因」.
5. **减读** — 按标准项收一页; 不甩第二套长考核表.

## 输入

| 输入 | 必需? |
|------|--------|
| 验收标准(方案实施期/跟进验收期条目 / 里程碑验收门 / 粘贴条文) | **是** |
| 交付或验收文档 | **是**(缺则只出待交清单) |
| 步骤名 / 方案链接 | 建议有 |

## 引擎

```text
1. 固定标准项列表(编号或短标题)
2. 读交付/验收文档
3. 逐条: 已覆盖 / 未提及 / 疑似冲突 / 无法判断(+摘录出处)
4. L0 检查计划: 建议人还要点开/确认什么
5. L1 若已只读打开: 只写真实看到的; 仍不盖章
6. 输出「文档验收建议报告」
7. 可选 1–5 分反馈(评建议, 非评人)
```

## 报告模板

```text
文档验收建议 — <步骤名>

标准来源: <章节/链接>
证据文档: <链接>

对照摘要:
| 标准项 | 建议看法 | 依据 |
|--------|----------|------|
| ... | 已覆盖 / 未提及 / 冲突 / 无法判断 | ... |

建议补证: ...
检查计划(L0): ...
说明: 以上为 Agent 建议, 是否通过由 <验收人> 决定.
```

## 与通用 suggest 的关系

| | 本文 | `proposal-acceptance-suggest` |
|--|------|-------------------------------|
| 触发 | 明确是**执行步文档门**材料到达 | 泛化「对照标准看材料」 |
| 输出 | 同形建议报告 | 同形 |
| 选用 | 跟进链路文档门优先本文 | 用户没分 POC/文档时可用通用版 |

## 成功标准

- 标准缺失挡回; 有标准则条条可追溯.
- 与 POC skill 不混用材料类型.
- 无 binding 盖章.
---

## 特化预留

自动从倒排表拉标准项表 —— 后续优化.
