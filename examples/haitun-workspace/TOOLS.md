# TOOLS.md — Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to
your setup. It is usage guidance, not availability.

## What Goes Here

Things like:

- SSH hosts and aliases
- API providers / base URLs you commonly use (never the keys themselves)
- Device nicknames, paths, or directories you reach for often
- Anything environment-specific

## Examples

```markdown
### SSH
- home-server → 192.168.1.100, user: admin

### Common paths
- notes → ~/notes
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without
losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

### Fusion Memory

- The process starter configures the operator-owned token-map path before Haitun starts.
- A mapped user's first message automatically starts authenticated MCP health checking and passive
  persistence for the trusted runtime Session.
- Use `memory_health` for status. Do not inspect or edit `.env`, ask for bearer tokens, or derive
  memory authentication from model-visible `<feishu_context>`.
- An unmapped user can continue chatting normally but has no durable memory.

### 飞书群聊上下文

收到飞书群聊消息时，消息开头会带一段 `<feishu_context>` 元数据（chat_id / chat_type /
message_id / sender_open_id）。需要群里之前的上下文时：

- `feishu_message_list(container_id=<chat_id>, container_id_type="chat")` 拉取本群历史消息
- 消息里提到的飞书文档链接：从 URL 取 file_type + token，用 `feishu_doc_read` 读正文
- 群里分享的附件/图片：用 `feishu_file_download` 下载后再处理

**一个群 = 一份共享上下文（多人同处一室）**：接了 Gateway 时，同一个群里所有人跟你说的话都进
**同一个** session（按 `chat_id` 建），私聊则各自独立。所以在群里：

- 你**看得见**本群此前的对话，A 问完 B 追问「那第二点呢」你应当接得上，不要再让人复述；
- 但**说话的人每条都可能不同**。要知道当前这句是谁说的，看本条消息 `<feishu_context>` 的
  `sender_open_id`，**不要**沿用上一条的发言者；
- 涉及身份的操作（`user_key`、把私密信息回给「本人」、代人提审批等）一律用**当前这条**消息的
  `sender_open_id`。群里 A 授权过不代表 B 也授权过——B 发起的写操作若 `need_auth=True`，要按
  B 的 `user_key` 单独走一遍授权；
- **联系方式、薪酬、个人考勤这类私密信息不在群里回**，改为私聊回给来问的本人。

### 飞书权限总原则：读用机器人；写先问归属，权限按需申请

**读**（读文档/表格/消息/考勤/审批…）一律先用机器人（tenant）权限，机器人不够才自动回退用户
身份。读不产生归属，**不要**为读去问用户任何东西。

**写**（新建文档/写正文/建表格/建任务/改权限/传文件…）产出是有主人的：谁做的就归谁。所以写之前
要定两件事，两件都由用户说了算，你不要替他猜：

1. **归属**：用他本人身份做（产出归他，需要他授权），还是用机器人身份做（产出归机器人）。
2. **权限**：选了本人身份时，只申请**这次任务真正需要**的权限，不再一次性要一大把。

- **照旧无脑把 `<feishu_context>` 里的 `sender_open_id` 当作 `user_key` 传给飞书工具**——身份和
  权限都按这个键各自隔离，群里 A 的选择/授权不影响 B。
- 写类工具都多了一个 `identity` 参数：`"user"`（归用户本人）/ `"bot"`（归机器人）/ 留空
  （沿用该用户此前记住的选择）。**你不需要每次都传**——问过一次就记住了。
- 用户还没被问过时，写类工具会**什么都不做**，返回 `need_identity_choice=True`。这不是错误，
  是在等你问。此时按下面「问归属」走一遍。
- 只有返回 `need_auth=True` 时才引导授权，并且**只申请它给出的 `need_capabilities`**。
  已授权过的权限会被记住，同类操作不会再问；只有任务需要**新**权限时才会再授权一次。
- 收到 `need_identity_choice` 或 `need_auth` 时**不要反复重试**同一个调用。

哪些操作**必须**用户本人授权（机器人权限天生做不了，直接 `need_auth`）：
- `feishu_docs_search`（全库搜「当前用户能看到的文档」，需 `docs_read`）；
- `feishu_wiki_create_space`（新建知识库，新库归授权用户，需 `wiki_write`）；
- `feishu_contact_search`（全组织按名搜人，需 `contact_read`）。

### 问归属（一次问清，之后不再问）

收到 `need_identity_choice=True` 时：

1. 用 `clarify` 问用户，例如「这份《周报》要建在**你的名下**（归你所有，需要你授权一次），还是用
   **机器人**建（归机器人所有，之后可以再共享给你）？」——把两种归属的后果说清楚，别只问
   「用哪个身份」；
2. 拿到答复后调 `feishu_identity_set(user_key=<sender_open_id>, identity="user"|"bot")`；
3. 再重试原来那个写操作（这次可以不传 `identity`，工具会读记住的选择）。

用户中途说「这一篇用机器人建就行」时，直接给那次调用传 `identity="bot"`，不必改掉记住的默认。
想查某人当前的选择和已授权权限，用 `feishu_identity_get(user_key)`。

### 引导用户授权（三级优先级，默认免复制，一次授权后不再问）

当工具返回 `need_auth=True`，把 `sender_open_id` 作为 `user_key` 贯穿全过程（多人场景各自
授权、互不覆盖）。**只调一个工具**：

```
feishu_auth_request(user_key=<sender_open_id>, capabilities=<工具给的 need_capabilities>,
                    reason=<一句话说明这次授权干什么>)
```

它按下面的优先级自动挑当前环境能用的最省事那种，你不用自己判断，看返回的 `tier` 决定下一步：

| 优先级 | `tier` | 用户要做什么 | 你接下来做什么 |
| --- | --- | --- | --- |
| 1 | `card` | 点一下卡片按钮 | **这一轮立刻收尾**，等回调那轮再 `feishu_auth_wait` |
| 2 | `link_auto` | 打开链接点「同意」，**不用复制 code** | 发 `authorize_url`，紧接着 `feishu_auth_wait` |
| 3 | `link_manual` | 打开链接点「同意」，**还要复制 code** | 发 `authorize_url`，再拿 code 调 `feishu_auth_complete` |

降级原因写在返回的 `downgraded_from` / `downgrade_reason` 里：**如实告诉用户**为什么用了更麻烦的
方式，别假装走的是更顺的那条。两种降级触发条件：

- 1→2：没有可私聊的 `open_id`（群场景），或卡片没发出去（缺 im 权限、用户没和机器人建过会话、
  飞书限流……）。卡片发不出去时链接仍然能发，所以整件事不会因此失败；
- 2→3：这个部署没有自动接收授权码的通道（既没配 `PSI_OAUTH_CALLBACK_BASE`，回环端口也不可用）。
  此时第 1 级也一并跳过——没有自动回流的卡片，点了还是要手抄，那按钮是个谎。

**第 1 级 `tier=card` 的细节**：卡上「点此授权」按钮同时做两件事——打开飞书授权页（`open_url`）
+ 把这次点击回调给你（`callback`）。

- **发完卡这一轮就收尾**：别在同一轮里调 `feishu_auth_wait`，也别把链接再当文本发一遍。
  同一轮等待会占住 Session 的 turn 锁，用户这期间说什么都得排队几分钟；
- 用户点按钮后，你会收到一条 `<feishu_card_action>`，其 `dispatch.handler` 是
  `feishu_auth_wait`、`action.value.user_key` 是该用户。**那一轮**才调
  `feishu_auth_wait(user_key=...)` 等授权码自动回流（此时用户正对着授权页，等待是应该的），
  拿到 token 后接着做原来被卡住的那件事；
- **卡片是一次性的**：用户点了按钮但没在授权页点「同意」时，这张卡已作废（原卡被改写成
  「已选择」），重新调 `feishu_auth_request` 发一张新的，别让用户再点旧卡；
- 授权卡只能**私聊**发给本人（`receive_id` 默认就是 `user_key`）。待完成的授权记录存在发卡方
  workspace，而群里点卡片会落到点击者自己的私聊会话、读不到这条记录，所以群 id 会跳过这一级。

**第 2、3 级的细节**：把返回的 `authorize_url` **原样发给用户**，让其打开并点「同意授权」，然后

- `tier=link_auto`：**不要向用户索要任何 code**。直接调 `feishu_auth_wait(user_key=...)`
  等待——用户点完「同意授权」后浏览器会看到「授权成功」页，授权码自动回流并完成授权。
  返回 `timed_out=True` 时可以再调一次继续等；
- `tier=link_manual`：才需要**明确告诉用户**看浏览器地址栏，地址形如
  `http://localhost/?code=xxxxxxxx&state=...`，把 `code=` 后面、`&` 之前那一串复制回来
  （整段网址也行），然后调 `feishu_auth_complete(code, user_key=...)`。

`capabilities` 只接受能力键（`docs_read` / `drive_read` / `drive_write`（含电子表格）/
`docx_write` / `wiki_write` / `bitable_write` / `task_write` / `calendar_write` /
`contact_read` / `contact_phone_email_read`），**不要传飞书原始 scope 串**——无效 scope 会让
整个授权页失败（20043），所以工具直接拒绝未知键。已授权过的权限会自动并进去，不会因为再授权
一次而丢掉旧能力。三级在这两条上行为一致。

想只用某一级、不要自动降级时才直接调底层工具：`feishu_auth_card`（只发卡）或
`feishu_auth_start`（只出链接，看它的 `auto_receive` 区分第 2、3 级）。

成功后凭证缓存并自动续期，之后同类操作不会再让用户授权。

想让自动通道可用（部署侧一次性配置，二者其一即可）：
- 给 Gateway 配一个用户浏览器可达的回调基址 `PSI_OAUTH_CALLBACK_BASE`（如
  `https://haitun.example.com`），并把 `<基址>/oauth/callback` 登记到飞书后台重定向 URL。
  手机上点授权也能自动回流，**多用户部署走这条**；
- 或纯本机场景：不配 `PSI_FEISHU_REDIRECT_URI`，工具会用
  `http://127.0.0.1:17860/oauth/callback`（端口可用 `PSI_OAUTH_LOOPBACK_PORT` 改），
  同样需要登记到飞书后台。

### 免授权优先：手上有链接就直接读

如果用户已经给了文档/wiki 链接，直接 `feishu_doc_read` / `feishu_wiki_get_node` 读即可，
**不要多此一举去搜索或授权**。只有当诉求确实需要全库搜索（如「帮我在公司知识库找报销 SOP」
而你手上没有链接）时，才用 `feishu_docs_search`（这一步才需授权）。

### 写入 / 知识库 / 下载类的具体用法（都已 tenant 优先，带上 user_key 即可）

- **建带内容的 wiki 文档，优先用一步到位工具**：
  `feishu_wiki_create_doc_with_content(space_id, title, content, parent_node_token, user_key)`
  一次完成「建节点 + 写正文」，避免分两步（`feishu_wiki_create_doc` 再 `feishu_doc_append_content`）
  时留下**空文档**。若正文写入失败，它会连 `node_token`/`obj_token` 一并回报，可用相同 `user_key`
  调 `feishu_doc_append_content` 补写。
- **在文档里放表格 / 流程图 / 泳道图**：`feishu_doc_append_content` 只能写标题和段落，
  **写不出真正的表格**，更画不了图。要真正的表格/图，用下面三个专门工具（都吃 docx 的
  `document_id`，也就是 `feishu_doc_create` 返回的 id，或 wiki 节点的 `obj_token`；带 `user_key`）：
  - 表格：`feishu_doc_append_table(document_id, rows_json, header_row, column_width_json, user_key, caption)`——
    `rows_json` 是二维 JSON 数组，如 `[["姓名","部门"],["张三","研发"]]`，会生成飞书原生表格块。
  - 流程图：`feishu_doc_append_flowchart(document_id, steps_json, title, user_key, caption)`——
    `steps_json` 是步骤数组 `["提交","审批","归档"]`。**飞书开放接口画不了真正的流程图块**
    （block_type 21 是空画布，API 填不进节点），所以用「单列表格 + ↓ 箭头」如实呈现，可编辑。
  - 泳道图：`feishu_doc_append_swimlane(document_id, lanes_json, stages_json, user_key, caption)`——
    `lanes_json` 可传对象 `{"客户":["下单","付款"],"仓库":["发货"]}`（列=泳道，自动排格），
    或传泳道名数组 `["客户","客服","仓库"]` 再用 `stages_json` 给二维正文行。同样用表格如实呈现。
  三个都收 `caption`（表题）：**只写内容不写「表N：」**，工具读文档已有的「表 N」自动续号，
  按学术体例写在**表格上方**（图注在下、表题在上），且「表」和「图」是两条互不干扰的序列。
  一句话：用户要「表格/流程图/泳道图」时别再往正文里塞纯文本，改用这三个工具。
- **改文档里已有的内容（不是追加）**：上面的 `append_*` 只会往末尾加，写错一段不必重开一篇——
  用这三个「块级编辑」工具改稿（都吃 docx 的 `document_id` 或 wiki 节点的 `obj_token`）：
  - 先列块拿 id：`feishu_doc_list_blocks(document_id, max_blocks, user_key)` 返回
    `{block_id, block_type, type_name, parent_id, text, editable_text}`。**这是拿到 `block_id`
    的唯一途径**，另两个工具都按 `block_id` 定位。`text` 是 200 字预览（要读全文仍用
    `feishu_doc_read`）；`editable_text=false` 表示该块（图片/表格/分割线）没有文字可改。
  - 改一段：`feishu_doc_update_block(document_id, block_id, text)`——只换文字，块的 id 和类型
    都保留（标题还是标题、项目符号还是项目符号）。注意 `text` 是**整段替换而非追加**，要传该块
    完整的新内容。文档根块（其 id 就等于 `document_id`）没有文字，工具会直接拒绝。
  - 删块：`feishu_doc_delete_blocks(document_id, block_ids_json, parent_block_id)`——
    `block_ids_json` 是 id 数组，如 `["doxcnAAA","doxcnBBB"]`。飞书的删除接口按
    **父块下的子块序号区间**删而不是按 id 删，所以工具在删之前把每个 id 解析成当前序号，并
    **从大序号往小删**（若从小往大删，删掉一个后面兄弟节点全体前移，后续序号就会打偏删错块）；
    定位不到的 id 一律以 `not_found` 回报，**绝不猜序号**。块若是嵌套的（在表格单元格、
    高亮块里，看列块结果的 `parent_id`）要传 `parent_block_id`，留空即文档根。
    删除经 API 不可撤销，动手前先用 `list_blocks` 核对一下要删的正是那段文字。
- **列出电子表格的工作表**：`feishu_sheet_tabs(token)` 返回每个工作表的
  `sheet_id`/`title`/行列数。**`SHEET_ID` 不在表格 URL 里**，而所有区域都写成
  `"SHEET_ID!A1:B2"`，所以不知道 `SHEET_ID` 时先调它，再去读写区域。
- **读电子表格的一个区域**：`feishu_sheet_read(token, range, max_chars)`——只读指定区域
  （`feishu_doc_read(file_type="sheet", ...)` 是整本工作簿一次性倒出来，定位不了单格）。
  返回拍平成纯文本的行数组：**mention 单元格（`@某人`）和带样式的富文本都会拍成可见文字**，
  所以人名列读出来是 `"@张三"` 而不是一坨 JSON（匹配人名时记得去掉开头的 `@`）。
  用它来「按人名列找出某人在第几行」和「写之前查目标单元格是否已被占」。
- **往电子表格写数据/公式/格式**（表格只能读不能写的缺口已补上）：
  `feishu_sheet_write(token, range, values_json, user_key)` 覆盖写一个区域；
  `feishu_sheet_append(token, range, values_json, insert_data_option, user_key)` 在数据末尾追加行；
  `feishu_sheet_format(token, range, style_json, user_key)` 设单元格样式（字体/颜色/边框/对齐/数字格式）。
  `token` 是表格 URL 里 `/sheets/` 后那串；`range` 用 `"SHEET_ID!A1:C3"`（裸 `"SHEET_ID"` 指整张已用区域）；
  `values_json` 是「行的数组」如 `'[["姓名","分数"],["张三",95],["合计","=SUM(B2:B2)"]]'`——**单元格值以 `=` 开头即写成公式**。
  写表格是写操作：带 `user_key=<sender_open_id>`，归属按上面「问归属」的结果走（`identity`
  留空即沿用记住的选择）。
- **删除文档/文件**：`feishu_drive_delete_file(file_token, file_type, user_key)`——删除进
  **回收站可恢复**。file_type 是 docx/doc/sheet/bitable/mindnote/slides/file/folder/shortcut。
  删**知识库(wiki)里的文档**：飞书没有独立删 wiki 节点的接口——先 `feishu_wiki_get_node`
  取 `obj_token`+`obj_type`，再 `feishu_drive_delete_file(file_token=obj_token, file_type=obj_type, user_key=...)`。
  删除不可轻率，动手前先跟用户确认清楚删的是哪一个。
- **访问/浏览知识库**：`feishu_wiki_list_spaces` / `feishu_wiki_list_nodes` / `feishu_wiki_get_node`
  已做「tenant 先试，返回空且带了 user_key 时自动改用户身份重试」。带上 `user_key=<sender_open_id>`：
  `feishu_wiki_list_spaces(user_key=...)` 列库 → `feishu_wiki_list_nodes(space_id, user_key=...)` 列文档
  → `feishu_wiki_get_node(token, user_key=...)` 拿 obj_token → `feishu_doc_read` 读正文。
  **不要因为一时返回空就说"企业没有知识库"**——确认带了 user_key 即可。
- **读知识库里的 PDF/附件（下载）**：飞书文档 API 只能直接读 docx/doc/sheet；PDF、图片等要先下载再解析。
  `feishu_file_download(source, save_path, user_key=...)` 已 tenant 优先、机器人下不到时自动回退到用户身份。
  流程：`feishu_wiki_get_node(token, user_key)` 拿 `obj_token` → `feishu_file_download`（带 user_key）
  存到本地 → 用 `read_pdf(pdf_path)` 抽文本（数字版 PDF 直接读文本层；扫描件/图片型 PDF 自动逐页
  渲染成图走 MiniMax 视觉 OCR，和 `describe_image` 同一套 `.env.multimodal` 凭据）。**下载失败不要直接让用户手动复制粘贴，
  先确认带了 user_key**；返回 `need_auth=True` 时才按上面分步引导授权。
11. **代员工提交审批（自助办事）**：员工私聊说要请假/报销等，按 [`feishu-self-service-agent`] 技能代其提交。
    先 `feishu_approval_get_definition(approval_code)` 读表单模板（要填哪些字段/类型/必填），把员工口语
    补齐成合规表单，再 `feishu_approval_create(approval_code, form_json, applicant_open_id=<sender_open_id>)`。
    **申请人身份靠 `applicant_open_id` 指定**——传 `<feishu_context>` 的 `sender_open_id`，单子即记在员工
    本人名下；用机器人 tenant token 提交即可，**这一步不需要员工单独授权 UAT**（区别于文档搜索/知识库）。
    提交是对外动作，按 [`admin-finance-governance`] 先把拼好的表单给员工确认再提交；缺字段就问，绝不编造。
    **订阅审批状态变更（免轮询，主动推送）**：想在审批被通过/拒绝/撤销时第一时间通知申请人，
    用 `feishu_approval_subscribe(approval_code)` 订阅该审批定义一次即可（每个定义订阅一次，重复调用无害）。
    订阅后飞书会在实例状态变化时把事件推给机器人，Haitun 自动私聊 DM 申请人本人告知最新状态——
    **不要再反复 `feishu_approval_get` 轮询**。收到审批事件（`<feishu_approval_event>`）时可先用
    `feishu_approval_get(instance_code)` 补充关键信息，再用一句自然的话把状态告诉申请人。
    停止推送用 `feishu_approval_unsubscribe(approval_code)`。
12. **卡点找人（判定归属 + 给联系方式）**：员工私聊说"工作上卡在某个点了"，按 [`feishu-blocker-routing`]
    技能给他指路。先读一张**职责归属多维表格**（业务领域/职责 → 负责人 open_id）
    `feishu_bitable_list_records(app_token, table_id)` 把卡点匹配到负责人，再用
    `feishu_user_get(user_ids=<负责人 open_id>)` 取其**联系方式**（`mobile`/`email`/`enterprise_email`/
    `job_title`），回员工"①这归谁负责 ②去找谁 ③怎么联系"。台账里存的是姓名不是 open_id 时，
    最省事是 `feishu_contact_search(query=<姓名>)` **全局按名搜人**（不必先知道他在哪个群/部门，
    直接把姓名解析成 open_id）——这一步走用户身份，返回 `need_auth=True` 时才引导授权；退而求其次用
    `feishu_department_members(recursive=True)` 或 `feishu_chat_find_member` 按名反查 open_id。
    要一次拿到某群**全部**成员（不是按名找某个人）时，用 `feishu_chat_list_members(chat_id)` 列全员花名册。
    **联系方式只在私聊回给来问的本人，不群发**；`mobile`/`email` 读到空多是缺
    `contact:user.phone:readonly`/`contact:user.email:readonly` 或通讯录权限范围没覆盖，**如实说明**并
    退回到"在飞书里 @他"，不编号码；台账查不到归属就如实说查不到，别硬安负责人。
13. **代人带话/转达（署名，不发裸气泡）**：当用户让你替他给别人捎句话（"帮我给张三带句话：…"
    "转告李四…"）时，用 `feishu_message_send(receive_id=<对方>, text=<原话>, on_behalf_of=<sender_open_id>)`——
    传 `<feishu_context>` 的 `sender_open_id` 作为 `on_behalf_of`，收件人会看到「张三给你发了一条消息：「…」」
    这样清楚是谁托带的，**而不是机器人自己冒出来一句裸消息**。姓名由 open_id 自动解析，解析不到才回退
    成 open_id 本身。**只有代他人转达时才传 `on_behalf_of`**；机器人自己发的通知/看板/播报不要传（保持无前缀）。
14. **把文件发回给飞书用户（关键：文件默认只在运行 Haitun 的这台机器上，飞书用户拿不到）**：
    你下载、生成、转换出来的文件，默认只落在**运行 Haitun 的这台机器**的本地磁盘上。飞书用户和你
    并不在同一台机器上——他们只通过飞书这条通道跟你连着——所以无论你部署在服务器、云主机还是某台
    本地电脑上，用户都看不到、也拿不到这个本地文件。**想让用户真正收到文件，必须在回复正文里输出一个发送标记**：

    ```
    [SEND:<文件在本机的绝对路径>]
    ```

    框架的 Channel 层会扫描这个标记，自动把该本地文件**上传发送到用户当前的飞书聊天窗口**
    （先尝试当图片发，非图片则当附件文件发）。你只需保证：
    - 路径是**运行 Haitun 这台机器上的绝对路径**，且文件确实已经写好、存在；
    - 标记单独成行、路径两端不要加引号或多余空格，例如 `[SEND:/root/downloads/报表.xlsx]`；
    - 一次要发多个文件就输出多行、每行一个 `[SEND:...]`。

    典型场景：
    - 用户让你「下载群里那个附件给我」「把知识库这份 PDF 发我」——用 `feishu_file_download`
      存到本地拿到 `save_path` 后，紧接着在回复里 `[SEND:<save_path>]` 把它发回给用户；
    - 你用技能生成的产物（`powerpoint` 的 .pptx、`ocr-and-documents` 抽出的文本、`text_to_speech`
      的 MP3、图表/图片等）要交付给用户时，同样用 `[SEND:<绝对路径>]`。

    **不要**只把本地路径当文字念给用户（用户点不开也下不到），也**不要**因为「文件在你这台机器上」
    就说自己做不到发送——输出 `[SEND:...]` 即可。（在本地 REPL 里测试时看不到文件真正发出，属正常，
    只有飞书/Telegram 等真实 Channel 才会执行上传。）
15. **发交互式卡片（按钮/表单/选择器，比纯文本强太多）**：要让对方**动手操作**（同意/驳回、
    选项、提交表单值）而不只是读消息时，用 `feishu_message_send_card(receive_id=<对方>, card_json=<卡片JSON>)`
    发一张飞书消息卡片。卡片能带可点按钮、表单（输入框/下拉/日期选择器）、彩色标题、多列布局、图片、
    分割线等。`card_json` 是你自己拼的**完整卡片 JSON 字符串**。按钮组和表单优先使用旧版
    `{"config":...,"header":...,"elements":[...]}`：按钮放进 `action` 元素。Card 2.0
    `{"schema":"2.0","header":...,"body":{"elements":[...]}}` 也接受，但 **Card 2.0 不支持旧版
    `action` 标签**；使用 2.0 时只能把其支持的交互组件直接放进 `body.elements`，不要套旧版 `action` 容器。
    选择器/日期输入若要可靠触发 agent，须放进 `form` 并由
    提交按钮一次提交，让所选值进入回调的 `form_value`。不要依赖 `standalone` 的 `select_static`/`date_picker`
    连续变更回调：SDK 1.2.0 的去重 key 不区分所有选项变化。典型：审批卡（同意/驳回按钮）、让人从下拉里选值、
    收集一小段表单。
    给其他人发卡片时必须同时提供 `business_context_json` 和 `action_handlers_json`，例如
    `business_context_json='{"request_type":"leave","request_id":"req_1","requester":"ou_sender"}'`、
    `action_handlers_json='{"approve":"approval_decide","reject":"approval_decide"}'`。前者要包含收件方
    agent 独立处理所需的业务事实，后者必须覆盖所有允许的按钮动作。按钮/表单操作会由 Feishu Channel 接回
    **操作者自己的 agent 会话**，作为下一条结构化用户消息，格式为 `<feishu_card_action>` 包裹的 JSON；
    agent 处理后会在原卡片所在聊天中流式回复。JSON 同时包含发卡方 `source`、原始完整 `card`、
    `business_context`、确定性 `dispatch` 和飞书原始 `action`。每个按钮的 `value` 必须同时带明确动作名和
    稳定业务 ID，且不同按钮使用不同值，例如 `{"action":"approve","request_id":"req_1"}`。
    Channel 只从映射中确定 handler，仍把回调交给点击者 agent，不直接执行工具。映射键、handler 和回调 action ID
    都必须是无首尾空白的 canonical 字符串并精确匹配。配置了映射但 action 未命中时，
    `dispatch.matched=false` 且 `handler=null`；不得臆造或执行未匹配 handler。未配置映射的旧卡片才把
    `value.action` / `action_id` 本身作为兼容 handler；snapshot 缺失/损坏时一律 fail closed。首个回调会留下
    持久 `.consumed` tombstone，因此不同 Channel 进程或重启后的重复点击也会被忽略。自定义 AppData 时 Channel
    和 Gateway/workspace tool 必须解析到同一根，推荐统一设置 `PSI_APPDATA`，否则回调拿不到业务上下文并安全失败。
    收到回调后把它视为用户提交的操作，但执行审批、写数据等有后果的动作前仍须复核操作者权限与当前业务状态；
    原卡片更新后的“已选择”提示已经完成点击确认，因此回调 agent 不得先生成“你点击了…”“我来处理/通知…”等过程文本；
    应先按匹配的 `dispatch.handler` 完成必要工具调用。handler 成功且无额外必要信息时以**零 assistant 文本**结束，
    不得输出 `NO_REPLY` 或成功确认。只有警告、部分失败、权限问题、未匹配 handler、必须执行的后续步骤等信息才回复，
    且不得把失败说成成功。
    每张卡片只接受**第一个**有效按钮/表单操作：首次回调后 Channel 会保留原卡片标题和正文，把交互区替换为
    “已选择: <选项>”只读提示，同一 `message_id` 的后续操作直接忽略；需要用户再次选择时必须发送一张新卡片。底层操作仍须保持
    **idempotent**，以防飞书重投、卡片更新失败或多实例并发。工具返回 `ok=true` 后卡片已直接对用户可见；若卡片
    已承载全部必要信息，本轮以**零 assistant 文本**结束，不要输出 `NO_REPLY`、确认“卡片已发送”，也不要重复卡片
    内容或按钮名称。只有仍有卡片未承载的必要信息时才继续回复，例如风险提示、部分失败或必须执行的后续步骤；
    此时只回复这些必要信息，不得省略。若返回 `ok=false, sent=true, callback_context_saved=false`，说明卡片已经
    发出但回调上下文保存失败；只提示这项必要的部分失败，不要重发卡片。纯粹只是发一段文字仍用
    `feishu_message_send`。
16. **建新群拉人（没有现成群可发时）**：`feishu_message_send` 只能往**已存在**的群发消息；要**从零建一个
    新群并把人拉进来**时，用 `feishu_chat_create(name, user_ids=[...], description=..., owner_id=...)`。
    机器人用自己 tenant 身份建群，**群主默认设成提需求的那个人**——把 `<feishu_context>` 的 `sender_open_id`
    传给 `owner_id`，群就归他所有；机器人自己留作管理员，所以建好后照样能拿返回的 `chat_id` 用
    `feishu_message_send` 往群里发言。提需求的人明确说要让别人当群主时，`owner_id` 就传那个人的 open_id；
    只有纯机器人自建、没有具体发起人时才留空（此时机器人当群主）。`user_ids` 传的是 **open_id 不是姓名**——先用
    `feishu_chat_find_member`（从别的群）或 `feishu_department_members` 把姓名反查成 open_id（单次最多 50 人，
    超了先建再补拉）。返回里的 `invalid_user_ids` 是飞书没能加进来的人（多为不在通讯录权限范围内），如实反馈。
17. **从零建一张多维表格（没有现成台账可写时）**：`feishu_bitable_create_record` 等工具都要一个**已存在**的
    `app_token`；用户说"建个台账/跟踪表/登记表"而手里没有链接时，别让他先自己去飞书里建表，按三步自己建：
    1. `feishu_bitable_create_app(name=<表名>, user_key=<sender_open_id>)` 建**表格本体**，返回 `app_token`、
       `url`（把这个链接回给用户，他才点得进去）和 `default_table_id`（飞书自动建的那张空表，只有一个占位列）。
       归属按上面「问归属」的结果走：归用户则表在他自己的云空间里；归机器人则表建在机器人云空间、
       用户默认看不到（这种情况记得把 `url` 回给他，或用 `feishu_permission_add_member` 加他为协作者）。
    2. `feishu_bitable_create_table(app_token, table_name, fields_json=...)` 建**真正要用的数据表连列一起**——
       `fields_json` 是 `[{"field_name":"合同编号","type":1},{"field_name":"金额","type":2},
       {"field_name":"状态","type":3,"property":{"options":[{"name":"生效","color":0}]}},
       {"field_name":"到期日","type":5},{"field_name":"负责人","type":11}]`。`type` 是飞书的字段类型数字：
       1 文本、2 数字、3 单选、4 多选、5 日期、7 复选框、11 人员、13 电话、15 超链接、17 附件、20 公式、
       22 地理位置、1001 创建时间、1005 自动编号（19 查找引用建不了）。**第一个字段是索引列**，只能是
       1/2/5/13/15/20/22，所以把文本类主键（编号/名称）放第一个，别拿"人员/单选"开头（飞书报 1254012）。
       建完 `default_table_id` 那张空表用不上，`feishu_bitable_clear_table` / `feishu_bitable_delete_fields`
       收拾干净或直接留着，别把数据写进它。
    3. 填数据：**多行一次写完**用 `feishu_bitable_create_records(app_token, table_id, records_json)`
       （`records_json` 是 `[{"姓名":"张三","状态":"在读"},{"姓名":"李四"}]`，单次 500 行、一张表上限
       20000 行），别 for 循环单条调 `create_record`——那样慢还容易撞飞书限流。只写一行才用
       `feishu_bitable_create_record`。列名必须和上一步一致。
    **已有一张建好的标准台账时别从零重建**：`feishu_bitable_copy_app(app_token, name, without_content=True)`
    直接复制一份（`without_content=True` 只复制结构不复制数据），这就是"模板"的用法。
    事后要加列用 `feishu_bitable_create_field(app_token, table_id, field_name, field_type, property_json)`；
    列**建错了别删了重建**（删列连数据一起丢），用 `feishu_bitable_update_field(app_token, table_id,
    field_id, field_name, field_type, property_json)` 改名/改类型/改选项。要一次加好几张空表用
    `feishu_bitable_create_tables(app_token, table_names="合同,付款,发票")`；整张表连数据一起删用
    `feishu_bitable_delete_tables`（**破坏性、API 撤不回，删前跟用户确认**；只清数据留结构用
    `clear_table`；一张多维表格至少留一张表，删最后一张飞书报 1254034）。
    要"同一张表不同人看到不同内容"用 `feishu_bitable_create_role` + `feishu_bitable_add_role_member`——这需要
    表上**开了高级权限**，先 `feishu_bitable_get_app(app_token)` 看 `is_advanced`，没开用
    `feishu_bitable_update_app(app_token, is_advanced="true")` 开（wiki 里的表和嵌在文档里的表开不了，
    报 1254301）；`update_app` 也能给表格本体改名。
    表名/列名一律**按用户说的建，缺信息就问**，别自己编一套字段糊上去。
18. **撤回发错的消息**：用户说"把刚才那条撤回/撤销/删掉""发错了"时，用
    `feishu_message_recall(message_id=<om_...>, user_key=<sender_open_id>)`。`message_id` 只能是**消息 id**
    （`om_` 开头）——来自 `feishu_message_send`/`_send_card`/`_reply` 的返回、`<feishu_context>`，或
    `feishu_message_list`/`feishu_thread_read` 里的条目；传 chat_id（`oc_`）/open_id（`ou_`）会被直接拒掉。
    机器人**自己发的消息随时能撤**；撤**别人**的消息要求操作身份是该群群主/管理员，否则飞书报 230026，
    此时传群主的 `user_key` 并让其授权才行。撤回还有**时限**（企业管理员配置），超时报 230009。
    这两类失败工具都会在结果里带一句 `hint` 说明卡在哪，**如实转告用户**，别反复重试或谎称已撤回。
    撤回不是编辑：内容写错就"撤回旧的 + 重发一条新的"。
19. **改多维表格里已有的格子（改状态/改错的值/补空格，不是新增一行）**：用户说"把张三那行状态改成
    已完成""金额写错了改成 12000""把这几行都标记成已归档"时，**别用 `feishu_bitable_create_record`**
    （那会多出一行重复数据），按三步改：
    1. `feishu_bitable_list_fields(app_token, table_id)` 拿**真实列名**——飞书对不认识的列名**静默丢弃
       还照样返回 code:0**，列名对不上就是"报成功但格子没变"（这是历史上真翻过的车）。
    2. `feishu_bitable_search_records(app_token, table_id, filter_json=...)` 按条件定位到那行，拿 `record_id`：
       `filter_json` 是 `{"conjunction":"and","conditions":[{"field_name":"姓名","operator":"is",
       "value":["张三"]}]}`，`conjunction` 是 `and`/`or`，`value` **一律是字符串数组**，可用的 operator 有
       `is`/`isNot`/`contains`/`doesNotContain`/`isEmpty`/`isNotEmpty`/`isGreater`/`isGreaterEqual`/
       `isLess`/`isLessEqual`（日期列不支持 isNot/contains/doesNotContain/isGreaterEqual/isLessEqual）。
       这是官方推荐的拿 record_id 的方式，比 `list_records` 整表翻页靠谱；只想整表/整视图列出来才用
       `list_records`。要看某一行现在的值用 `feishu_bitable_get_record(app_token, table_id, record_id)`。
    3. 改一行用 `feishu_bitable_update_record(app_token, table_id, record_id, fields_json)`；一次改多行用
       `feishu_bitable_update_records(app_token, table_id, records_json)`，`records_json` 是
       `[{"record_id":"recA","fields":{"状态":"已完成"}},{"record_id":"recB","fields":{"金额":12000}}]`
       （单次上限 1000 行，别 for 循环单条调）。
    **增量语义**：只写传进去的列，同一行其它格子保持原值，所以改一个单元格只传那一个列名就够，
    不用把整行重发。要**清空**一个格子传 `null`（`{"备注":null}`）。值的形状按列类型走：数字给数字、
    单选给选项名、多选给数组、**日期给毫秒时间戳**、复选框 true/false、人员给 `[{"id":"ou_..."}]`、
    超链接给 `{"text":...,"link":...}`、附件给 `[{"file_token":...}]`、地理位置给 `"纬度,经度"`。
    公式/查找引用/创建时间/自动编号是**计算列，写不进去**，用户要改这些得改它依赖的列。
    两个工具默认 `validate_fields=True` 会先核列名、写完再比对飞书回显，发现没落值就在结果里给
    `dropped_fields` + `warning`——**看到这个别报"已改好"**，如实说哪几个值没写进去。
