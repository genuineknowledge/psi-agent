根据 `skills/ows-catalyst-recommender/SKILL.md` 来执行。对于每个 Stage02
推荐 worker 的 agent，要求在分配到的 `slot_n` 中完成一次推荐迭代：读取
workflow 输入，并基于当前 `<output_root>/pools/candidates`、
`<output_root>/pools/structures`、
`<output_root>/pools/novel_and_stable_catalysts` 和
`<output_root>/fail/candidates` 去重；写入
`<output_root>/02-ows-catalyst-recommender/slot_n/ows_Sn_Ck`，其中包含
`CANDIDATE_PAYLOAD.json` 和 `REASONING.md`；将该 `ows_Sn_Ck` 文件夹同步复制到
`<output_root>/tmp/candidates/slot_n/ows_Sn_Ck`；如果推荐过程中触发知识捕获
并获得知识，则可选写入
`<output_root>/tmp/knowledge/slot_n/ows_Sn_Ck`；不得直接写入
`<output_root>/pools`。

Every worker must emit its assigned per-slot knowledge delta artifact. When
knowledge is captured, include the knowledge file and a delta manifest marked
`captured=true`. When no knowledge is captured, emit an explicit empty delta
manifest marked `captured=false` and no knowledge file.
