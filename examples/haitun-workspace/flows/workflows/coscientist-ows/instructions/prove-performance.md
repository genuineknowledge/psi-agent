根据 `skills/stage08-catalytic-performance-prover`，启动
`performance_prover_agent`，独立处理每个尚未判断的
`<output_root>/tmp/candidates/slot_n/ows_Sn_Ck` 文件夹：读取
`CANDIDATE_PAYLOAD.json`，构造一个只包含该候选名称和化学式的单记录
`retained_records` 输入；使用 `stage08-catalytic-performance-prover` 运行
`LLM_proof/run_llm_proof.py`；在候选文件夹中写入
`CATALYTIC_PERFORMANCE_PROOF.md` 以及相邻的 audit JSON；将判断为可能具有
催化性能的文件夹移动到
`<output_root>/pools/candidates/slot_n/ows_Sn_Ck`；将判断为无催化性能的
文件夹移动到 `<output_root>/fail/candidates/slot_n/ows_Sn_Ck`；并从
`<output_root>/tmp/candidates` 移除每个已经判断过的文件夹。
