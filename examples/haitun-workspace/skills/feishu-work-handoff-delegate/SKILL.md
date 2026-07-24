---
name: feishu-work-handoff-delegate
description: "工作同步 + 交接代答 — 飞书成员私聊 HaiTun 同步自己负责的工作（当前进展 / 下一步 / 交接原则），HaiTun 记进一张团队可见的『工作交接台账』多维表格；之后别人来找这个人对接、而本人不在时，HaiTun 在其交接原则的框架内代答『当前进展 + 下一步该做什么』。Use in two cases: (1) a member DMs the bot to sync their own work status/next-steps/handoff principles — record it into a Feishu bitable under their own sender_open_id; (2) someone asks about another person's work or says they're taking over — read that person's rows from the bitable and, strictly within the recorded 交接原则, tell them the current progress and next step. Anything outside the principles: say it's not authorized, give the owner's contact via feishu_user_get, optionally notify the owner with feishu_message_send. Uses feishu_bitable_* (read/write the ledger), feishu_chat_find_member / feishu_department_members (resolve names to open_id), feishu_user_get (contacts), feishu_message_send (relay/notify). Needs bitable:app scope + the app as a collaborator on the ledger base, and contact scopes for phone/email."
category: productivity
---

# 工作同步 + 交接代答（在本人给的原则内代答下一步）

两种用法，共用同一张**飞书多维表格**当「工作交接台账」：

1. **同步端** — 成员私聊 HaiTun 说清自己负责的事、现在到哪一步、下一步要做什么、
   交接给别人时该守的原则；HaiTun 把它结构化后记进台账，**记在这位成员本人名下**。
2. **代答端** — 别人来找某人对接某块工作、而**那个人不在**时，HaiTun 读台账里那个人的
   记录，**只在他写下的「交接原则」框架内**代答「当前进展 + 下一步该做什么」。
   原则没覆盖 / 需要拍板的，如实说「这超出他给我的原则，得等他本人确认」并给联系方式。

这是「在授权原则内被动代答下一步」，**不替本人主导整个交接、不替本人拍板、不替人去做事**。
与 [`feishu-blocker-routing`]（人找对人给联系方式）互补：那个只指路，本技能能替本人交代下一步。

用到的现成工具：
- `feishu_bitable_list_tables(app_token)` / `feishu_bitable_list_records(app_token, table_id, ...)`
  / `feishu_bitable_create_record(app_token, table_id, fields_json)` — 读写工作交接台账
- `feishu_bitable_list_fields` / `feishu_bitable_delete_fields` / `feishu_bitable_clear_table`
  — 建表后清默认空行/占位列
- `feishu_user_get(user_ids, ...)` — 用 open_id 取负责人联系方式（电话/邮箱/职位）
- `feishu_chat_find_member(...)` / `feishu_department_members(...)` — 按姓名反查 open_id
- `feishu_message_send(receive_id, text, on_behalf_of=...)` — 通知本人有人来交接，或代人带话署名
- `feishu_wiki_get_node(node_token)` — wiki 链接换 `app_token`

## 台账数据源（一张团队可见的多维表格）

匹配靠一张**飞书多维表格**当「谁负责什么、进展到哪、下一步、交接原则」的台账。
建议列（用户可增删，但 `负责人`/`open_id`/`工作事项`/`当前进展`/`下一步`/`交接原则` 尽量都留）：

| 列名 | 类型 | 说明 |
|---|---|---|
| `负责人` | 文本 / 人员 | 这块工作归谁（如「张老师」） |
| `open_id` | 文本 | 负责人 open_id — 代答匹配、取联系方式、通知本人都要用；同步时从 `<feishu_context>` 的 `sender_open_id` 自动填 |
| `工作事项` | 文本 | 负责的这块事（如「客户合同评审」「SPA 前端」）— 匹配关键词 |
| `当前进展` | 文本 | 最新状态（多次同步时更新为最新） |
| `下一步` | 文本 | 交接人来了该做什么 / 下一步动作 |
| `交接原则` | 文本 | 本人给的原则/边界 — HaiTun **只在此框架内**代答 |
| `更新时间` | 日期 / 文本 | 最近一次同步时间 |
| `联系方式备注` | 文本 | 可选，兜底说明 |

拿 `app_token`：
1. 用户给的多维表格链接形如 `https://<域名>.feishu.cn/base/<app_token>?table=<table_id>&...`，
   `/base/` 后那段就是 `app_token`，URL 里的 `table` 参数就是 `table_id`。
2. 若是 wiki 链接（`/wiki/<node_token>`），先 `feishu_wiki_get_node(node_token)` 拿到
   `obj_token` 当 `app_token`。
3. 不知道 `table_id` 就 `feishu_bitable_list_tables(app_token)` 列出来选对的那张。

**没有台账链接就先问用户要**，别猜 `app_token`，也别凭空编负责人/进展/原则。

### 清理飞书新建表的默认空行/空列

飞书新建数据表会自带占位列和空行。建表 → `feishu_bitable_clear_table(app_token, table_id)`
清默认空行 → `feishu_bitable_list_fields(app_token, table_id)` 看列 →
`feishu_bitable_delete_fields(app_token, table_id, field_ids)` 删多余占位列（主键列 `is_primary=true`
删不掉，保留即可）→ 确认剩下列名与 `fields_json` 的键一致 → 再写数据。

## 用法一：成员同步自己的工作 + 交接原则（同步端）

成员私聊 HaiTun 说清自己负责的事、进展、下一步、交接原则时：

1. **确认是本人在同步自己的工作**：负责人就是私聊里 `<feishu_context>` 的
   `sender_open_id`，`open_id` 列直接填它，`负责人` 填其姓名。**只记本人名下**，
   不替别人登记他的工作（那要那个人自己来同步）。
2. **把口语整理成结构化字段**：从对话里提炼 `工作事项` / `当前进展` / `下一步` / `交接原则`。
   - `交接原则` 是关键：这是别人来交接时 HaiTun 代答所依据的边界（如「小改直接改，
     大改必须等我确认」「客户口径以合同为准，别自行承诺」）。**照本人说的记，别替他扩大或收紧**。
   - 关键项缺失就**问清再记**（这块具体是什么事？现在到哪了？下一步谁做什么？交接时有什么要守的？），
     **绝不替员工编进展或原则**。
3. **定位行 → 更新或新建**：先 `feishu_bitable_list_records` 找有没有（同一 `open_id` +
   同一 `工作事项`）的行。
   - 已存在：视为同一块工作的更新，用 `feishu_bitable_create_record` 写一条新的最新记录，
     或按台账约定更新（本仓库 `feishu_bitable_*` 以新建记录为主；如需覆盖旧行，先
     `feishu_bitable_delete_records` 删旧行再建）。以「保留最新一条可读」为准。
   - 不存在：`feishu_bitable_create_record(app_token, table_id, fields_json)` 新建。
   - `fields_json` 的键必须与表里真实列名逐字一致，例如：
     ```json
     {"负责人":"张老师","open_id":"ou_xxx","工作事项":"客户合同评审","当前进展":"已过初审，等法务复核","下一步":"法务复核通过后寄回客户盖章","交接原则":"小改直接改；改条款必须等我确认；口径以合同为准","更新时间":"2026-07-24"}
     ```
4. **回执确认**：告诉本人记了哪块事、哪些字段（尤其把「交接原则」回显给他核对——
   「以后别人来交接这块，我就按这条原则代你交代下一步，对吗？」）。
   若 `ok=false`，**如实**把飞书错误报给本人（多为权限/列名不符），**不谎报成功**。

## 用法二：别人来对接、本人不在，HaiTun 代答下一步（代答端）

有人私聊说「我要找张老师对接 X」「张老师那块进展怎么样 / 我来接手了」而本人不在时：

1. **听懂要找谁、要交接哪块事**：模糊就追问一句（找哪位？哪块工作？），**别自己脑补**。
2. **把姓名解析成 open_id**：台账靠 `open_id` 匹配最稳。姓名→open_id 用
   `feishu_chat_find_member` 或 `feishu_department_members(recursive=True)` 反查；
   同名多人要跟来问的人核对是哪位。
3. **读台账匹配**：`feishu_bitable_list_records` 读记录，按（`open_id` 或 `负责人` + `工作事项`）
   匹配到那块工作的行；同一块取**最新一条**（看 `更新时间`）。
4. **在「交接原则」框架内代答**：一句话说清 **①当前进展 ②下一步该做什么 ③相关约束/原则**，例如：
   「张老师负责的客户合同评审：目前已过初审、等法务复核；下一步是法务复核通过后寄回客户盖章。
   他交代的原则：改条款必须等他本人确认，口径以合同为准。」
   - 命中多行/多块事：把候选列出让对方挑，或按最贴切一条并说明理由。
   - 一行没命中：**如实说「台账里没查到张老师这块的交接记录」**，给兜底（直接找本人、
     问其上级/部门 `feishu_department_members`），**不硬编进展或下一步**。

## 用法三：原则没覆盖 / 需要拍板（边界处理）

来问的事超出台账里记的「交接原则」，或需要本人拍板时：

1. **如实说未授权**：明确讲「这块超出 {负责人} 给我的交接原则，我不能替他定，需要他本人确认」，
   **绝不越权承诺、不替本人拍板、不编原则来硬答**。
2. **给联系方式兜底**：拿负责人 `open_id` 调 `feishu_user_get(user_ids=<open_id>)` 取
   `name` / `mobile` / `email` / `job_title` 给来问的人。读不到就说「电话/邮箱没读到
   （可能权限没开），可在飞书里直接 @他」并给其飞书姓名，**不编号码**。
3. **可选：通知本人有人来交接**：`feishu_message_send(receive_id=<负责人 open_id>,
   receive_id_type="open_id", text="有人来对接你负责的『X』，问到了超出你交接原则的 Y，需要你确认…")`。
   若是替来问的人转达其原话，用 `on_behalf_of=<来问者 open_id>` 让本人看到署名而非裸气泡。

## 边界

- 只做「在本人给的原则框架内**被动代答下一步**」：不主导整个交接对话、不替本人拍板、
  不替谁去做那件事；原则没覆盖就退回到「等本人确认 + 给联系方式」。
- 同步只记**本人名下**（`open_id`=`sender_open_id`）；不替别人登记他的工作。
- 台账查不到就**如实说查不到**，绝不硬编进展、下一步、原则或联系方式。
- 交接原则/进展**照本人说的记**，代答时**照台账念**，绝不替他扩大授权或自行加码承诺。
- 工作进展与原则可能含敏感信息：只回给应知悉者（来正当对接的人），不群发、不到处贴。
- 与其他技能互补：人找对人给联系方式走 [`feishu-blocker-routing`]，导师反馈走
  [`feishu-mentor-feedback`]，代员工提交审批走 [`feishu-self-service-agent`]，
  会话/任务交接走 [`session-management`]；本技能专管「工作+交接原则的同步与授权内代答」。
