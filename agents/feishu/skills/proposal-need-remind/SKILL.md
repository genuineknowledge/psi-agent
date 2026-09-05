---
name: proposal-need-remind
description: "公司决策分级(大事/中事/小事)下的方案入口提醒者(一层, 非监督者). LOAD when the user is about to decide, change a plan, seek collaboration, ask for extra resources, discuss TODO risk, assign work that may need a written proposal, or asks 「这算中事吗」「要不要写方案」「能不能自己定」. Soft-remind only: if it looks like 中事, suggest writing a multi-option proposal and offer to help via proposal-writing-standard; never block the turn, never force a doc. NOT for 半强制卡方案/未写不许继续(二层监督者, 未做). NOT for 行政财务假勤报销分级(admin-finance-governance). NOT for 最终拍板或 RFC 结构检查(见 proposal-review-standard / proposal-writing-standard)."
category: knowledge-base
---

# 方案需求提醒(决策分级 · 提醒者一层)

公司制度把事情分成 **大事 / 中事 / 小事**, 并规定不同决策动作.
**中事** 的制度动作是: **拟定方案(须含多种方案与分析) → 直属上级共同决策**.

本文只做 **提醒者一层**: 对话里疑似中事(或吃不准)时, **提醒**对方先走方案小闭环,
并可接到 [`proposal-writing-standard`](../proposal-writing-standard/SKILL.md) 帮写/检查.
**不做监督者二层**(半强制要求写出方案、未写就卡住流程) —— 那是另一套验收, 未实现.

**刻意为之:** 一层宁可略多提醒, 也不要在证据不足时假装「小事可自决」而漏提醒;
但提醒必须可跳过, 用户说「先不写 / 这是小事」后本回合不再纠缠.

## When to use

- 用户在商量「能不能自己定」「要不要找人一起定」「要不要写方案/RFC」.
- 对话里出现: 改原计划、找人协作、要额外资源、TODO/项目有可控风险、跨人决策.
- 派活/交办时事项看起来超过「本人职责内可自决」([`work-assignment-delegation`](../work-assignment-delegation/SKILL.md) 可并行; 需要方案时用本文提醒 + 方案 skill).
- 用户贴了意图但还没文档, 不确定要不要上方案.

## When not to use

- 用户已经在写/改 RFC, 要结构检查或润色 → 只用 [`proposal-writing-standard`](../proposal-writing-standard/SKILL.md).
- 用户要评审档位 → [`proposal-review-standard`](../proposal-review-standard/SKILL.md).
- 假勤/报销等行政财务分级 → [`admin-finance-governance`](../admin-finance-governance/SKILL.md)(另一套阈值, 勿混用本文的大中小).
- 要 **强制**「不写方案不许继续」→ **本文禁止**; 那是二层, 未交付.

## 分级口径(制度原文要点 · 判定表)

准确识别事之大小是成员必修课; 海豚用同一套词, 不另造「重要/一般」.

### 小事 → 个人决策(不提醒写方案)

同时更贴近下列特征时倾向小事(有一条「中事信号」则升档, 见下):

- 仅涉及 **本人职责范围内** 的工作
- **不影响** TODO 事项进度
- **不涉及** 与他人协作
- **不涉及** 给公司带来损失等风险

**决策规范:** 个人决策即可.

### 中事 → 提醒拟定方案(本 skill 主目标)

出现任一信号即 **至少按中事提醒**(可标「疑似中事」):

- 涉及 TODO 事项的 **可控风险**
- **改变** 原有计划
- **寻求** 与他人合作
- 需要 **额外资源** 等

**决策规范:** 拟定方案, 方案中要有 **多种方案及其分析**; 由 **直属上级共同决策**.

### 大事 → 提醒会议民主讨论(不是「只写方案就够」)

出现任一信号即按大事提醒:

- 涉及 TODO 事项的 **不可控风险**
- 可能给公司带来 **重大损失**
- 对项目与公司有更好的建议等(重大方向/组织级议题)

**决策规范:** 相关会议 **民主讨论、集体决策**, 由 **第一责任人拍板**.
大事提醒里可提「必要时仍可准备书面材料」, 但 **主路径是会议集体决策**, 不要把大事降级成「写个方案私了」.

### 共同纪律

凡经组织决策的, 必须坚决贯彻、落实到底 —— 提醒里可带一句, 不展开说教.

## 引擎: 提醒闭环(一层)

```text
本回合用户意图 / 事项描述
    ↓
按上表粗分: 小事 | 疑似或确定中事 | 大事 | 吃不准
    ↓
小事 → 不打断主任务; 不提方案(除非用户主动问)
中事 / 吃不准 → 输出「方案入口提醒」+ 依据信号; 询问是否现在帮写/检查
大事 → 输出「会议集体决策提醒」+ 依据信号; 不假装中事方案可替代
    ↓
用户同意写方案 → 加载 proposal-writing-standard(帮写/检查/补救)
用户跳过 → 尊重; 本回合不再半强制追问
```

### 提醒话术要求

1. **先给档位词**(只用 `小事` / `中事` / `大事` / `暂无法判定`), 再给 **1–3 条命中的制度信号**(摘自用户原话或上下文, 短).
2. 中事提醒必须点明制度要求: **多种方案 + 分析**、**直属上级共同决策**.
3. 明确这是 **建议**, 例如: 「按制度这更像中事, **建议**先写方案再决策; 你也可以说明为何按小事自决.」
4. **一次提醒即可**; 用户已拒绝或已声明自决后, 同话题不反复刷屏.
5. 用户愿意写时: 主动提出用方案 SOP 帮起草或检查飞书文档, 再进 `proposal-writing-standard`.
6. **禁止** 二层语气: 「必须先交方案才能继续」「不写方案我拒绝对话」「已为你创建强制待办」等.

### 吃不准时

- 输出 `暂无法判定`, 列出缺哪类信息(是否改计划 / 是否协作 / 是否要资源 / 风险是否可控).
- **默认按「疑似中事」给一条轻提醒**(一层策略: 漏提醒比多提醒更糟), 并问一句澄清.
- 不要为了显得果断而硬判小事.

### 与派活的衔接

若同时命中交办([`work-assignment-delegation`](../work-assignment-delegation/SKILL.md)):

- 交办记录照常走;
- 另加一句中事/大事提醒;
- 需要方案时协助接收者形成可评审草案 → 转 `proposal-writing-standard`, 不在本文展开 11 章.

## Boundaries

- **无方案表、无定时扫描** —— 只在对话/交办当下提醒.
- 不维护 Bitable「谁该写方案未写」名单(那是二层监督者的事).
- 不与 `admin-finance-governance` 的金额/假别阈值混用.
- 不替上级做中事共同决策, 不替第一责任人拍大事板.
- 提醒不是 RFC 结构检查; 检查交给 `proposal-writing-standard`.

## 别做的事(一层硬边界)

- 不因「疑似中事」拒绝回答或拒绝执行用户明确要求的下一步(除非另有安全/权限硬规则).
- 不把「提醒过了」写成「用户已同意写方案」或「方案已通过」.
- 不在本 skill 内复制技术方案 11 章全文 —— 需要时引用 `proposal-writing-standard`.
