---
name: rookie-doc-sync
description: "（当前不生效，见正文）新人入职清单文档被编辑时，把勾选状态同步回明细表"
event: haitun.rookie.doc_edited
source: feishu
filter: {}
visibility: silent
run_once: false
fire: tool
raw_event: drive.file.edit_v1
tool: rookie_sop_sync_doc
tool_args: {}
---

新人在自己的清单文档里勾了项 → 读回 todo 块的 done 状态 → 写进明细表 → 重算总览。
document_id 由 Session 注入 event_payload_json，不要写死 tool_args。

`filter: {}` 在这里是**刻意留空**的，与 rookie-sop-welcome 不同：文档变更事件的
document_id 只有落在 state 的 docs 索引里才会被处理，映射不到就直接报错返回，
所以不存在误同步别人文档的风险，不需要按 open_id 收窄。

`fire=tool`：到点不经过 LLM。同步是纯数据搬运，让模型参与只会增加延迟和不确定性。

## 为什么当前不生效

`drive.file.edit_v1` 至今没有推达过一次，原因是飞书的权限模型形成了死结（实测对照）：

- 机器人**自建**的文档：能订阅（`is_subscribe: true`），但编辑事件不推给它（0 条）
- **用户自己**的文档：编辑事件本该推，但 `subscribe` 返回 `forbidden` —— 机器人只能
  订阅自己拥有的文档，把它加成协作者也没用

即「能订阅的收不到，收得到的订不上」。平台侧配置已全部就位（事件开通、权限开通、
应用版本已发布、订阅方式为长连接），仍不来。

所以进度同步改走拉取：入职当天每 10 分钟一次（`rookie-docsync-<后8位>` 定时，
过了当天自删），之后靠每日 9:00 催办前那一次。

这个触发器**故意留着**：留着零成本，若飞书日后放开这个权限，它会自动开始生效，
届时高频拉取那条路仍然幂等、不冲突。
