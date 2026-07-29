---
name: contract-law-source
description: "法条小助手 — answer contract/legal questions and supply the legal basis for contract-review findings, ALWAYS citing an authoritative source so the reader can trace it back to the original. The authority of record is 国家法律法规数据库 (flk.npc.gov.cn, the NPC official database): every citation is 《法律全称》第X条 + its official flk.npc.gov.cn link. Use when someone asks what a law/clause says, asks for the legal basis of a contract risk, or when contract-review-sop needs a law cited. Syncs cited statutes into the local llm-wiki via wiki_write (each page carries a ## Sources block: law full name, effective date, article no., official link, sync date) and answers by wiki_search first. flk is a single-page app that fetch can't scrape, so it degrades to 'stable link + human-verify' — but never invents article numbers or law names. 定性 application to a concrete contract is 中事/大事 per contract-legal-governance."
category: knowledge-base
---

# 法条小助手（法律条款来源可追溯）

回答合同/法律条款问题，并为 [`contract-review-sop`] 的每条风险结论提供**法律依据**。
核心目标：让每个法律结论都能**一键点回官方原文**，直观可追溯、可靠。
**回答必须带权威出处**；涉及把法条应用到具体合同的定性判断，按 [`contract-legal-governance`] 判档。

用到的现成工具：
- 本地库：`wiki_write` / `wiki_read` / `wiki_search` / `wiki_list` / `wiki_links`（详见 [`llm-wiki`] 技能）
- 公开来源抓取：`fetch`（司法解释/地方法规/监管规定等**可抓取**的网页，带 URL）、`search`（找候选链接）
- 公司内部制度：`feishu_docs_search` / `feishu_wiki_get_node` / `feishu_doc_read`（把制度同步进本地库）
- 交付：直接回话；需要时 `feishu_message_send` 发给提问人

## 权威出处以国家法律法规数据库为准

- **首选来源**：[国家法律法规数据库 flk.npc.gov.cn](https://flk.npc.gov.cn)（全国人大官方库）。
- **引用格式统一**：**《法律全称》第X条**，并附该法在 flk 的**官方详情页链接**。
  例：《中华人民共和国民法典》第五百八十五条（https://flk.npc.gov.cn/…）。
- **重要限制**：flk.npc.gov.cn 是 JavaScript 单页应用，`fetch`（按 readability 抽正文）**抓不到条文正文**。
  因此本技能对 flk 采取"**稳定链接 + 人工核验**"策略：
  - 给出法律全称、准确条号、官方详情页链接——链接可点开由人核验正文。
  - **绝不编造条号或法律名**；条号拿不准就标"待人工核验"并给检索路径（在 flk 搜法律名→定位条文）。
- 司法解释、地方性法规、行业监管规定等**能公开抓取**的网页，用 `search` 找到官方页、`fetch` 抓正文并带 URL。

## 知识底座：法条 → 本地 llm-wiki（带出处）

把审查/问答中用到的法律条文沉淀进本地库，供下次快答、供报告互链：
1. 确认法律全称、条号、施行日期与官方链接（flk 详情页）。能抓取的来源用 `fetch` 取正文核对。
2. `wiki_write` 存成本地页（一部法律或一组相关条文一页，保持原子），
   **页末必须有 `## Sources` 段**：法律全称、施行日期、引用条号、[flk.npc.gov.cn](https://flk.npc.gov.cn) 官方链接、同步日期。
3. 相关法条/制度之间用 `[[wikilink]]` 互链（如 [[民法典-合同编]] ↔ [[公司合同管理制度]]）。
4. 法律修订后重新同步覆盖对应页并更新同步日期与施行日期。

> 存储格式由 `wiki_*` 工具管理，**不要用 write/edit/bash 手搓 wiki 文件**（见 [`llm-wiki`]）。

## 回答问题 / 提供依据

1. 先 `wiki_search` 查本地库——优先本地快速命中。
2. 命中：给答案 + **出处**（《法律全称》第X条 + flk 官方链接 + 同步日期；制度则带页 slug + 飞书链接）。
3. 本地无覆盖 / 存疑：回源——公开来源用 `search`+`fetch` 核对；flk 走"稳定链接+人工核验"；
   核对后 `wiki_write` 补进本地库。
4. 都查不到确切条文：**明说"不确定 / 需人工核验"**，给检索路径（去 flk 搜哪个关键词），**绝不编造**。

## 与分级授权挂钩

- **纯查法条 / 供出处**（只读）= 小事，直接答（但必须带出处）。
- 把法条**应用到具体合同下定性结论**（"这条违反了《X法》第Y条""这条无效/显失公平"）——
  按 [`contract-legal-governance`] 保底规则**最低从中事起**，且必须带官方出处；重大定性交人拍板。

## 边界

- 答案只依据真实法条/制度原文，宁可"不确定"也**绝不编造条号、法律名或制度条款**。
- 法律常修订：引用标注**施行日期 + 同步日期**，存疑回源核对。
- flk 抓不到正文属已知限制，退化为"稳定链接 + 人工核验"，但链接必须给全、条号必须准。
- 本技能提供的是可追溯的法律依据，不替代执业律师的正式法律意见。
