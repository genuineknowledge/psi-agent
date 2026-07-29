---
name: contract-review-sop
description: "The company's legal contract-review SOP — read a contract from Feishu (doc/drive/wiki), check it clause-by-clause against the user's review checklist and company policy, and produce a review-opinion report where EVERY risk finding cites its source: the company policy page (llm-wiki slug + Feishu link) AND the law (《法律全称》第X条 + official flk.npc.gov.cn link) so the reader can trace it back. Use when asked to 审查/审阅/review/redline a contract, assess contract risk, or draft a review opinion. Reads the source with feishu_doc_read / feishu_file_download (+ocr) / feishu_docs_search, cites law via contract-law-source, writes the report with feishu_doc_create + feishu_doc_append_content or inline feishu_drive_add_comment, logs a row via contract-ledger, and grades every step per contract-legal-governance (审查意见=中事, 定性/放行用印=大事). Never invents clauses, article numbers, or law names."
category: productivity
---

# 公司法律合同审查 SOP（审查 + 出意见报告）

从飞书读一份合同，按**用户给的审查清单 + 公司制度 + 法律依据**逐条比对，
出一份**每条风险都带出处**的审查意见报告，让人能一键点回原文与法条，直观可追溯。
这是合同法务的核心技能。**分级口径以 [`contract-legal-governance`] 为准**——先加载总纲再执行。

> 定位：海豚出的是**审查意见（中事）**，不是执业律师的正式法律意见。给"可签/不可签"
> 的定性结论、放行对外用印、涉诉讼仲裁风险，都是**大事**，交人拍板。

用到的现成工具：
- 读合同：`feishu_docs_search` / `feishu_wiki_get_node` / `feishu_doc_read`；
  PDF/扫描件/WPS 导出件用 `feishu_file_download` + [`ocr-and-documents`] 技能取文本
- 法条出处：走 [`contract-law-source`]（法条小助手），每条结论带 **《法律全称》第X条 + [flk.npc.gov.cn](https://flk.npc.gov.cn) 链接**
- 制度出处：`wiki_search` / `wiki_read` 查本地已同步的公司制度页（带 slug + 飞书原链，见 [`llm-wiki`]）
- 出报告：`feishu_doc_create` + `feishu_doc_append_content` 建审查意见文档；或在合同原文上 `feishu_drive_add_comment` 逐条批注
- 留痕：`contract-ledger` 写/更新台账行（审查结论摘要 + 状态 + 出处）
- 发起用印/合同审批（大事，确认后）：`feishu_approval_get_definition` + `feishu_approval_create`

## 每次审查先向用户确认（缺就问，别猜）

1. **审哪份合同** — 飞书链接 / 搜索关键词（doc / drive / wiki）。
2. **我方立场** — 我方是甲方还是乙方 / 采购方还是供应方？审查视角随立场变。
3. **审查清单 / 关注点** — 用户明确要审的条款项（见下方"通用审查维度"可作默认建议，但**以用户给的为准**）。
4. **依据口径** — 以哪版公司制度、哪些法律为准（如《民法典》《劳动合同法》《公司法》等）；
   有指定就先同步进本地 wiki（见 [`contract-law-source`]）。
5. **报告去向** — 输出成独立飞书文档、还是在原文上逐条批注、还是两者都要；发给谁。

清单缺失时，先给"通用审查维度"让用户确认或增删，**不自行认定审查范围**。

## 通用审查维度（供用户选，非硬编码规则）

- **主体资格**：双方名称与营业执照/统一社会信用代码是否一致、有无签约权限。
- **标的与质量**：标的物/服务描述是否清晰、验收标准是否明确。
- **金额与支付**：金额大小写一致、币种、支付节点与条件、发票与税费。
- **权利义务**：双方义务对等性，是否有明显不利于我方的单方义务。
- **违约责任**：违约金比例是否合理（过高可主张调整）、赔偿范围、免责条款。
- **期限与解除**：合同期限、续约、单方/协商解除条件是否公平。
- **知识产权 / 保密**：归属、使用许可、保密期限与责任。
- **争议解决**：管辖法院/仲裁机构约定是否明确、是否对我方便利。
- **合规红线**：是否含违法、显失公平、无效条款（对照法律逐条给出处）。

## 流程

1. **读原文**：按用户给的定位方式取到合同。docx/doc/sheet 用 `feishu_doc_read`；
   PDF/扫描件/WPS 导出件用 `feishu_file_download` 下来再走 [`ocr-and-documents`] 取文本。
   涉及用户私有文档时，传 `<feishu_context>` 的 `sender_open_id` 作 `user_key` 以本人身份读。
2. **逐条比对**：对清单里每一项，从合同里定位对应条款（引原文），判断是否齐全/合理/合规。
3. **每条结论带双重出处**（核心，见下节格式）：
   - **制度依据**：`wiki_search` 查公司制度页，引 slug + 飞书原文链接 + 同步日期。
   - **法律依据**：走 [`contract-law-source`]，引 **《法律全称》第X条** + [flk.npc.gov.cn](https://flk.npc.gov.cn) 官方链接。
   - 查不到确切条号或制度页 → 标 **"待人工核验"** 并给检索路径，**绝不编造条号/法律名/制度条款**。
4. **风险分级**：每条标风险等级（高/中/低）与处置建议（建议修改为…/建议删除/可接受）。
   给"可签/不可签"这类**定性总结论是大事**，只在用户要求且明确同意后给，否则止于逐条意见。
5. **出报告**：`feishu_doc_create` 建"合同审查意见-<合同名>-<日期>"，`feishu_doc_append_content` 写入
   （结构见下）；或/并在原文上 `feishu_drive_add_comment` 把每条意见批注到对应位置。
6. **留痕**：用 [`contract-ledger`] 写/更新该合同的台账行（状态改"审查中"→出意见后按结论，
   `审查结论摘要` 填一句话 + 报告链接）。
7. **（大事，确认后）发起审批**：若要提交用印/合同审批，`feishu_approval_get_definition` 读表单模板、
   映射真实字段 id（**绝不编造字段**），把拼好的标准表单**逐字段回显给用户确认**后，
   `feishu_approval_create(applicant_open_id=<sender_open_id>)` 代本人提交；返回 `ok=false` 如实回报，不谎报成功。

## 报告结构（每条都带出处）

```
# 合同审查意见 · <合同名>
- 合同链接 / 审查范围 / 我方立场 / 依据版本 / 审查日期

## 一、总体意见
（若用户要定性结论——大事，需确认；否则写"逐条意见如下，定性结论待决策"）

## 二、逐条审查
### 1. <审查项，如"违约责任">
- 合同原文：<引用条款原文/条号>
- 问题：<发现的风险点>
- 制度依据：[[公司合同管理制度]]（wiki slug: xxx，飞书链接，同步 2026-07-23）
- 法律依据：《中华人民共和国民法典》第五百八十五条（https://flk.npc.gov.cn/…）
- 风险：高 / 中 / 低
- 建议：<改为…/删除/可接受>

## 三、待人工核验清单
（查不到确切出处、需律师确认的点，逐条列检索路径）
```

## 边界

- 绝不编造合同条款、条号、法律名、制度条款或字段 id；查不到就标"待人工核验"并给路径。
- 每条风险结论必须带**法律出处**（《法律》第X条 + flk 官方链接），无出处不下定性结论。
- 审查意见是中事（出结论交人决策）；定性"可签/不可签"、放行用印、代提交审批是大事，先确认。
- 合同含商业敏感与他方信息：报告只发指定接收人，不群发未确认内容。
- 本技能不替代执业律师的正式法律意见；重大/疑难合同建议交法务或外部律师复核。
