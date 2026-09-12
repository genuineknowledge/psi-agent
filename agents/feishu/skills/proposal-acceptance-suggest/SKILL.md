---
name: proposal-acceptance-suggest
description: "方案验收建议通用入口(非 binding). LOAD when user asks 对照标准看够不够/给验收建议 without saying POC vs 交付文档, or pastes mixed evidence. Prefer proposal-acceptance-poc for POC reports and proposal-acceptance-doc for delivery docs when the gate type is known. Compare evidence to written standards → short suggestion; human decides. NEVER 组织验收通过. NOT for 进度催办/建定时(proposal-writing-standard). NOT for 形成期 v2.0 结构检查. NOT for 公司看板完成度/失实."
category: productivity
---

# 方案验收建议(通用 · 非 binding)

给人减负: **对照书面标准看材料** + **该怎么试(L0/L1)**.
人拍板; 可选给建议打分(评海豚).

**分流(优先于本文):**

| 材料 / 门 / 业务类 | Skill |
|-------------------|--------|
| POC 检测/评测报告, 或步骤门=`POC`, 或里程碑业务类=**场景闭环**(含海豚功能自测分流) | [`proposal-acceptance-poc`](../proposal-acceptance-poc/SKILL.md) |
| 交付/验收文档, 或步骤门=`文档`, 或里程碑业务类=**交付物** | [`proposal-acceptance-doc`](../proposal-acceptance-doc/SKILL.md) |
| 步骤门=`无需` / 业务类=**无交付物** | **不**跑对照; 回 writing-standard 进度语义(勾选自报即可) |
| 用户没分清、或只要泛化对照 | **本文** |

业务类识别口径见 [`proposal-writing-standard`](../proposal-writing-standard/SKILL.md)
「三类里程碑」(与枚举 `无需`/`文档`/`POC` 一一对应).

跟进建定时见 [`proposal-writing-standard`](../proposal-writing-standard/SKILL.md)
(检查/评审通过即同步); **勾选 ≠ 验收**.

## When to use

- 「按方案标准看够不够」且未标明 POC vs 文档.
- 跟进之后用户显式要验收辅助, 门类型不明.

## When not to use

- 已知 POC 材料 → acceptance-poc.
- 已知交付文档门 → acceptance-doc.
- 只要拆步催办 / 建跟进定时 → `proposal-writing-standard`(C.同步定时).
- 形成期 v2.0 四期结构 / 硬闸门 → writing / review(勿按旧 11 章 RFC).
- 看板完成度/失实 → todo-completion / todo-truthfulness.
- 要求盖章通过 → 拒绝 binding.

## 纪律(硬)

1. **非 binding** — 禁止「验收通过」「组织认定合格」; 文末请人决定.
2. **禁止编造**; **无观测不写符合**.
3. **标准优先** — 无标准挡回补标准.
4. **局部信息** — 只用本回合指定材料.
5. **不加人负担** — 几分钟可读完.

## 输入

| 输入 | 必需? |
|------|--------|
| 验收标准 | **是** |
| 证据 | 建议有 |
| 验收人 | 否 |

## 引擎

```text
1. 若能判断门类型 → 转 poc / doc skill, 本文结束
2. 固定标准项 → 收集证据 → 逐条对照
3. L0 检查计划; 有权限可 L1 只读观测
4. 输出建议报告; 可选 1–5 分反馈
```

### 深度档

| 档 | 本版 |
|----|------|
| L0 检查步骤 + 文字对照 | **要做** |
| L1 只读打开 | **可做** |
| L2+ 写操作/压测 | **不做** |

## 报告模板

```text
验收建议 — <条目名>

标准来源: ...
证据来源: ...

对照摘要:
| 标准项 | 建议看法 | 依据 |
|--------|----------|------|
| ... | 已覆盖 / 未提及 / 冲突 / 无法判断 | ... |

建议补证 / 检查计划(L0) / 观测(L1)
说明: 以上为 Agent 建议, 是否通过由 <验收人> 决定.
```

## 成功标准

- 能分流则分流; 否则通用报告仍非 binding、可追溯.
---

## 特化预留

门类型自动识别准确率、与跟进卡 action 直达 —— 后续优化.
