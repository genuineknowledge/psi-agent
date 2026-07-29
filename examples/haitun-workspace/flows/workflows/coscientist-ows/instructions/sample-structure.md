根据 `skills/mattergen-structure-sampler`，启动
`crystal_generation_evaluation_agent` 处理
`<output_root>/pools/candidates/slot_n/ows_Sn_Ck` 下的候选文件夹：读取
candidate payload、reasoning、证明 Markdown 和证明 audit JSON；根据
`CANDIDATE_PAYLOAD.json` 派生 `RECOMMENDED_CANDIDATE.csv`，字段为
`candidate_id`、`candidate_name`、`main_photocatalyst`、
`main_photocatalyst_formula_note`、`preliminary_synthesis_route`、
`laboratory_feasibility_decision`、`violated_laboratory_limitation_ids`、
`laboratory_feasibility_reason`、`difference_from_prior_recommendations`、
`reference_knowledge_ids` 和 `supporting_knowledge`；将 MatterGen 结果文件写入
`<output_root>/04-mattergen-structure-sampler/slot_n/ows_Sn_Ck`：
`RECOMMENDED_CANDIDATE.csv`、`ROUND_MANIFEST.json`、
`STRUCTURE_SAMPLING_PLAN.csv`、`SAMPLING_PARAMETERS.json`、
`SAMPLING_COMMANDS.md`、`samples/ows_Sn_Ck` 和 `runner_logs`；中断续跑时，
先读取该 stage workspace 中已有文件，并在已有结果基础上继续；每个已确认
GPU ID 运行一个 MatterGen 子进程，每个子进程绑定且只绑定一个 GPU ID，
不得在同一个 GPU ID 上并发运行两个 MatterGen 子进程；生成结构通过核验后，
将整个候选文件夹从 `<output_root>/pools/candidates/slot_n/ows_Sn_Ck` 移动到
`<output_root>/pools/structures/slot_n/ows_Sn_Ck`。如果 MatterGen 失败，
或在采样完成前中断，则候选文件夹保留在 `<output_root>/pools/candidates`
中等待重试，不创建 MatterGen 失败池。
