---
name: feishu-blocker-routing
description: "卡点找人 — 员工在飞书私聊 HaiTun 说自己工作上卡在某个点（某事推不动、缺信息/权限、依赖别人），HaiTun 判断这个点属于谁的工作范围、该去找谁帮忙，并附上那个人的联系方式（电话/邮箱），让协作更顺畅。Use when someone DMs the bot saying they're blocked/stuck on something and asks who to turn to. Reads a 职责归属 (ownership) 多维表格 mapping 业务领域/职责 → 负责人 open_id via feishu_bitable_*, matches the blocker to the owning area, resolves that owner's contact details with feishu_user_get, and replies to the employee with 谁负责 + 找谁 + 联系方式. Needs bitable:app scope + the app as a collaborator on the ownership base, contact scopes for phone/email, and the app authorized on the 通讯录权限范围."
category: productivity
---

# 卡点找人（判定职责归属 + 给出联系方式）

员工私聊 HaiTun 说"我卡在某个点上了"（某事推不动、缺信息、缺权限、要等别人），
HaiTun 先判断这个卡点属于**谁的工作范围**，再告诉员工**该去找谁帮忙**并附上**联系方式**，
让活儿能接着往下走。这是"人找对人"的路由，不替员工去做那件事，也不替别人拍板。

用到的现成工具：
- `feishu_bitable_list_tables(app_token)` / `feishu_bitable_list_records(app_token, table_id, ...)`
  — 读**职责归属台账**（业务领域/职责 → 负责人 open_id）
- `feishu_user_get(user_ids, ...)` — 用负责人 open_id 取其**联系方式**（电话/邮箱/职位/部门）
- `feishu_department_members(...)` / `feishu_chat_find_member(...)` — 需要时按姓名反查 open_id
- `feishu_message_send(receive_id, text, ...)` — 需要时把结论回给员工（私聊里直接回也行）

## 职责归属台账（数据源）

匹配靠一张**飞书多维表格**当"谁负责什么"的台账，建议列：

- `业务领域/职责`（如"报销流程""线上部署""客户合同""SPA 前端"）— 匹配用的关键词
- `负责人`（人员列，或直接存 `open_id` 文本列）
- `open_id`（负责人的 open_id，取联系方式和回执要用；人员列也可，但存一列 open_id 最稳）
- 可选：`备用负责人`/`联系方式备注`/`说明`

拿 `app_token`：
1. 用户给的多维表格链接形如 `https://<域名>.feishu.cn/base/<app_token>?table=<table_id>&...`，
   `/base/` 后那段就是 `app_token`，URL 里的 `table` 参数就是 `table_id`。
2. 若是 wiki 链接（`/wiki/<node_token>`），先 `feishu_wiki_get_node(node_token)` 拿到
   `obj_token` 当 `app_token`。
3. 不知道 `table_id` 就 `feishu_bitable_list_tables(app_token)` 列出来选对的那张。

**没有台账链接就先问用户要**，别猜 app_token，也别凭空编负责人。

## 每次找人的流程

1. **听懂卡点**：从私聊里弄清员工到底卡在什么事上（哪块业务/哪个环节/缺什么）。
   模糊就追问一句（是哪个系统？卡在哪一步？），**别自己脑补**成某块业务。
2. **读台账匹配归属**：`feishu_bitable_list_records` 读职责归属表，把卡点关键词对到
   `业务领域/职责` 行，定位**负责人**。
   - 命中多行：把候选一并列出让员工挑，或按最贴切的一条并说明理由。
   - 一行都没命中：**如实说"台账里没查到明确归属"**，给出兜底建议（问直属上级／找该员工
     所在部门，用 `feishu_department_members` 看部门同事或 leader），**不硬安一个负责人**。
3. **取联系方式**：拿负责人 `open_id` 调 `feishu_user_get(user_ids=<open_id>)`，
   取 `name` / `mobile` / `email`（或 `enterprise_email`）/ `job_title`。
   - 台账里存的是姓名不是 open_id：先 `feishu_department_members(recursive=True)` 或
     `feishu_chat_find_member` 按姓名反查 open_id，再取详情。同名多人要跟员工核对是哪位。
4. **回员工**：一句话说清 **①这是谁的工作范围 ②去找谁 ③怎么联系**，例如：
   "这块（线上部署）归 张三 负责，可以找他。联系方式：电话 138xxxx / 邮箱 zhangsan@…（职位：SRE）。"
   联系方式**取到几项报几项**；`mobile`/`email` 为空就说"电话/邮箱没读到（可能权限没开），
   可在飞书里直接 @他"，并给出其飞书姓名，**不编号码**。

## 边界

- 只做**人找对人 + 给联系方式**：不替员工去推进那件事，不替负责人答应/拍板，不越权承诺。
- 归属只认台账（或明确的部门/上级兜底）；**查不到就如实说查不到**，绝不硬安负责人或编联系方式。
- 联系方式是个人敏感信息：**只在私聊里回给来问的本人**，不群发、不到处贴；取到几项报几项。
- 电话/邮箱读不到多是缺 `contact:user.phone:readonly` / `contact:user.email:readonly` 或
  通讯录权限范围没覆盖——**如实告知**并退回到"在飞书里 @他"，不谎报。
- 与其他技能互补：真正办事走对应技能（如自助办事 [`feishu-self-service-agent`]），本技能只负责指路。
