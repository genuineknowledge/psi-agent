---
name: proposal-acceptance-poc
description: "POC/评测节点验收建议(非 binding). LOAD when user submits POC test report/检测文档/评测结果 against a proposal step, or after a writing-standard POC 索要卡 and they paste the POC doc link. Check scenario+samples+pass criteria vs report → short suggestion; human decides. NEVER 组织验收通过. NOT for 普通交付文档对照(proposal-acceptance-doc). NOT for 只催进度/建定时(proposal-writing-standard). NOT for 形成期 v2.0 四期结构检查(proposal-writing-standard)."
category: productivity
---

# 方案 · POC 验收建议

针对方案(**执行跟进与验收 SOP v2.0**)里业务类为 **场景闭环**、落表验收门=`POC`
(明示评测/样本/及格线/可复跑) 的步骤(常见于实验期或实施期):
用户交来 **POC 检测文档** 之后, 对照方案里的场景·样本·及格线给**分钟级建议**.
人拍板; 海豚不盖章. 业务类识别见 writing-standard「三类里程碑」.

共享对照纪律见 [`proposal-acceptance-suggest`](../proposal-acceptance-suggest/SKILL.md);
本文是 **POC 材料到达后的特化入口**.

## When to use

- 「POC 报告在这 / 检测文档链接 / 评测跑完了, 帮看过没过」.
- 跟进索要 POC 材料之后, 用户丢回文档或说明.
- 方案步骤门类型为 POC, 且本回合有检测材料.

## When not to use

- 交的是设计说明/交付说明/无评测跑批的文档 →
  [`proposal-acceptance-doc`](../proposal-acceptance-doc/SKILL.md).
- 还没交材料, 只要催 / 方案还在写 → [`proposal-writing-standard`](../proposal-writing-standard/SKILL.md).
- 要 binding「验收通过可上线」→ **拒绝盖章**, 只出建议报告.
- 要按实验方法**重跑/验数据真实性（接线断言）** → [`poc-l2-reproduce`](../poc-l2-reproduce/SKILL.md)
- 要**海豚功能对话 UAT**（自然语境用例打干净 Session）→ [`haitun-feature-uat`](../haitun-feature-uat/SKILL.md)

## 纪律(硬)

1. **非 binding** — 禁止「POC 验收通过」「组织认定合格」; 文末固定请人拍板.
2. **三者齐全才谈客观对照** — 方案侧须能定位: **场景 + 样本(或样本指针) + 及格线**;
   缺任一 → 报告写「无法完成 POC 对照 + 缺什么」, 不假装跑过.
3. **禁止编造跑批结果** — 没读到的数字/用例不得填写; 标「材料未提及」.
4. **检测文档优先** — 证据以用户提交的 POC 文档为准; 对话口述可作补充但要标明.
5. **不加人第二套长表** — 建议几分钟读完.

## 输入

| 输入 | 必需? |
|------|--------|
| 方案中该步的场景/样本/及格线(或章节指针) | **是** |
| POC 检测文档(链接或正文) | **是**(缺则只列待交清单, 不判) |
| 步骤名 / 方案链接 | 建议有 |

## 引擎

```text
1. 固定 POC 契约: 场景列表、样本指针、及格线(摘自方案)
2. 读检测文档(feishu_doc_read / 粘贴)
3. 逐场景对照:
   - 已覆盖且数字/结论可指回报告
   - 未跑 / 未提及
   - 与及格线冲突或口径不一致
   - 无法判断(缺权限、报告含糊、样本对不上)
4. 输出「POC 验收建议报告」(下方模板)
5. 可选: 请用户 1–5 分评价本建议是否有用(评海豚, 非盖章交付)
```

### 深度

| 档 | 本版 |
|----|------|
| 对照用户提交的 POC 文档 vs 方案契约 | **要做** |
| Agent 自己搭环境重跑题库 | **不做**(L2); 用户要求复现/验真 → [`poc-l2-reproduce`](../poc-l2-reproduce/SKILL.md) |

## 报告模板

```text
POC 验收建议 — <步骤名>

方案契约: 场景 / 样本 / 及格线 ← <章节或摘录>
检测文档: <链接>

对照:
| 场景或用例 | 建议看法 | 依据 |
|------------|----------|------|
| ... | 达到及格线迹象 / 未跑 / 冲突 / 无法判断 | 摘录+出处 |

缺口: ...
说明: 以上为 Agent 建议, 是否通过由 <验收人> 决定.
```

## 成功标准

- 无三者齐全时不假装客观通过.
- 有材料时每条能对上场景或及格线.
- 无 binding 盖章句.
---

## 特化预留

可复跑题库批处理、自动从方案抽出及格线表 —— 后续优化.
