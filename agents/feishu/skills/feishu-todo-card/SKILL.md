---
name: feishu-todo-card
category: knowledge-base
description: 给某人发一张「今日 TODO」卡片：一张卡列多条待办，每条一个勾选形状(○●/□■/◇◆/△▲/☆★/☐☑)、一个详情链接、一个对应的飞书任务。勾一条只结那一条，其余仍可勾，卡片原地更新。用于每日/每周待办推送、清单式派活、以及任何「一条消息里要办好几件事」的场景。也讲清多选卡与普通单次卡的区别、per-row 幂等边界、以及 task_guid 怎么来。
---

# 飞书 TODO LIST 卡片

一张卡多条待办，**逐条勾选**。这跟普通卡片的根本区别：普通卡片点一次就整张退休（防重复提交），
TODO 卡把「一次性」的粒度从**整张卡**降到**每一行**。

## 用哪个工具

| 场景 | 工具 |
|---|---|
| 一张卡列多条待办、逐条勾 | `feishu_todo_card_send` |
| 勾选后的回调（标飞书任务完成） | `feishu_todo_card_tick`（卡片自动派发，不用手调；勾选还会**自动给 mentor 发评价卡**，见 `company-todo-review` 技能） |
| 已完成行的「撤销」回调（重开飞书任务） | `feishu_todo_card_untick`（卡片自动派发，不用手调） |
| 一张卡只要一个答案（同意/驳回、选一项） | `feishu_message_send_card`，**别用** TODO 卡 |
| 自己拼多选卡（非待办形状） | `feishu_message_send_card` + `multi_use=True` |

## 标准流程

**先建飞书任务，再发卡。** 每行的链接指向它自己的飞书任务，任务不存在就没有可点的目标。

1. 对每条待办 `POST /open-apis/task/v2/tasks`（见 `feishu-task` 技能），拿回 `task_guid`。
2. 组 `items_json`，把 `task_guid` 填进对应行。
3. `feishu_todo_card_send(receive_id="ou_...", items_json=...)`。

```text
feishu_todo_card_send(
  receive_id="ou_xxx",
  title="8月5日 待办",
  subtitle="来源: 团队 TODO 表 | mentor: 张三",
  items_json='[
    {"title":"写周报","task_guid":"abc-123","detail":"周五 18:00 前","shape":"square"},
    {"title":"改设计文档","task_guid":"def-456","detail":"评审前完成","shape":"circle"}
  ]')
```

## 每行可配的字段

- `title` — 待办文字（必填；空的会变成「任务 N」）。
- `task_guid` — 对应的飞书任务，渲染成 applink 链接。
- `link` — 显式 URL，**覆盖** applink（想链到文档而非任务时用）。
- `shape` — 该行的形状：`circle` ○● / `square` □■ / `diamond` ◇◆ / `triangle` △▲ /
  `star` ☆★ / `check` ☐☑。不填则用卡片级 `shape`。**按任务类型区分形状**就靠这个字段。
- `detail` — 标题下的第二行（截止时间、验收标准）。
- `done` — 预置已完成：渲染成删除线且不给按钮。

未完成 = 空心 + 加粗；已完成 = 实心 + 删除线 + 无按钮。

## 关于链接：applink，不是 web url

`task/v2` 的返回体里**没有** web URL 字段，别去等它。工具用官方客户端跳转协议拼：

```text
https://applink.feishu.cn/client/todo/detail?guid=<task_guid>
```

要跳到别处（文档、多维表格记录）就填 `link` 覆盖掉。

## 幂等边界（会踩的地方）

**逐行 at-most-once，不是逐卡。** 勾第 1 行不影响第 2 行；重复勾第 1 行恰好被拒一次
（跨进程、跨重启都有效，靠 per-action 墓碑文件）。并发同时勾两行也各自成立。

**每行的 `action` 必须唯一且规范**（无前后空格）。工具自动生成 `todo_tick_<行号>`，自己拼卡时
务必照办 —— 两行撞名会互相顶掉。**没有可用 action id 的行会退回整卡去重**，也就是退化成普通
单次卡：点一下整张卡就没了。

**已勾状态会回写快照。** 否则第二次勾会从原始卡渲染，把第一行的完成状态覆盖回未完成。这一步
是框架做的，但如果日志里出现 `failed to persist ticked card`，就说明后续勾选会显示错行 —— 重发一张新卡。

**防重放靠两层，各管一段：**

| 层 | 挡什么 | 作用域 |
|---|---|---|
| 墓碑文件 `{message_id}.{action}.consumed` | 同一行被重复消费（含飞书 at-least-once 重投、进程重启后重投） | 跨进程、跨重启 |
| 每卡一把 `anyio.Lock`（读-改-写临界区） | 两行同时勾时交错覆盖彼此的完成状态 | 单进程内 |

墓碑用 `Path.touch(exist_ok=False)`，在 CPython 上**就是** `os.open(O_CREAT|O_EXCL|O_WRONLY)`，
是原子的（`exist_ok=True` 才会走非原子的 `utime` 路径）。

锁只在单进程内有效，这是**够的**：一个飞书 app 只能有一条 WS 长连接消费者，这是飞书平台的限制
而非本项目的选择（本机起两个实例会互相抢连接）。所以同一张卡的并发勾选必然落在同一个进程里。
真要出现多进程分别收到同一张卡的回调，锁失效但墓碑仍然成立 —— 退化后果是「某一行的完成状态可能
被另一进程的回写覆盖」，不是重复执行动作。

**别用 `feishu_message_edit_card` 改 TODO 卡。** 它不重新注册回调，编辑后按钮全是死的。

## 连点会被合并成一个回合

卡片是立刻重绘的，但你这一轮处理要几秒，而每个 session 只有一把锁。用户等不及连勾 5 条，
本来会排成 5 个回合、回 5 条消息。框架因此加了合并闸：**在途回合期间到达的点击，全部并进
下一个回合**，按 `(message_id, 点击者)` 分键 —— 群卡里两个人各点各的，互不合并、各自回复。

合并后你会收到一个批量壳，里面是多条 `<feishu_card_action>`：

```
<feishu_card_action_batch count="3">
<feishu_card_action>...</feishu_card_action>
<feishu_card_action>...</feishu_card_action>
<feishu_card_action>...</feishu_card_action>
</feishu_card_action_batch>
```

**每条都要处理**（逐条调 `feishu_todo_card_tick`，一条都不能漏 —— 合并只省回复，不省动作），
但**只回一条消息**总结，或者干脆 `NO_REPLY`。不要一条点击回一段话。

## 不要用飞书原生 checker

飞书有 `checker` 组件（Card 2.0），看着最像 todo 勾选框，但框架只把 `action`/`button`/`form`
当交互元素 —— **`checker` 不在其中**，点了不会被消费机制识别，一次性保证和「已完成」回显都不生效。
所以这里用文本形状字符 + 按钮，形状反而更自由。

## 完成后发生什么

勾选 → 框架把该行改成 `● ~~文字~~` 并原地更新卡片 → 派发 `feishu_todo_card_tick` →
`PATCH /open-apis/task/v2/tasks/:task_guid` 写 `completed_at`（**毫秒**）+ `update_fields`；
tick 还会同步台账状态为「已交付」，并**自动给该行的 mentor 发一张 1-5 分评价卡**
（发卡失败不阻塞勾选本身；评价卡链路见 `company-todo-review` 技能）。

已完成行会带「撤销」按钮 → 派发 `feishu_todo_card_untick` → 重开飞书任务、台账回退、
卡片改回可勾选。

漏了 `update_fields` 飞书会返回成功但一个字段都不改。这个工具已经带上了，自己调 API 时注意。

行内没有 `task_guid` 时，返回 `task_updated: false` 并说明原因 —— 这不是错误，只是没有任务可动。

**卡片在这一步已经更新好了。** 不要再发一遍、不要复述点击动作；只有任务更新失败才需要回话。

## 权限

标任务完成默认走机器人 token。**机器人不是任务成员时会被拒** —— 这时传 `user_key`（点击者的
open_id），以本人身份完成。定时批量建任务读公共表的权限问题见 `feishu-unattended-access`。

## 上限

单卡最多 40 条，超了先拆卡。行数多时飞书客户端会折叠，重要的放前面。
