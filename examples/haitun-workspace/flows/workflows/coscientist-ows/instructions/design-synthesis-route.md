根据 `skills/round-parallel-synthesis-advisor/SKILL.md` 和
`skills/source-liquid-sop-designer`，启动 `synthesis_route_designer_agent`
将当前 `<output_root>/pools/novel_and_stable_catalysts/slot_n/ows_Sn_Ck`
下的记录作为一个 round-level candidate pool 处理；构建当前轮输入时，
跳过已经出现在 Stage08 目录下已有 `ROUND_PARALLEL_SYNTHESIS_INDEX.json`
文件的 `retained_records` 或 `blocked_records` 中的记录；写入
`<output_root>/08-round-parallel-synthesis-advisor/rounds/<round_id>/SYNTHESIS_INPUT_SUMMARY.json`；
在任何 retain/block 判断之前，读取该 input summary、实验室规则库、
ChemSkills README、所需 station `SKILL.md` 文件、所需参数模板，以及以下
skill reference 文件：
`skills/round-parallel-synthesis-advisor/references/round_route_design.md`、
`skills/round-parallel-synthesis-advisor/references/round_output_contract.md`、
`skills/round-parallel-synthesis-advisor/references/source_liquid_preparation_methods.md`；
路线设计只能使用这些 Stage08 授权输入；使用
`round-parallel-synthesis-advisor` 运行
`generate_round_synthesis_shell.py --input-json
<path-to-SYNTHESIS_INPUT_SUMMARY.json>`；随后在同一个 round 目录中完成
`ROUND_PARALLEL_SYNTHESIS_ROUTE.md` 和 `ROUND_PARALLEL_SYNTHESIS_INDEX.json`；
累计参数 CSV 文件只能写入
`<output_root>/08-round-parallel-synthesis-advisor/synthesis-routes/`；更新
`<output_root>/08-round-parallel-synthesis-advisor` 下的
`SOURCE_LIQUID_INVENTORY.json`、`SOURCE_LIQUID_PREPARATION_METHODS.json`、
`CUMULATIVE_SYNTHESIS_ROUTE.md` 和 `CHEMSKILLS_EXECUTION_SPEC.md`；只要新增
`source_liquid_id`，就必须在本次 Stage08 更新中立即把该原液的完整配制方法
写入 `SOURCE_LIQUID_PREPARATION_METHODS.json`，不得等累计路线完成后再补写；
只有当累计路线达到 96 wells 时才写入 `SOURCE_LIQUID_BOTTLE_PREPARATION.md`；
不得创建
`<output_root>/08-round-parallel-synthesis-advisor/rounds/<round_id>/synthesis-routes`；
强制 `plate_count=1` 且 `plate_id=p1`；`retained_records` 与
`blocked_records` 必须准确覆盖每一条输入记录且每条只覆盖一次；将 blocked
candidate 文件夹从
`<output_root>/pools/novel_and_stable_catalysts/slot_n/ows_Sn_Ck` 移动到
`<output_root>/fail/candidates/slot_n/ows_Sn_Ck`。
