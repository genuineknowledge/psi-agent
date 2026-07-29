根据 `skills/stage08-synthesis-safety-feasibility-judge/SKILL.md`，启动
`synthesis_safety_feasibility_judge_agent`，在
`synthesis_route_design_step` 产出 96 wells 累计合成路线后运行；读取
`<output_root>/08-round-parallel-synthesis-advisor/CUMULATIVE_SYNTHESIS_ROUTE.md`
作为 Stage08 96-well plate synthesis route，逐个催化剂独立判断化学安全性和
合成可行性，不调用 ChemSkills，不修改 Stage08 路线文件，不新增路线中不存在的
催化剂；输出
`<output_root>/09-synthesis-safety-feasibility-judge/rounds/<round_id>/SYNTHESIS_SAFETY_FEASIBILITY_JUDGMENT.md`
和
`<output_root>/09-synthesis-safety-feasibility-judge/rounds/<round_id>/SYNTHESIS_SAFETY_FEASIBILITY_JUDGMENT.md.audit.json`。
Markdown 中每个催化剂章节必须以三级标题
`### <index>. <catalyst name or formula>` 开始；每个催化剂必须分别给出
不少于 500 个中文字符的化学安全性理由和不少于 500 个中文字符的合成可行性
理由，并在 audit JSON 中记录 `total`、`min_safety_reason_chinese_chars`、
`min_feasibility_reason_chinese_chars`、`missing_safety_conclusion`、
`missing_feasibility_conclusion`、`missing_step_table` 和 `records`。
