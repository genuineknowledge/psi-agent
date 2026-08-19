# Extract the runtime role catalog

Return exactly one JSON object with `role_catalog_draft` as its sole top-level key. Do not return Markdown, prose, tool calls, `role_key`, or a source hash. The deterministic validator adds identity and revision fields.

## Source boundary

- Read only the one `reference_documents` item whose `purpose` is `role_information`.
- Treat its complete `content` as the only source of recruitment roles.
- Do not use resumes, prior talent records, general knowledge, cached roles, or the local `role-requirements.json`.
- The current-demand summary or complete-position table is authoritative for the set of openings. Earlier demand tables may add details, but fulfilled allocation rows are history rather than additional open roles.
- Ignore historical candidate matching, candidate names, candidate scores, alternatives, interview examples, and links to other documents. In particular, content under headings such as `人才库匹配` cannot support any role field.

## Output contract

```json
{
  "role_catalog_draft": {
    "schema_version": "1.0",
    "roles": [
      {
        "name": "exact position name from the source",
        "employment_type": "实习|正式|正式/实习|未说明",
        "location": "exact source location or 未说明",
        "headcount": 1,
        "status": "active|inactive|unclear",
        "responsibilities": ["exact source text"],
        "hard_requirements": ["exact source text"],
        "preferences": ["exact source text"],
        "source_evidence": [
          {
            "section": "source heading path",
            "text": "short exact substring copied from the source"
          }
        ]
      }
    ]
  }
}
```

Each role object must contain exactly the nine displayed fields.

## Extraction rules

1. A role is a concrete position that a candidate could be matched to. Do not emit broad direction labels, table column names, people, projects, demand codes, or historical candidate cases as roles.
2. Preserve the source position name. Do not improve, expand, merge, translate, or invent it. If the same normalized name appears with conflicting requirements, retain both source evidence sets and set the role to `unclear`; do not silently choose one.
3. Use `active` only for an explicitly pending/open role or a role included in the authoritative current-opening summary. Explicitly filled, paused, closed, or no-longer-recruiting entries are not active. Use `unclear` when the current recruitment state cannot be established.
4. `headcount` must be the positive integer stated by the source. Do not infer headcount from candidate examples or duplicate a grouped headcount across multiple invented roles.
5. `responsibilities`, `hard_requirements`, and `preferences` contain concise exact source substrings. Use an empty list when the source does not state that category. Common hard requirements may be copied to each active role when the source explicitly says they apply to all roles.
   地点、城市、线下到岗和通勤信息一律视为低权重 `preferences`，不得进入 `hard_requirements`，即使源文档把它们写在“共性要求”中。
6. `source_evidence` must contain exact, short substrings sufficient to prove the position name, current status/headcount, and every non-empty requirements list. Never paraphrase evidence.
   Every `text` value must be a **single contiguous substring** of the supplied
   `reference_documents[].content`, including its literal spacing. Before returning,
   check each value with the equivalent of `text in source_content`.
   For table-shaped source content, quote one cell or one source line per evidence
   item. Do **not** reconstruct a row by joining cells with `|`, `/`, commas, or
   inserted newlines. For example, if the source contains newline-separated cells
   `岗位A`, `实习`, `1`, and `9月中`, those are four valid evidence texts; the
   invented string `岗位A | 实习 | 1 | 9月中` is invalid even if it describes the
   same row. If a fact spans multiple source lines, emit separate evidence items
   rather than one multi-line reconstruction. If a proposed quote fails the literal
   containment check, replace it with a shorter contiguous source cell/line; never
   return the failing quote.
7. Do not emit an empty role list. If the document cannot produce at least one certain active role, still return the best source-grounded draft with uncertain statuses; the validator will block and report it.

Before returning, verify that no role name or evidence text comes from `人才库匹配`, no active role contains a pause/filled marker, and every quoted string occurs verbatim in the source content.
