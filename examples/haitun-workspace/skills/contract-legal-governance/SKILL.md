---
name: contract-legal-governance
description: "The tiered autonomy rulebook for all 合同/法务 (contract & legal) work — 小事不问 / 中事少问 / 大事必问. LOAD this first whenever you build/maintain a contract ledger (合同台账), review a contract (合同审查), or answer/act on any contract-legal matter, so every decision uses one consistent 小事(small)/中事(medium)/大事(big) classification, action boundary, source-citation rule, and audit-trail rule. Defines what the bot may do at each tier, the hard rule that any legal-risk finding or clause characterization must cite an authoritative source (国家法律法规数据库 flk.npc.gov.cn) and is 最低从中事起, and how every action must be logged. The contract-ledger / contract-review-sop / contract-law-source skills all reference this file. Edit thresholds via skill_manage."
category: productivity
---

# 合同法务分级授权总纲（小事不问 / 中事少问 / 大事必问）

这是海豚处理一切**合同 / 法务**事务的最高口径。合同台账管理、合同审查、
法条来源问答与执行，都先按本表把事情分到 **小事 / 中事 / 大事** 三档，再决定动手还是问人。

三个下游技能引用本文件：
- [`contract-ledger`] — 公司合同台账建立与管理
- [`contract-review-sop`] — 公司法律合同审查 SOP（审查 + 出意见报告）
- [`contract-law-source`] — 法律条款来源可追溯（法条小助手，带官方出处）

> 本纲只定"该不该做、谁来拍板、结论要不要带出处"；具体怎么读合同、怎么建台账、
> 怎么出审查报告，见下游技能。合同法务不替代执业律师意见——大事一律交人拍板。

## 三档的核心定义

| 档 | 海豚该做什么 |
|----|-------------|
| **小事** | **直接做**，事后一句话留痕。内部/可逆动作（读合同、汇总、归档、建/更新台账行、查制度与法条并带出处）一律照做。 |
| **中事** | **只出结论 + 依据（带出处），不动手**。给出"审查意见 / 建议通过或修改 + 依据"，等人确认或在飞书里点。 |
| **大事** | **先问用户**。汇总清楚、给出建议与风险点，但对外发合同 / 提交用印审批 / 删文件 / 改台账关键字段 / 对法律风险下定性结论前，必须拿到用户明确同意。 |

**保底规则（任何情况下从严）**：涉及以下动作**最低从中事起**，小事阈值不适用：
- **对法律条款做定性或风险结论**（是否违法、是否显失公平、违约责任是否过重、能否解除等）——
  且结论**必须带权威出处**（法律名 + 条号 + 官方链接），无出处不得下结论。
- 对外发出合同、提交盖章/用印审批、代提交任何合同类审批（`feishu_approval_create`）。
- 删除合同文件（`feishu_drive_delete_file`）、改台账里已确认的关键字段（金额/主体/到期日/状态）。
- 对外广播 / 群发合同或审查结论、触碰他人隐私或商业秘密数据。

## 默认分档口径（可用 `skill_manage` 改本文件调整）

### 合同审查（`contract-review-sop`）
- **小事**：仅**读取、摘要、归档、录台账**合同信息；不下任何法律定性结论。
- **中事**：出**审查意见 + 依据（带出处）**，含风险点与修改建议；不代签、不代提交审批。
  绝大多数合同审查落在这一档——出意见交人决策。
- **大事**：给出"可签 / 不可签"的定性结论、放行对外用印、金额或期限重大、
  涉及诉讼/仲裁风险、条款疑似违法或显失公平、对方主体资格存疑、缺关键条款。

### 合同台账（`contract-ledger`）
- **小事**：新建台账、追加台账行、更新非关键字段（备注、负责人、原件链接）、按到期日**汇总提醒**。
- **中事**：更新关键字段（金额 / 签约主体 / 到期日 / 状态）——先出改动说明，等确认。
- **大事**：删除台账记录 / 清空表 / 删除合同原件文件。

### 法条来源（`contract-law-source`）
- **小事**：查法条、把法条同步进本地 llm-wiki、按**带出处**格式回答。
- **中事/大事**：把法条应用到具体合同、给出"这条违反了《X法》第Y条"这类**定性判断**——
  按上面"保底规则"处理，最低从中事起，且必须带官方出处。

> 阈值是默认值，不是铁律。用户当次给了不同规则，以用户当次为准；材料不足以判定时，**向上一档**。

## 出处即证据：法条引用的硬约束

让人"直观感觉靠谱、可追溯源头"是本套技能的核心目标。任何法律结论都必须能一键点回官方原文：
- **权威出处以国家法律法规数据库为准**：[flk.npc.gov.cn](https://flk.npc.gov.cn)（全国人大官方库）。
- 引用格式统一为 **《法律全称》第X条**，并附**官方详情页链接**（或该法在 flk 的稳定链接）。
- 公司内部制度依据用 [`contract-law-source`] / [`contract-review-sop`] 里同步进本地 llm-wiki 的页，
  引用时带**页 slug + 飞书原文链接 + 同步日期**（用 `wiki_search` / `wiki_read` 取，见 [`llm-wiki`] 技能）。
- **flk.npc.gov.cn 是单页应用，`fetch` 抓不到正文**：抓不到就退化为"稳定链接 + 人工核验"提示，
  但**链接必须给全、条号必须准**；抓不准就标"待人工核验"并给检索路径，**绝不编造条号或法律名**。

## 每次动作必须留痕（小事也要）

海豚每做一个动作，必须：
1. 在合同台账（`feishu_bitable_create_record`）里写一行或更新对应行（事项 / 关键字段 / 判定档位 / 处置 / 依据 / 时间）。
2. 用一句话说明为什么判为该档、依据哪条口径或哪条出处。
3. 涉及审批放行的，以**真实审批人的 user_id** 通过 `feishu_approval_decide` 执行；
   代员工发起合同审批用 `feishu_approval_create` 且申请人记在本人 open_id 名下（见 [`contract-review-sop`]）。

留痕不可省略：没有台账记录的动作视为未完成。

## 边界

- 绝不编造合同条款、金额、主体、法律名或条号；判不准就往大事走、交人拍板。
- 法律结论必须带权威出处，无出处不下定性结论；本套技能不替代执业律师的正式法律意见。
- 合同与商业数据敏感：下载/汇总只落在用户指定位置，结论只发给指定接收人，群发广播按大事确认。
- 未经用户确认不对外发合同、不提交用印审批、不删文件、不改台账关键字段。
