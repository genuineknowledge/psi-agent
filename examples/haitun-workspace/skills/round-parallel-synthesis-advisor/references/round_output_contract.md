# Round Parallel Output Contract

The round-parallel Stage08 output consists of one round markdown route, one
round JSON index, workflow-level cumulative ChemSkills CSV parameter files, and
workflow-level cumulative files. Per-round parameter directories are not part of
the contract.

## Files
Use this layout:

```text
<output_root>/08-round-parallel-synthesis-advisor/rounds/<round_id>/SYNTHESIS_INPUT_SUMMARY.json
<output_root>/08-round-parallel-synthesis-advisor/rounds/<round_id>/ROUND_PARALLEL_SYNTHESIS_ROUTE.md
<output_root>/08-round-parallel-synthesis-advisor/rounds/<round_id>/ROUND_PARALLEL_SYNTHESIS_INDEX.json
<output_root>/08-round-parallel-synthesis-advisor/synthesis-routes/<cumulative_csv_file_name>.csv
<output_root>/08-round-parallel-synthesis-advisor/SOURCE_LIQUID_INVENTORY.json
<output_root>/08-round-parallel-synthesis-advisor/SOURCE_LIQUID_PREPARATION_METHODS.json
<output_root>/08-round-parallel-synthesis-advisor/CUMULATIVE_SYNTHESIS_ROUTE.md
<output_root>/08-round-parallel-synthesis-advisor/CHEMSKILLS_EXECUTION_SPEC.md
<output_root>/08-round-parallel-synthesis-advisor/SOURCE_LIQUID_BOTTLE_PREPARATION.md  # only after the 96-well cumulative route is complete
```

Do not create or update:

```text
<output_root>/08-round-parallel-synthesis-advisor/rounds/<round_id>/synthesis-routes/
```

Every current-round parameter update must be written into the cumulative
workflow-level CSV files under
`<output_root>/08-round-parallel-synthesis-advisor/synthesis-routes/`.

## Route Markdown Sections
Use this section order:

```markdown
# 本轮并行光催化剂合成总路线：<round_id>

## 1. 本轮输入与总体判断

## 2. 保留与阻塞结果

## 3. 单孔板布局

## 4. 总原液预算

## 5. 统一合成方法

## 6. 主要试剂与前驱体

## 7. 具体并行合成步骤

## 8. 关键控制点

## 9. 主要合成风险

## 10. 表征与验证建议

## ChemSkills 可执行总路线

## 前驱体准备要求

## ChemSkills 执行序列

## ChemSkills 操作输入参数

## 累计参数文件内容

## 不可执行或阻塞项
```

The `ChemSkills 可执行总路线` and `ChemSkills 执行序列` sections must contain
only allowed ChemSkills operations. Mention performance validation only in
`表征与验证建议`; do not include it in executable operations.

## Index Schema
`ROUND_PARALLEL_SYNTHESIS_INDEX.json` must contain:

- `round_id`: round identifier
- `input_json`: repository-relative path to `SYNTHESIS_INPUT_SUMMARY.json`
- `route_markdown`: repository-relative path to `ROUND_PARALLEL_SYNTHESIS_ROUTE.md`
- `chemskills_execution_spec`: repository-relative path to
  `CHEMSKILLS_EXECUTION_SPEC.md`
- `source_liquid_preparation_methods`: repository-relative path to
  `SOURCE_LIQUID_PREPARATION_METHODS.json`
- `source_liquid_bottle_preparation`: repository-relative path to the final
  `SOURCE_LIQUID_BOTTLE_PREPARATION.md` when written; otherwise null or absent
- `constraint_source_files`: array of repository-relative authoritative rule
  files read before retain/block decisions; must include the current
  `SYNTHESIS_INPUT_SUMMARY.json` and
  `data/laboratory-limitations/laboratory_limitations_for_agent.json`
- `chemskills_source_files`: array of repository-relative ChemSkills files read
  before route and CSV design; must include `data/chem-skills/README.md` plus
  every station `SKILL.md` and CSV template used by the route
- `plate_count`: exactly `1`
- `plate_id`: exactly `p1`
- `source_liquid_limit`: integer source-liquid capacity derived from the
  authoritative laboratory/ChemSkills files used for this route
- `source_liquid_count`: integer from `0` to `source_liquid_limit` after the
  agent completes the route
- `input_record_count`: number of input records
- `retained_record_count`: number of retained records
- `blocked_record_count`: number of blocked records
- `retained_records`: array of retained record objects
- `blocked_records`: array of blocked record objects
- `source_liquids`: array of source-liquid objects
- `well_map`: array of well assignment objects
- `parameter_csv_files`: array of repository-relative cumulative CSV paths under
  `<output_root>/08-round-parallel-synthesis-advisor/synthesis-routes/`
- `review_status`: `pass`, `blocked`, or `needs_human_review`

The shell script may initialize counts conservatively. The agent must update the
index after writing the final route.

Each retained record object should include:

- `record_index`
- `record_id`
- `system`
- `catalyst_name`
- `well`
- `source_liquid_ids`
- `review_status`

Each blocked record object should include:

- `record_index`
- `record_id`
- `system`
- `catalyst_name`
- `blocked_reason`
- `violated_limitations`

## Completion Checks
The route is incomplete if:

- the route markdown is empty
- any CSV path listed in `parameter_csv_files` is missing
- any CSV path listed in `parameter_csv_files` is under
  `rounds/<round_id>/synthesis-routes/`
- `plate_count` is not `1`
- `plate_id` is not `p1`
- `source_liquid_limit` is missing or unsupported by the authoritative files
  recorded in `constraint_source_files` and `chemskills_source_files`
- `source_liquid_count` is missing or greater than `source_liquid_limit`
- any retained record has no well
- the same record appears in both retained and blocked arrays
- retained plus blocked records do not cover all input records
- `constraint_source_files` is missing or does not include the current input
  JSON and laboratory rule library
- `chemskills_source_files` is missing, does not include
  `data/chem-skills/README.md`, or omits a station file/template required by a
  listed ChemSkills operation or parameter CSV
- `CHEMSKILLS_EXECUTION_SPEC.md` is missing or contains sections outside the
  required three-section contract
- `SOURCE_LIQUID_PREPARATION_METHODS.json` is missing, has duplicate
  `source_liquid_id` entries, or does not cover every allocated source liquid
- `SOURCE_LIQUID_PREPARATION_METHODS.json` reports the 96-well route complete
  but `SOURCE_LIQUID_BOTTLE_PREPARATION.md` is missing or empty
- `rounds/<round_id>/synthesis-routes/` exists

## Workflow-Level Cumulative Files

### SOURCE_LIQUID_INVENTORY.json Schema

```json
{
  "source_liquids": [
    {
      "source_liquid_id": "string",
      "chemical_name": "string",
      "concentration": "string or null",
      "first_used_round": "string"
    }
  ],
  "source_liquid_limit": "integer derived from the authoritative laboratory/ChemSkills files",
  "allocated_count": "integer (0-source_liquid_limit)",
  "available_slots": "integer (0-source_liquid_limit)",
  "last_updated_round": "string"
}
```

This file must be created or updated after each round. For the first round,
initialize with an empty `source_liquids` array and counts derived from the
source-liquid limit confirmed from the authoritative files. For subsequent
rounds, merge new source liquids from the current round's INDEX and update
counts.

### SOURCE_LIQUID_PREPARATION_METHODS.json Schema

This file must be created or updated as soon as Stage08 allocates new source
liquids, using `references/source_liquid_preparation_methods.md`. It
accumulates explicit preparation methods immediately for each new
`source_liquid_id` without writing the final total source-liquid SOP until the
96-well route is complete.

```json
{
  "updated_at": "ISO-8601 timestamp",
  "status": "incremental|complete",
  "final_markdown_written": false,
  "final_markdown_path": null,
  "route_completion_gate": {
    "required_well_count": 96,
    "observed_well_count": 0,
    "is_complete": false,
    "evidence_file": null
  },
  "methods": [
    {
      "source_liquid_id": "string",
      "bottle": "string",
      "element": "string",
      "target_concentration": "string",
      "final_bottle_volume": "string",
      "method_kind": "direct_weighing|working_precursor_dilution|neat_solvent|unused",
      "procurement": ["string"],
      "working_precursor": {"name": "string", "preparation": ["string"]},
      "final_bottle_steps": ["string"],
      "qc_safety": "string",
      "first_seen_round_id": "string",
      "last_updated_round_id": "string",
      "evidence_files": ["string"]
    }
  ]
}
```

Every allocated source liquid in `SOURCE_LIQUID_INVENTORY.json` must have one
matching method entry. When `observed_well_count` reaches 96, set
`route_completion_gate.is_complete=true`, write
`SOURCE_LIQUID_BOTTLE_PREPARATION.md`, set `final_markdown_written=true`, and
store the repository-relative markdown path in `final_markdown_path`.

### CUMULATIVE_SYNTHESIS_ROUTE.md Structure

This markdown file documents the operation pattern that all synthesis routes
must follow. It should include:

```markdown
# 累计合成路线操作模式

## 操作序列约束
[Document the required operation sequence]

## 使用的ChemSkills操作
[List all ChemSkills operations used across rounds]

## 操作参数约束
[Document any parameter-level constraints]

## 保留结果与累计单孔板布局
[List retained records across completed rounds with cumulative p1 wells,
original round ids, original round wells, record ids, catalyst/system
components, source-liquid ids, and total volume where available.]

## 总原液预算与 source-liquid 清单
[Summarize source-liquid usage against the authoritative source-liquid limit,
remaining slots, and the complete source-liquid inventory with bottle columns,
elements, reagent names, concentrations, first-used rounds, and roles where
available.]

## 累计轮次记录
[List rounds that contributed to this pattern]
```

Update this file after each round to reflect the evolving operation pattern. For
the first round, establish the initial pattern. For subsequent rounds, verify
compatibility and document any extensions to the pattern. Do not write this file
as a simple concatenation of per-round route files.

### CHEMSKILLS_EXECUTION_SPEC.md Structure

This markdown file is the concise ChemSkills execution document for the total
cumulative synthesis route. It replaces any separate handoff package. It must
contain exactly the following three sections after the title and no additional
top-level or second-level sections:

```markdown
# ChemSkills 执行输入规范

## 总原液预算与 source-liquid 清单
[Document total source-liquid usage, remaining slots, and the complete source-liquid list.]

## ChemSkills 可执行总路线
[Document only the ChemSkills-executable operation sequence and cumulative CSV parameter file paths.]

## ChemSkills JSON 输入格式
[Document the JSON object or JSON-string format expected by ChemSkills, including references to the cumulative parameter CSV files.]
```

Do not include candidate rationale, synthesis risks, characterization advice,
or non-executable laboratory notes in this file. Those belong in
`ROUND_PARALLEL_SYNTHESIS_ROUTE.md` or `CUMULATIVE_SYNTHESIS_ROUTE.md`.
