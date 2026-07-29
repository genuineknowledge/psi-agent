根据 `skills/mattersim-structure-evaluator`，启动
`crystal_generation_evaluation_agent` 以每批 8 个候选的方式处理
`<output_root>/pools/structures/slot_n/ows_Sn_Ck` 下的候选文件夹，并在
`<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>`
下创建 batch workspace；写入 `STRUCTURE_SAMPLING_PLAN.csv`、
`EVALUATION_PLAN.csv`、`COMBINED_EVALUATION_PLAN.csv`、
`STRUCTURE_EVALUATION_SUMMARY.csv`、`combined/generated_crystals.extxyz`、
`combined/detailed_metrics.json`、`combined/relaxed_structures.extxyz` 和
`evaluations/<candidate_id>/` 输出；每个已确认 GPU ID 运行一个 MatterSim
batch，每个 batch 绑定且只绑定一个 GPU ID，不得在同一个 GPU ID 上并发运行
两个 MatterSim batch；如果某个候选至少有一个稳定且新颖的结构，并且该结构的
`recommended_return_step` 为 `proceed_to_experimental_validation`，则视为通过，
并将其文件夹从 `<output_root>/pools/structures/slot_n/ows_Sn_Ck` 移动到
`<output_root>/pools/novel_and_stable_catalysts/slot_n/ows_Sn_Ck`；将已经判断为
不通过的候选文件夹移动到
`<output_root>/fail/candidates/slot_n/ows_Sn_Ck`；从
`<output_root>/pools/structures` 移除每个已经判断过的候选。如果 MatterSim
失败，或在评估完成前中断，则受影响的候选文件夹保留在
`<output_root>/pools/structures` 中等待重试。
