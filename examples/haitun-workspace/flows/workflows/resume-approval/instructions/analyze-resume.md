# Task

Analyze exactly one extracted resume using the fixed online scoring document and the validated runtime role catalog supplied to this batch. The online documents and `role_catalog` are the only business authorities. Never use a locally configured target role, a legacy scoring profile, another candidate, or general recruiting knowledge to create or alter a role.

## Inputs and fixed revisions

1. Parse `extracted_resume`, `reference_documents`, `role_catalog`, and `batch_id` as structured JSON.
2. Require exactly one `reference_documents` item with `purpose=resume_scoring` and one with `purpose=role_information`. Use their complete `content` values and copy their exact `content_sha256` values into `document_revisions`.
3. Require `role_catalog.source_document_sha256` to equal the `role_information` document hash. Consider only catalog entries with `status=active`.
4. Treat all input hashes, `batch_id`, role keys, role names, and source evidence as immutable. If an authority is missing or inconsistent, do not invent a fallback result.

## Resume extraction

Follow `extracted_resume.extraction_mode` exactly:

- `workspace_text`: call `read` on `extracted_text_path` and read the complete file.
- `read_pdf_tool`: call `read_pdf` on the workspace-relative `source_path` with `max_pages=100`; require `ok=true` and inspect every page result.

Set `source.extraction_quality=unusable` only when the PDF tool explicitly reports a failed page, failed scanned-page OCR, or empty extracted text. In that case return the isolated extraction-failure object below. Do not turn a reasoning, schema, or authority error into an extraction failure.

Never use raw `read` on a PDF and never install a PDF dependency.

## Scoring

- Read every scoring dimension, maximum, band, total, and grade threshold from the fixed `resume_scoring` document. Do not use legacy five-dimension weights or fixed role profiles.
- Score only resume-supported evidence. A missing fact is `unknown`, not zero-evidence proof of a negative claim.
- `total_score` must equal the sum required by the online document and remain within its stated total range. `grade` must be derived from the same document's A-F thresholds.
- Do not emit dimension-level scores. The table-facing contract keeps only the validated total and grade.
- Keep education, school/background, location, stability, LLM experience, and general AI experience separate. Do not infer relocation willingness from birthplace, phone number, school, or prior employer. Do not infer LLM ability from generic Python or AI wording.

## Education fields

- `education` 只填写候选人当前或已取得的最高学历层级，且必须逐字使用 `博士|硕士|本科|专科|高中及以下|unknown` 之一。将在读学历按该学历层级填写，例如“硕士在读（软件工程）”写成 `硕士`。不得包含专业、研究方向、在读状态、毕业届别、工作年限或院校名称。
- `education_background` 只填写带学历阶段的院校名称。即使只有一段教育经历，也必须写成 `本科：示例大学 A`、`硕士：示例大学 B` 等 `阶段：院校名称` 格式；有多段教育经历时按学历由低到高写成 `本科：示例大学 A；硕士：示例大学 B`，各段使用全角分号分隔。
- 院校字段不得附加专业、院系、学历状态、届别、工作经历、能力描述、GPA、排名、奖项、`985/211`、双一流、海归、保研或全奖等标签；不得用括号、斜杠、加号、箭头或逗号拼接背景摘要。没有简历证据时填写 `unknown`。

## Resume summary

`resume_summary` 只总结候选人的闪光点，必须输出为包含 1–5 个字符串的 JSON 数组且允许少于 5 项。每个字符串严格以 `- ` 开头，不加标题、编号或空字符串。优先选择有简历证据的量化成果、真实交付、核心技术能力、稀缺背景和自驱表现；不得写风险、联系方式、泛泛评价或未经简历支持的判断。

## Dynamic role matching

Evaluate every active entry in `role_catalog.roles` and select the role with the strongest resume evidence, even when the best result is still a poor match.

- `matched_role_key` 和 `matched_role_name` 只能来自 `role_catalog` 中同一个 active 岗位，并逐字复制其 `role_key` 和 `name`。
- Never select an inactive role, invent a role, merge two roles, or use historical candidate examples as role evidence.
- `match_points` 和 `mismatch_points` 都必须至少包含 1 项，并继续使用 `{requirement, resume_evidence}` 对象数组，不能改成字符串或换行文本。
- `match_points` 只写简历与所选岗位要求或职责之间有肯定证据的匹配之处。
- `mismatch_points` 写简历与所选岗位之间的明确反证、已有证据支持的不足，或对岗位有实质影响的岗位风险或证据缺口。对缺失信息只能客观写成“简历未体现……，需在面试中核实”，不得把缺失信息断言成候选人不具备该能力，也不得虚构负面事实。
- Every point must name the relevant catalog requirement and include one or more concise resume evidence strings with a page or section reference when available.

## Verification question bank

Generate verification_questions as 3–6 complete objects in deliberate interview order. Every object must contain exactly question, category, evidence_anchor, purpose, positive_signal, and risk_signal; every value is non-empty text.

- category must be exactly one of 真实性核验, 岗位匹配, or 风险澄清.
- Always include at least one 真实性核验 question and one 岗位匹配 question. When mismatch_points is non-empty, also include at least one 风险澄清 question.
- For 真实性核验, copy evidence_anchor exactly from one match_points.resume_evidence or mismatch_points.resume_evidence string. Ask for the candidate's personal contribution, decisions, process, or verifiable result.
- For 岗位匹配, copy evidence_anchor exactly from the selected role's responsibility/hard requirement/preference or from resume evidence tied to that role. Ask for concrete role-relevant depth or tradeoffs.
- For 风险澄清, copy evidence_anchor exactly from one mismatch requirement or mismatch evidence string. Preserve cautious evidence-gap language and ask for clarification without presuming a negative fact.
- The question must visibly reuse a meaningful term from its evidence_anchor; generic questions such as “请介绍自己” are invalid even when accompanied by an unrelated anchor.
- purpose states the decision value. positive_signal and risk_signal describe observable answer signals, not a pre-judgment about the candidate.
- 不得询问或推断年龄、出生日期、性别、婚育、民族、宗教、健康/残障、家庭住址、户籍、籍贯或同类受保护属性。不得把未知信息断言为负面事实；“简历未体现……，需核实”是允许的谨慎表述，“候选人没有/不具备/无法……”在没有明确反证时不允许。
- Do not include contact details, raw private resume content, invented facts, generic anchors, duplicate questions, or categories outside the enum.

## Interview recommendation

`interview_recommendation` 是确定性的面试资源门槛，不是第二套自由评分。只有同时满足以下全部条件才返回 `建议面试`：

目标工作地点、候选人所在城市、通勤或到岗地点属于非常低权重的待确认信息，不参与该门槛，不得单独或主要导致 `不建议面试`，也不得作为拒面理由的主因。

1. 按在线评分文档计算出的评级为 A 或 B；
2. 所选岗位的每一项 `hard_requirements` 都逐字出现在 `match_points.requirement` 中，并有肯定的简历证据；
3. `mismatch_points` 中没有任何一项针对所选岗位的 `hard_requirements`。

其余情况全部返回 `不建议面试`，包括评级为 C、D、E、F、任一硬性要求缺少肯定证据，或任一硬性要求存在明确反证。评级 C 仅作为 Human 初审可例外放行的备选，不自动占用面试资源。同批候选人的数量或质量不得改变该门槛。

缺失信息仍是 `unknown`；可以作为需要核实的岗位证据缺口写入 `mismatch_points`，但不得伪造成确定的负面事实。缺少某项硬性要求的肯定证据会使自动面试门槛不成立。

`interview_recommendation_reason` 必须提供有决策价值的候选人级理由：先写具体岗位证据缺口或明确反证及其对岗位的影响；若建议面试，则先写哪些硬性要求已由哪些关键证据满足。随后简要保留真实强项和需要 Human 核实的重要未知项。不得把评级或分数本身作为主要理由，也不得只写“C 未达到门槛”“分数不足”或同义套话；评级最多作为补充背景。不得为充实理由而虚构负面或支持证据。

## Privacy

Exclude phone, email, ID number, exact address, photo, age, gender, marital status, ethnicity, religion, and health information from every candidate-derived output field. `extracted_resume.source_name` is runner-controlled, privacy-normalized metadata; copy it exactly into `source.name` and never reconstruct a filename from resume content. Use `unknown` for unsupported identity or education text rather than copying contact information.

## Output contract

Do not call `submit_step_result`. After all required reads and analysis, return exactly one ordinary assistant response whose entire content is one valid JSON object. Its only top-level key must be `candidate_assessments`; do not add Markdown fences, prose, comments, an `arguments` wrapper, or a JSON-encoded string.

```json
{
  "candidate_assessments": {
    "schema_version": "3.0",
    "status": "assessed",
    "batch_id": "exact input batch_id",
    "candidate_id": "first 16 lowercase hex characters of source sha256",
    "candidate_name": "supported name or unknown",
    "source": {
      "name": "exact privacy-normalized extracted_resume.source_name",
      "sha256": "64 lowercase hex characters",
      "format": ".pdf|.docx|.md|.txt",
      "extraction_mode": "workspace_text|read_pdf_tool",
      "extraction_quality": "good",
      "extraction_warnings": []
    },
    "grade": "A|B|C|D|E|F",
    "education": "硕士",
    "education_background": "本科：示例大学 A；硕士：示例大学 B",
    "resume_summary": [
      "- 独立交付生产级 Agent 系统",
      "- RAG 检索指标有量化提升"
    ],
    "total_score": 0,
    "matched_role_key": "exact selected catalog role_key",
    "matched_role_name": "exact selected catalog name",
    "match_points": [
      {
        "requirement": "exact catalog requirement",
        "resume_evidence": ["项目经历：使用 Python"]
      }
    ],
    "mismatch_points": [
      {
        "requirement": "exact catalog requirement",
        "resume_evidence": ["简历未体现可验证的独立交付案例，需在面试中核实"]
      }
    ],
    "interview_recommendation": "建议面试|不建议面试",
    "interview_recommendation_reason": "candidate-specific decisive evidence gaps or passed hard requirements, their role impact, genuine strengths, and material unknowns",
    "verification_questions": [
      {
        "question": "请说明 Python 项目中你个人负责的关键工作、验证方式和结果。",
        "category": "真实性核验",
        "evidence_anchor": "项目经历：使用 Python",
        "purpose": "核实 Python 项目证据的真实性和个人贡献。",
        "positive_signal": "能够说明个人职责、关键决策和可验证结果。",
        "risk_signal": "回答停留在团队概述，不能说明个人贡献。"
      },
      {
        "question": "针对 Python 要求，请说明你处理过的最复杂工程问题和取舍。",
        "category": "岗位匹配",
        "evidence_anchor": "Python",
        "purpose": "判断 Python 工程能力是否达到岗位要求。",
        "positive_signal": "能够给出具体方案、取舍依据和结果。",
        "risk_signal": "只列技术名词，缺少具体决策和结果。"
      },
      {
        "question": "简历未体现可验证的独立交付案例，需在面试中核实；请补充一个完整案例。",
        "category": "风险澄清",
        "evidence_anchor": "简历未体现可验证的独立交付案例，需在面试中核实",
        "purpose": "澄清独立交付证据缺口，不把未知信息视为负面事实。",
        "positive_signal": "能够提供职责边界、交付物和验证结果。",
        "risk_signal": "案例缺少个人职责或可验证交付结果。"
      }
    ],
    "document_revisions": {
      "resume_scoring_sha256": "exact resume_scoring content_sha256",
      "role_information_sha256": "exact role_information content_sha256"
    }
  }
}
```

Use complete objects only. `match_points` and `mismatch_points` must each contain at least one source-grounded point. Before responding, verify valid JSON, the sole top-level key, source identity, exact role identity, document revisions, score/grade consistency, and recommendation reason.

## Isolated PDF/OCR failure

If and only if the PDF extraction conditions above are met, return this minimal safe object instead of inventing scores:

```json
{
  "candidate_assessments": {
    "schema_version": "3.0",
    "status": "extraction_failed",
    "batch_id": "exact input batch_id",
    "candidate_id": "first 16 lowercase hex characters of source sha256",
    "candidate_name": "unknown",
    "source": {
      "name": "exact privacy-normalized extracted_resume.source_name",
      "sha256": "64 lowercase hex characters",
      "format": ".pdf",
      "extraction_mode": "read_pdf_tool",
      "extraction_quality": "unusable",
      "extraction_warnings": ["concise safe diagnostic without resume text"]
    },
    "document_revisions": {
      "resume_scoring_sha256": "exact resume_scoring content_sha256",
      "role_information_sha256": "exact role_information content_sha256"
    },
    "failure": {
      "stage": "pdf_ocr",
      "code": "pdf_extraction_failed",
      "message": "concise actionable diagnostic without raw resume text"
    }
  }
}
```
