`shutdown_step` 的 agent 任务指令。该指令要求 `main_coordinator_agent` 读取
`<output_root>/08-round-parallel-synthesis-advisor/CUMULATIVE_SYNTHESIS_ROUTE.md`；
以该文档中是否已经包含 96 个催化剂的合成路线作为终止 flag。只有达到
96 个催化剂合成路线，且合成路线可行性分析结果已经产出时，才关闭 workflow；
关闭时必须关闭仍然活跃的 `recommender_1_agent`、`recommender_2_agent`、
`recommender_3_agent`、`recommender_4_agent`、
`performance_prover_agent`、`crystal_generation_evaluation_agent`、
`synthesis_route_designer_agent` 和
`synthesis_safety_feasibility_judge_agent`，并写出 `workflow_closed`。
如果未达到 96 个催化剂的合成路线，或合成路线可行性分析结果尚未产出，
则不得关闭 workflow。
