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
| 1 | `card` | 点一下卡片按钮 | **这一轮立刻收尾**，回调那轮调 `feishu_auth_collect`（不阻塞，那轮也立刻收尾） |
| 2 | `link_auto` | 打开链接点「同意」，**不用复制 code** | 发 `authorize_url`，**这一轮收尾**；调 `feishu_auth_collect` 让码自己回来，或等用户回话那轮 `feishu_auth_check` |
| 3 | `link_manual` | 打开链接点「同意」，**还要复制 code** | 发 `authorize_url`，再拿 code 调 `feishu_auth_complete` |

降级原因写在返回的 `downgraded_from` / `downgrade_reason` 里：**如实告诉用户**为什么用了更麻烦的
方式，别假装走的是更顺的那条。两种降级触发条件：

- 1→2：没有可私聊的 `open_id`（群场景），或卡片没发出去（缺 im 权限、用户没和机器人建过会话、
  飞书限流……）。卡片发不出去时链接仍然能发，所以整件事不会因此失败；
- 2→3：这个部署没有自动接收授权码的通道（既没配 `PSI_OAUTH_CALLBACK_BASE`，回环端口也不可用）。
  此时第 1 级也一并跳过——没有自动回流的卡片，点了还是要手抄，那按钮是个谎。

**第 1 级 `tier=card` 的细节**：卡上「点此授权」按钮同时做两件事——打开飞书授权页（`open_url`）
+ 把这次点击回调给你（`callback`）。

- **发完卡这一轮就收尾**：别在同一轮里等待，也别把链接再当文本发一遍。
  在轮次里等待会占住 Session 的 turn 锁，用户这期间说什么都得排队几分钟；
- 用户点按钮后，你会收到一条 `<feishu_card_action>`，其 `dispatch.handler` 是
  `feishu_auth_collect`、`action.value.user_key` 是该用户。**那一轮**调
  `feishu_auth_collect(user_key=...)`：它把「等授权码」交给一个脱离本轮的后台任务，
  **本轮同样立刻收尾**。收到点击时用户才刚要在浏览器上点「同意」，在这一轮原地等就又把
  会话占住了；码回流后后台自己换好 token 并私聊告诉用户可以继续；
- 想主动确认进度，再调一次 `feishu_auth_collect` 即可（它返回 `status`：`watching` /
  `granted` / `failed` / `timeout`，且不会起第二个收码任务）；
- **卡片是一次性的**：用户点了按钮但没在授权页点「同意」时，这张卡已作废（原卡被改写成
  「已选择」），重新调 `feishu_auth_request` 发一张新的，别让用户再点旧卡；
- 授权卡只能**私聊**发给本人（`receive_id` 默认就是 `user_key`）。待完成的授权记录存在发卡方
  workspace，而群里点卡片会落到点击者自己的私聊会话、读不到这条记录，所以群 id 会跳过这一级。

**第 2、3 级的细节**：把返回的 `authorize_url` **原样发给用户**，让其打开并点「同意授权」，然后

- `tier=link_auto`：**不要向用户索要任何 code**，也**任何一轮都别在工具里干等**。
  发完链接就收尾，然后二选一：调 `feishu_auth_collect(user_key=...)` 让码自己回来（推荐，
  用户不必再回话；后台收到后私聊告知他可以继续），或者请用户点完「同意授权」后回你一句
  （他会看到「授权成功」页），那一轮调 `feishu_auth_check(user_key=...)` 查一眼。返回
  `pending=True` 只是还没点完，不是失败——授权码在取件箱里留存约 10 分钟，晚一轮取毫无损失；
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

如果用户已经给了文档/wiki 链接，直接 `feishu_doc_read`（wiki 链接先用 `feishu_api` 打
`GET /open-apis/wiki/v2/spaces/get_node` 换 `obj_token`）读即可，
**不要多此一举去搜索或授权**。只有当诉求确实需要全库搜索（如「帮我在公司知识库找报销 SOP」
而你手上没有链接）时，才用 `feishu_docs_search`（这一步才需授权）。

### 写入 / 知识库 / 下载类的具体用法（都已 tenant 优先，带上 user_key 即可）

- **建带内容的 wiki 文档，优先用一步到位工具**：
  `feishu_wiki_create_doc_with_content(space_id, title, content, parent_node_token, user_key)`
  一次完成「建节点 + 写正文」，避免分两步（`feishu_wiki_create_doc` 再 `feishu_doc_append_content`）
  时留下**空文档**。若正文写入失败，它会连 `node_token`/`obj_token` 一并回报，可用相同 `user_key`
  调 `feishu_doc_append_content` 补写。
- **在文档里放表格 / 流程图 / 泳道图**：`feishu_doc_append_content` 直接吃 Markdown，
  **表格、列表、待办、引用、代码块、分割线、行内粗体/斜体/删除线/行内码/链接都会被飞书
  转成原生块**——`| 姓名 | 部门 |` 写进去就是一张真正的飞书表格（能拖列宽、能排序、能编辑），
  不再是一堆竖线和横线。所以**普通「文档里带个表格」直接写 Markdown 就行**，不用另外调工具。
  下面几个专门工具解决 Markdown 表达不了的事（都吃 docx 的 `document_id`，也就是
  `feishu_doc_create` 返回的 id，或 wiki 节点的 `obj_token`；带 `user_key`）：
  - 表格（要给**表题自动编号**、或要指定**列宽**、或数据本来就是二维数组不想拼 Markdown 时用）：
    `feishu_doc_append_table(document_id, rows_json, header_row, column_width_json, user_key, caption)`——
    `rows_json` 是二维 JSON 数组，如 `[["姓名","部门"],["张三","研发"]]`，生成飞书原生表格块。
    **和直接写 Markdown 表格怎么选**：只是正文里要一张表 → 直接 Markdown；要自动编号的表题
    或指定列宽 → 用这个。

  - **可编辑的内嵌电子表格**：`feishu_doc_append_sheet(document_id, rows, columns, values_json, header_row, user_key, caption)`——
    在文档里嵌一张**真正的飞书电子表格**（block_type 30，飞书自动新建后端表格），带公式栏、
    单元格格式、筛选，能在文档里直接编辑，也能单独打开。返回 `spreadsheet_token` / `sheet_id` /
    `range`，正是 `feishu_sheet_write` / `_append` / `_format` 要的参数，之后可反复写。
    `values_json` 给了就一次建好并填数（`=` 开头的格子是活公式），尺寸按数据自己定；
    不给就是一张空表，`rows`/`columns` 要多大就多大（飞书**建块**时限死 9x9，工具会自动
    先建小再靠写入撑到你要的尺寸，所以要 30 行就真给 30 行）。
    **和上面 `append_table` 怎么选**：内容是「数据」（要公式、会反复更新、要筛选排序、想当独立
    表格用）→ 用 `append_sheet`；内容是「排成格子的文字」（一小段对照说明，读起来是正文的一部分）
    → 直接写 Markdown 表格或用 `append_table`。这两种表格块只装文字，**没有公式也不能筛选**。

  - **内嵌多维表格**：`feishu_doc_append_bitable(document_id, view_type, user_key, caption)`——
    内容是「一条条记录」（台账、问题列表、报名表：要字段类型、多视图、逐行协作）时用它，
    返回 `app_token` / `table_id`，接着用 `feishu_api` 打 `POST /open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields`
    建字段、`POST .../records` 填数据（多行用 `feishu_bitable_create_records`，它先核对列名）。
  - 流程图：`feishu_doc_append_flowchart(document_id, steps_json, title, user_key, caption)`——
    `steps_json` 是步骤数组 `["提交","审批","归档"]`。**飞书开放接口画不了真正的流程图块**
    （block_type 21 是空画布，API 填不进节点），所以用「单列表格 + ↓ 箭头」如实呈现，可编辑。
  - 泳道图：`feishu_doc_append_swimlane(document_id, lanes_json, stages_json, user_key, caption)`——
    `lanes_json` 可传对象 `{"客户":["下单","付款"],"仓库":["发货"]}`（列=泳道，自动排格），
    或传泳道名数组 `["客户","客服","仓库"]` 再用 `stages_json` 给二维正文行。同样用表格如实呈现。
  这几个都收 `caption`（表题）：**只写内容不写「表N：」**，工具读文档已有的「表 N」自动续号，
  按学术体例写在**表格上方**（图注在下、表题在上），且「表」和「图」是两条互不干扰的序列。
  一句话：正文里的表格直接写 Markdown；要编号表题、要能算数筛选、要逐行协作，才换上面的工具。

- **改文档里已有的内容（不是追加）**：上面的 `append_*` 只会往末尾加，写错一段不必重开一篇——
  用这三个「块级编辑」工具改稿（都吃 docx 的 `document_id` 或 wiki 节点的 `obj_token`）：
  - 先列块拿 id：`feishu_doc_list_blocks(document_id, max_blocks, user_key)` 返回
    `{block_id, block_type, type_name, parent_id, text, editable_text}`。**这是拿到 `block_id`
    的唯一途径**，另两个工具都按 `block_id` 定位。`text` 是 200 字预览（要读全文仍用
    `feishu_doc_read`）；`editable_text=false` 表示该块（图片/表格/分割线）没有文字可改。
    碰到**内嵌的电子表格块**（`type_name="sheet"`）还会多返回 `spreadsheet_token`/`sheet_id`/
    `range`，内嵌多维表格块（`type_name="bitable"`）多返回 `app_token`/`table_id`——
    **要改文档里已有的内嵌表格就靠这个**：先列块拿到这些坐标，再用 `feishu_sheet_write` 等写。
    （这类块本身没有文字，`update_block` 改不了它，内容都在它背后那张表里。）
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
- **列出电子表格的工作表**：走 `feishu-api` 技能的接口表——`feishu_api` 打
  `GET /open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query`，`data.sheets[]` 里
  是每个工作表的 `sheet_id`/`title`/`index`，行列数在 `grid_properties` 里。**`SHEET_ID` 不在
  表格 URL 里**，而所有区域都写成 `"SHEET_ID!A1:B2"`，所以不知道 `SHEET_ID` 时先打这个端点，
  再去读写区域。`row_count` 是表格上限而不是有数据的行数，拿它当数据范围会读回一大片空行。
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
- **表格的结构操作**（建表、改名、加/插/删行列、合并拆分单元格、查找替换、工作表增删复制）：
  走 `feishu-sheet` 技能的接口表，用 `feishu_api` 按表调用。**那份技能里有一处必须照抄不能推理的坑**：
  插行列（`insert_dimension_range`）的 `startIndex/endIndex` 是 **0-based 左闭右开**，
  删行列（`DELETE dimension_range`）却是 **1-based 两端都闭** —— 同一份 `{3, 7}` 插入 4 行、删除 5 行，
  两边都返回成功。另外飞书**没有创建透视表/图表的接口**（要图表走 `feishu_chart`），
  保护范围的写入和条件格式那两个端点未核实，技能里都写清了。
- **云盘的文件管理**（查文档元数据、建文件夹、列目录、复制、移动、建快捷方式、查异步任务）：
  走 `feishu-drive` 技能的接口表。几个要点：复制**不支持文件夹**而移动支持；移动/删除文件夹是
  **异步**的，回一个 `task_id` 要拿 `GET /open-apis/drive/v1/files/task_check` 查；列目录**不递归**
  且 `page_size` 上限 200；`metas/batch_query` 查不到的落在 `failed_list[]` 里不报错。
- **重命名云文档**：飞书**没有统一接口**。表格走 `feishu-sheet` 的改标题端点；wiki 节点走
  `wiki/v2/.../update_title`（只支持 doc/docx/shortcut）；**独立 docx 没有改名接口**，
  只能复制成新名字再删旧的（换了 token、评论和历史留在旧文件上）——这个后果先告诉用户再动手。
- **删除文档/文件**：走 `feishu-drive` 技能的接口表——`DELETE /open-apis/drive/v1/files/:file_token`
  带 `type`，删除进**回收站可恢复**。type 是 docx/doc/sheet/bitable/mindnote/slides/file/folder/shortcut。
  删**知识库(wiki)里的文档**：飞书没有独立删 wiki 节点的接口——先 `feishu_api` 打
  `GET /open-apis/wiki/v2/spaces/get_node` 取 `obj_token`+`obj_type`，再拿 `obj_token` 当
  `file_token`、`obj_type` 当 `type` 删。
  删除不可轻率，动手前先跟用户确认清楚删的是哪一个。
  **回收站没有公开 API**：列出/恢复/清空都得用户自己去飞书客户端做，别去猜一个 `/trash` 路径试。
- **把飞书文档导出成 pdf/docx/xlsx/csv 存到本地**：`feishu_doc_export(token, file_type,
  file_extension, save_path, sub_id="", user_key=...)`。一次调用走完飞书的三步（建任务→轮询→下载）。
  格式按源类型配对：docx/doc → pdf 或 docx，sheet/bitable → xlsx 或 csv；**导 csv 必须给 `sub_id`**
  （表格的 sheet_id 或多维表格的 table_id，因为一个 csv 装不下多张表）。配错格式会本地就拦下来。
  文档过大（107）或图片过多（6000）是导不出来的，重试无用。
- **往文档正文里插本地图片/附件**：`feishu_doc_append_image(document_id, image_path, caption="")` /
  `feishu_doc_append_file(document_id, file_path, caption="")`。各自都是「建空块→上传进这个块→
  PATCH 绑 token」三步，工具替你走完；单个文件 20MB 上限。图片块自己没有图注字段，
  所以 `caption` 会写成下面一个段落。
- **访问/浏览知识库**：`feishu_wiki_list_spaces` / `feishu_wiki_list_nodes` 两个列表工具
  已做「tenant 先试，返回空且带了 user_key 时自动改用户身份重试」。带上 `user_key=<sender_open_id>`：
  `feishu_wiki_list_spaces(user_key=...)` 列库 → `feishu_wiki_list_nodes(space_id, user_key=...)` 列文档
  → `feishu_api` 打 `GET /open-apis/wiki/v2/spaces/get_node`（`query_json='{"token": …}'`）拿 obj_token
  → `feishu_doc_read` 读正文。**不要因为一时返回空就说"企业没有知识库"**——两个列表工具确认带了
  user_key 即可；而**节点详情那一步的空结果重试要你自己发起**（它走通用端点，没有那层自动重试）:
  `data.node` 是空的就带 `user_key` 加 `prefer="user"` 再问一次，别当成节点不存在。
- **知识库的搜索与管理**（全文搜索、移动/复制节点、把云文档搬进知识库、空间设置、成员增删）：
  走 `feishu-wiki` 技能的接口表，用 `feishu_api` 按表调用。四件要先知道的事：
  **① 搜索只吃 user token**（`POST /open-apis/wiki/v1/nodes/search`，必须带 `user_key` +
  `prefer="user"`）—— 飞书把「搜索 Wiki」和「创建知识库」列为 wiki 里唯二不支持 tenant token 的接口，
  拿机器人身份搜是「成功但空」，不是报错。**② 把云文档搬进知识库是异步的**：返回 `task_id`，
  拿它打 `GET /open-apis/wiki/v2/tasks/:task_id` 且必须带 `task_type=move`（只有这一个值），
  且**只有发起的那个身份查得到**；搬完文档链接会从 `/docx/<token>` 变成 `/wiki/<node_token>`，
  这件事要先跟用户说。**③ 移动节点要三处编辑权限**（节点本身、原父容器、目标父容器），
  131006 的返回里会写缺的是 source 还是 destination，照着要权限别改参数重试。
  **④ 加成员看空间类型**：`public` 空间只能加管理员（加成员返回 131101）、`person` 空间
  不能加别的管理员 —— 先 `GET /open-apis/wiki/v2/spaces/:space_id` 读 `space_type`/`visibility`。
  改空间设置和加/删成员都要求调用方是**这个空间的管理员**，机器人一般不是，带 `user_key` 以本人身份调。
  重命名 wiki 节点仍在 `feishu-drive` 那份表里（只支持 doc/docx/shortcut 三种节点类型）。
- **读知识库里的 PDF/附件（下载）**：飞书文档 API 只能直接读 docx/doc/sheet；PDF、图片等要先下载再解析。
  `feishu_file_download(source, save_path, user_key=...)` 已 tenant 优先、机器人下不到时自动回退到用户身份。
  **`source_type` 分三种，选错是 404 不是自动跳转**：`"media"`（默认，文档**里面**的图片/附件）、
  `"file"`（云盘里独立的资源文件，如别人上传的 PDF）、`"url"`（直链，审批表单附件就是这种，约 12 小时失效）。
  两个 token 端点服务的是不相交的东西；而飞书自己的在线文档（docx/sheet/bitable）**两个都不管**，
  那些要用 `feishu_doc_export` 导出。
  流程：`feishu_api` 打 `GET /open-apis/wiki/v2/spaces/get_node`（带 user_key）拿 `obj_token`
  → `feishu_file_download`（带 user_key）
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
    用 `feishu_api POST /open-apis/approval/v4/approvals/:approval_code/subscribe(approval_code)` 订阅该审批定义一次即可（每个定义订阅一次，重复调用无害）。
    订阅后飞书会在实例状态变化时把事件推给机器人，Haitun 自动私聊 DM 申请人本人告知最新状态——
    **不要再反复 `feishu_approval_get` 轮询**。收到审批事件（`<feishu_approval_event>`）时可先用
    `feishu_approval_get(instance_code)` 补充关键信息，再用一句自然的话把状态告诉申请人。
    停止推送用 `feishu_api POST /open-apis/approval/v4/approvals/:approval_code/unsubscribe(approval_code)`。
12. **卡点找人（判定归属 + 给联系方式）**：员工私聊说"工作上卡在某个点了"，按 [`feishu-blocker-routing`]
    技能给他指路。先读一张**职责归属多维表格**（业务领域/职责 → 负责人 open_id）
    （`feishu_api` 打 `GET /open-apis/bitable/v1/apps/:app_token/tables/:table_id/records`）把卡点匹配到负责人，再用
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
14. **把文件发回给用户（关键：文件默认只在运行 Haitun 的这台机器上，用户拿不到）**：
    你下载、生成、转换出来的文件，默认只落在**运行 Haitun 的这台机器**的本地磁盘上。用户和你
    并不在同一台机器上——他只通过当前这条通道（Web 控制台 / 飞书 / Telegram）跟你连着——所以无论
    你部署在服务器、云主机还是某台本地电脑上，用户都看不到、也拿不到这个本地文件。**想让用户真正
    收到文件，必须在回复正文里输出一个发送标记**：

    ```
    [SEND:<文件在本机的绝对路径>]
    ```

    框架的 Channel 层会扫描这个标记，自动把该本地文件**上传发送到用户当前所在的聊天窗口**
    （先尝试当图片发，非图片则当附件文件发）。**通道是框架按用户当前所在位置自动选的，不由你挑**，
    你也不需要知道现在是哪条通道。你只需保证：
    - 路径是**运行 Haitun 这台机器上的绝对路径**，且文件确实已经写好、存在；
    - 标记单独成行、路径两端不要加引号或多余空格，例如 `[SEND:/root/downloads/报表.xlsx]`；
    - 一次要发多个文件就输出多行、每行一个 `[SEND:...]`。

    **绝不要拿 `feishu_*` 工具当交付手段**（这是真出过的事故：用户在 Web 控制台只让转换一个文档，
    agent 却去 `feishu_chat_find("Haitun团队")` 想发到一个用户压根没提的飞书群）。`feishu_*` 的
    发送/上传接口投递到**飞书**，那和「用户当前这个对话」是两个不同的目的地——用户在 Web 控制台时
    永远收不到。判断依据很简单：
    - 用户消息里有 `<feishu_context>` → 这一轮来自飞书；
    - **没有**这个块 → 你**不在**飞书上（Web 控制台 / Telegram / CLI / 定时任务都属此类），
      `feishu_*` 发出去的东西用户看不到。没看到就按「不在飞书」处理。
    只有用户**明确要求**「发到某个飞书群/某个人/某篇文档」时才用 `feishu_*` 消息工具——那是一件
    独立的任务，而不是「把文件发给我」的答案。

    典型场景：
    - 用户让你「下载群里那个附件给我」「把知识库这份 PDF 发我」——用 `feishu_file_download`
      存到本地拿到 `save_path` 后，紧接着在回复里 `[SEND:<save_path>]` 把它发回给用户
      （注意：`feishu_file_download` 是**读取**飞书上的文件，交付仍然靠 `[SEND:]`）；
    - 你用技能生成的产物（`powerpoint` 的 .pptx、`ocr-and-documents` 抽出的文本、`text_to_speech`
      的 MP3、图表/图片等）要交付给用户时，同样用 `[SEND:<绝对路径>]`。

    **不要**只把本地路径当文字念给用户（用户点不开也下不到），也**不要**因为「文件在你这台机器上」
    就说自己做不到发送——输出 `[SEND:...]` 即可。（在本地 REPL 里测试时看不到文件真正发出，属正常，
    只有 Web 控制台/飞书/Telegram 等真实 Channel 才会执行上传。）
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
    持久 `.consumed` tombstone，因此不同 Channel 进程或重启后的重复点击也会被忽略（`multi_use=True` 时墓碑
    降为 per-action `{message_id}.{action}.consumed`，逐行各拒一次）。自定义 AppData 时 Channel
    和 Gateway/workspace tool 必须解析到同一根，推荐统一设置 `PSI_APPDATA`，否则回调拿不到业务上下文并安全失败
    （未显式传 `--appdata` 且配了 `gateway_url` 时，Channel 会经 `GET /defaults` 向 Gateway 现问该根，
    因为 Channel 是兄弟进程、继承不到 Gateway 导出的 `PSI_APPDATA`；显式传参仍然优先）。
    收到回调后把它视为用户提交的操作，但执行审批、写数据等有后果的动作前仍须复核操作者权限与当前业务状态；
    原卡片更新后的“已选择”提示已经完成点击确认，因此回调 agent 不得先生成“你点击了…”“我来处理/通知…”等过程文本；
    应先按匹配的 `dispatch.handler` 完成必要工具调用。handler 成功且无额外必要信息时以**零 assistant 文本**结束，
    不得输出 `NO_REPLY` 或成功确认。只有警告、部分失败、权限问题、未匹配 handler、必须执行的后续步骤等信息才回复，
    且不得把失败说成成功。
    **默认**每张卡片只接受**第一个**有效按钮/表单操作：首次回调后 Channel 会保留原卡片标题和正文，把交互区替换为
    “已选择: <选项>”只读提示，同一 `message_id` 的后续操作直接忽略；需要用户再次选择时必须发送一张新卡片。
    **要一张卡承载多条各自独立勾选的待办时传 `multi_use=True`**：一次性的粒度从整张卡降到单个 `value.action`，
    勾一行只结那一行（渲染成 `● ~~文字~~` 并原地更新卡片）、其余行按钮保留，重复点同一行仍恰好被拒一次
    （跨进程、跨重启有效）。此时每行的 `action` **必须唯一且规范**（无首尾空白），撞名会让两行互相顶掉，
    没有可用 action id 的行会退回整卡去重、退化成普通单次卡。连点会被 Channel 合并成一个回合，
    你会收到 `<feishu_card_action_batch count="N">` 包住 N 条 `<feishu_card_action>`：**每条都要逐个处理
    （漏一条就丢一次动作），但只回一条消息**。同意/驳回一类「第二个答案必须不可能」的卡片一律留 `False`。
    标准「今日待办清单」直接用 `feishu_todo_card_send`（见 `feishu-todo-card` skill），不必自己拼多选卡。
    底层操作仍须保持
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
17. **从零建一张多维表格（没有现成台账可写时）**：写数据都要一个**已存在**的
    `app_token`；用户说"建个台账/跟踪表/登记表"而手里没有链接时，别让他先自己去飞书里建表，按三步自己建。
    多维表格的端点表在 **`feishu-bitable` 技能**里，读它再用 `feishu_api` 调；下面只说流程和坑。
    1. `feishu_api` POST `/open-apis/bitable/v1/apps`（body `{"name":"<表名>"}`）建**表格本体**，返回
       `app_token`、`url`（把这个链接回给用户，他才点得进去）和 `default_table_id`（飞书自动建的那张空表，
       只有一个占位列）。归属按上面「问归属」的结果走：归用户则表在他自己的云空间里；归机器人则表建在
       机器人云空间、用户默认看不到（这种情况记得把 `url` 回给他，或按 `feishu-permission` 技能
       加他为协作者）。
    2. `feishu_bitable_create_table(app_token, table_name, fields_json=...)` 建**真正要用的数据表连列一起**——
       `fields_json` 是 `[{"field_name":"合同编号","type":1},{"field_name":"金额","type":2},
       {"field_name":"状态","type":3,"property":{"options":[{"name":"生效","color":0}]}},
       {"field_name":"到期日","type":5},{"field_name":"负责人","type":11}]`。`type` 是飞书的字段类型数字：
       1 文本、2 数字、3 单选、4 多选、5 日期、7 复选框、11 人员、13 电话、15 超链接、17 附件、20 公式、
       22 地理位置、1001 创建时间、1005 自动编号（19 查找引用建不了）。**第一个字段是索引列**，只能是
       1/2/5/13/15/20/22，所以把文本类主键（编号/名称）放第一个，别拿"人员/单选"开头（飞书报 1254012）。
       建完 `default_table_id` 那张空表用不上，`feishu_bitable_clear_table` 或 `feishu_api` DELETE
       `.../tables/:table_id/fields/:field_id` 收拾干净或直接留着，别把数据写进它。
    3. 填数据：**多行一次写完**用 `feishu_bitable_create_records(app_token, table_id, records_json)`
       （`records_json` 是 `[{"姓名":"张三","状态":"在读"},{"姓名":"李四"}]`，单次 500 行、一张表上限
       20000 行），别 for 循环单条调单行接口——那样慢还容易撞飞书限流。只写一行才用 `feishu_api` POST
       `.../tables/:table_id/records`。列名必须和上一步一致。
    **已有一张建好的标准台账时别从零重建**：`feishu_api` POST `/open-apis/bitable/v1/apps/:app_token/copy`
    （body 带 `without_content: true`）直接复制一份（只复制结构不复制数据），这就是"模板"的用法。
    事后要加列用 `feishu_api` POST `.../tables/:table_id/fields`；
    列**建错了别删了重建**（删列连数据一起丢），用 `feishu_bitable_update_field(app_token, table_id,
    field_id, field_name, field_type, property_json)` 改名/改类型/改选项。要一次加好几张空表用
    `feishu_api` POST `.../tables/batch_create`；整张表连数据一起删用 `feishu_api` POST
    `.../tables/batch_delete`（**破坏性、API 撤不回**，所以端点表要求带 `confirm='DELETE_BITABLE_TABLES'`，
    删前跟用户确认；只清数据留结构用 `feishu_bitable_clear_table`；一张多维表格至少留一张表，
    删最后一张飞书报 1254034）。
    **视图、仪表盘、自动化流程、记录附件**也都在那份技能的接口表里，四件事各有一处反直觉：
    ① `view_type` 只有 `grid`/`kanban`/`gallery`/`gantt`/`form` 五种，一张表最多 200 个视图，
    **最后一个视图删不掉**（1254023）；② 改视图的筛选条件按 **`field_id`** 写而不是列名
    （这一域别的写接口全是列名，只有这里例外，填列名报 1254009），且 `property` 要整个对象
    一起给，先 GET 读回当前的再改，否则可能把隐藏列设置一起清掉；③ **飞书没有「新建仪表盘」
    和「建图表」的接口**，只能列出和 `copy` 已有的那份（让用户先手工搭一个当模板），
    要凭数据出图给用户看走 `feishu_chart`；自动化流程同理**只能开关**（`status` 只有
    `Enable`/`Disable`，大写），建流程/改流程内容都没有接口 —— 批量导数据前 `Disable`、
    导完记得 `Enable` 回来，停用状态不会自己恢复；④ **附件列写不进本地路径**，要先
    `feishu_drive_upload(file_path, parent_node=<app_token>, parent_type="bitable_file")`
    （图片列用 `bitable_image`，`parent_node` 是 base 的 app_token 不是 table_id）拿 `file_token`，
    再把 `{"合同扫描件":[{"file_token":"boxcnXXX"}]}` 写进格子 —— 值必须是**数组套对象**，
    直接给字符串会被静默丢弃。**19 查找引用字段建不出来**（建字段接口的 `type` 枚举里没有 19，
    文档也明说不支持），要这一列只能让用户在客户端里加；20 公式能建但建的时候设不了公式表达式。
    要"同一张表不同人看到不同内容"用 `feishu_api` POST `.../roles` + POST `.../roles/:role_id/members`——这需要
    表上**开了高级权限**，先 `feishu_api` GET `/open-apis/bitable/v1/apps/:app_token` 看 `is_advanced`，没开用
    `feishu_api` PUT `/open-apis/bitable/v1/apps/:app_token`（body `{"is_advanced": true}`）开（wiki 里的表和
    嵌在文档里的表开不了，报 1254301）；同一个 PUT 也能给表格本体改名。
    表名/列名一律**按用户说的建，缺信息就问**，别自己编一套字段糊上去。
18. **撤回发错的消息**：用户说"把刚才那条撤回/撤销/删掉""发错了"时，用
    `feishu_message_recall(message_id=<om_...>, user_key=<sender_open_id>)`。`message_id` 只能是**消息 id**
    （`om_` 开头）——来自 `feishu_message_send`/`_send_card`/`_reply` 的返回、`<feishu_context>`，或
    `feishu_message_list`/`feishu_thread_read` 里的条目；传 chat_id（`oc_`）/open_id（`ou_`）会被直接拒掉。
    机器人**自己发的消息随时能撤**；撤**别人**的消息要求操作身份是该群群主/管理员，否则飞书报 230026，
    此时传群主的 `user_key` 并让其授权才行。撤回还有**时限**（企业管理员配置），超时报 230009。
    这两类失败工具都会在结果里带一句 `hint` 说明卡在哪，**如实转告用户**，别反复重试或谎称已撤回。
    撤回是"让这条消息不该存在"；只是**内容写错**就别撤回重发，用下面第 20 条的编辑。
19. **改多维表格里已有的格子（改状态/改错的值/补空格，不是新增一行）**：用户说"把张三那行状态改成
    已完成""金额写错了改成 12000""把这几行都标记成已归档"时，**别新增一行**
    （那会多出一行重复数据），按三步改：
    1. `feishu_api` GET `.../tables/:table_id/fields` 拿**真实列名**——飞书对不认识的列名**静默丢弃
       还照样返回 code:0**，列名对不上就是"报成功但格子没变"（这是历史上真翻过的车）。
    2. `feishu_bitable_search_records(app_token, table_id, filter_json=...)` 按条件定位到那行，拿 `record_id`：
       `filter_json` 是 `{"conjunction":"and","conditions":[{"field_name":"姓名","operator":"is",
       "value":["张三"]}]}`，`conjunction` 是 `and`/`or`，`value` **一律是字符串数组**，可用的 operator 有
       `is`/`isNot`/`contains`/`doesNotContain`/`isEmpty`/`isNotEmpty`/`isGreater`/`isGreaterEqual`/
       `isLess`/`isLessEqual`（日期列不支持 isNot/contains/doesNotContain/isGreaterEqual/isLessEqual）。
       这是官方推荐的拿 record_id 的方式，比整表翻页靠谱；只想整表/整视图列出来才用 `feishu_api` GET
       `.../tables/:table_id/records`。要看某一行现在的值用 `feishu_api` GET
       `.../tables/:table_id/records/:record_id`。
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
20. **改已经发出去的消息（不撤回、不重发）**：用户说"把刚才那条改成…""数字写错了改一下"
    "补一句"时，用 `feishu_message_edit(message_id=<om_...>, text=<改好的完整内容>, user_key=<sender_open_id>)`。
    消息**保留原 message_id**、保留在会话/话题里的位置，飞书只标一个"已编辑"——比"撤回+重发"好：
    重发会丢 message_id（引用它的回复和话题会断），还会给所有人推一条"撤回了一条消息"。
    编辑是**整条替换**，所以要传改好的**全文**，不是差量；`<at user_id="ou_xxx"></at>` 照样能用
    （会自动改用富文本发，@才渲染得出来）。
    卡片消息用 `feishu_message_edit_card(message_id, card_json)`（不同接口）——审批卡片改成"已通过"、
    按钮置灰、看板刷新都用它，别再发一张新卡片把旧的留在那儿还能点。注意卡片的**按钮回调不会重新注册**
    （回调在发送时就快照好、首次点击即消耗），所以它改的是卡片**显示什么**，不是按钮**触发什么**；
    可选动作本身要变就得用 `feishu_message_send_card` 发新卡片。
    三条硬限制，答复用户前先知道：**只有发送者能编辑**（机器人只能改自己发的；要改某人自己发的消息，
    传该用户的 `user_key` 并让其授权）、一条消息最多编辑 **20 次**、超过企业管理员配置的**可编辑时限**就只能撤回重发。
    图片/文件/音频/视频消息**不能编辑**，只能撤回重发。失败时结果里带 `hint` 指明卡在哪，如实转告。
21. **给消息加表情回应（收到/已处理，不占一条消息）**：用户说"收到就行""给这条点个赞"
    "标记一下已处理"时，用 `feishu_message_react(message_id=<om_...>, emoji_type=<表情>)`——回应落在原气泡上，
    **不往会话里加消息**，而回一句"好的"会。取消用
    `feishu_message_unreact(message_id, emoji_type=<同一个表情>)`（也可传 `reaction_id`）；
    看谁回应了什么用 `feishu_message_reactions(message_id)`（也是拿 `reaction_id` 的地方，可当轻量点名/投票读）。
    `emoji_type` 传飞书键（`THUMBSUP`/`OK`/`DONE`/`OnIt`/`THANKS`/`Fire`/`PARTY`）、中文（`赞`/`收到`/`完成`/`感谢`）
    或表情本身（`👍`/`✅`/`🎉`）都行，工具会归一化——**飞书这套枚举大小写不统一**（`THUMBSUP` 全大写但
    `Fire`/`OnIt` 首字母大写），照字面猜十次错九次，报 231001。
    只有**加回应的那个身份**能取消它，所以取消时传当初加回应用的同一个 `user_key`；
    同一个表情被多人加过时工具**不猜**，返回 candidates 让你挑 `reaction_id`。
22. **发图片/文件/语音/视频/富文本消息（不只是纯文本和卡片）**：
    - `feishu_message_send_image(receive_id, image_path)`——把**本机图片**发成图片消息（图表、截图、照片）。
      纯文本里放个 URL 只是个链接，云盘文件也不是聊天附件，只有这个能在会话里真的显示一张图。≤10MB。
    - `feishu_message_send_file(receive_id, file_path, file_name="")`——发成可下载附件（PDF/Word/Excel/PPT/zip/任意）。
      ≤30MB；要的是"放云盘、发链接、可协作编辑"就用 `feishu_drive_upload` 而不是这个。
    - `feishu_message_send_audio(receive_id, audio_path, duration_ms=0)`——发成可播放语音。飞书只认
      **OPUS**，.mp3 当语音发直接报 230055，先转（`ffmpeg -i in.mp3 -acodec libopus -ac 1 -ar 16000 out.opus`）
      或者干脆用 `send_file` 当附件发。`text_to_speech` 产出的是 MP3，要发语音得先转。
    - `feishu_message_send_video(receive_id, video_path, cover_image_path="")`——发成可播放视频，只支持 **mp4**、≤30MB；
      不传封面就没有预览帧（封面上传失败不会连视频一起丢，会照发无封面版）。
    - `feishu_message_send_post(receive_id, blocks_json, title="")`——**富文本**：加粗文字、链接、@、图片
      装在**同一个气泡**里（带标题的周报、图表配解说、带链接和@的清单）。比卡片省事（不用拼卡片 JSON、
      不用接回调），比发好几条消息干净。`blocks_json` 是块数组，`tag` 可为
      `text`（可带 `style`: bold/italic/underline/lineThrough）/`a`(`href`)/`at`(`user_id`，`"all"` 是全员)/
      `img`（本机图给 `image_path` 自动上传，或直接给已有 `image_key`）/`code_block`(可带 `language`)/`md`/`hr`，
      分段规则（图片、分割线、markdown 各自独占一行，相邻文字/链接/@合并成一行）工具已经替你处理好。
    这几个工具都是**上传 + 发送两步一起做**的，所以不会把 `image_key` 和 `file_key` 搞混
    （图片走 `im/v1/images` 得 `image_key`，音视频文件走 `im/v1/files` 得 `file_key`，用反了报 230001）。
    要给用户**发一个你刚生成的本地文件**、又不关心细节，最省事的仍是在回复里输出 `[SEND:<绝对路径>]`（第 14 条）；
    这几个工具用在**指定发给谁、指定发成哪种消息类型**（比如把图表发到某个群、把视频带封面发给某人）的时候。
23. **查消息已读未读（谁看了、谁还没看）**：用户说"这条通知大家看了吗""还有谁没读""催一下没看的人"时，用
    `feishu_message_read_status(message_id=<om_...>)`。飞书只开放**已读**名单、**没有**未读接口，所以未读是
    工具自己算的：拉该会话成员名单减掉已读的人（发送者两边都不算）。这一步是**尽力而为**——群成员列表拉不到时
    已读名单照样返回，但只带一句 `note`，所以**先看有没有 `unread_users` 字段再报数**，别把"算不出来"说成"0 人未读"。
    两条飞书硬限制绕不过去，工具会带 `hint`：只能查**机器人自己发**的消息（别人发的报 230012），且只能查
    **7 天内**发的（超期报 230033）。碰上这两种就**如实说查不了**，别编一个已读数字。只要已读名单、不要未读时
    传 `include_unread=False`，省两次调用（大群里尤其值）。
24. **置顶/取消置顶消息**：用户说"把这条置顶""钉在群顶上""取消置顶"时，用
    `feishu_message_pin(message_id)` / `feishu_message_unpin(message_id)`，想知道群里现在置顶了什么用
    `feishu_message_pins(chat_id)`（按置顶时间倒序，只给 message_id + 谁置顶的 + 什么时候，**不含消息正文**，
    要正文再去 `feishu_message_list` 读）。公告、会议链接、群规这类"别让它被刷走"的内容用置顶，比反复重发干净。
    两个方向都是**幂等**的：已置顶的再置顶返回原来那条 pin；**没置顶过的取消置顶飞书也报成功**——所以取消成功
    只说明"现在没置顶"，**别据此说"我把置顶取消了"**，要确认原本有没有先查 `feishu_message_pins`。
    最常见的失败是 **230046：多数群只允许群主/管理员置顶**，机器人通常两者都不是——这时传该管理员本人的
    `user_key`（并让其完成授权）以其身份操作，或让群主放开权限；`hint` 会说明卡在哪一种。
25. **把消息转发给别人/别的群**：用户说"把这条转给张三""同步到那个群""把刚才那段讨论转给老板"时，用
    `feishu_message_forward(message_id, receive_id)`，一次转**多条**用
    `feishu_message_merge_forward(message_ids_json, receive_id)`（合并成一张折叠卡片）。
    **别读出内容再用 `feishu_message_send` 重发**——那样会丢掉**原作者署名**，消息里的图片/附件也会**悄悄没了**；
    转发能原样保留，这在"把客户这句话转给研发"这种要留证据的场合是关键差别。代价是转发**不能改内容**，
    要加说明就转发完再单独发一条。目标类型按前缀自动判断：群 `oc_`、私聊 `ou_`、`on_`、邮箱，以及
    **话题 `omt_`**（转发是唯一能直接投进话题的接口，`feishu_message_send` 做不到）。
    合并转发一次 1-100 条，且这些消息必须来自**同一个会话**（跨群报 230069，普通消息混话题回复报 230067）；
    被飞书单独拒掉的 id 会放在 `invalid_message_ids` 里返回，**报数前先看这个字段**。
    红包/投票/语音/日程转让/系统消息/加密消息，以及合并转发里的子消息，**都不能转发**（230061/230064），
    碰上就如实转告 `hint`，别改用重发假装转发成功。
26. **在文档里画图表**：一个工具 `feishu_chart(chart_type, data_json, title, options_json, document_id, ...)`
    覆盖 21 种图（饼图 `pie`、折线 `line`、柱状 `column`、散点 `scatter`、雷达 `radar`、甘特 `gantt`……）。
    数据按字段名放进 `data_json`（`{"labels_json": [...], "values_json": [...]}`），该类型专属参数放
    `options_json`（`unit` / `percent` / `highlight` 之类）；**放错键会被拒并列出接受的键**，不会悄悄忽略——
    否则 `percent` 被丢掉就会画出一张没人要的图。**先读 `feishu-charts` 技能再选类型**：工具只管画，
    「哪种图能回答这个问题」是技能里的判断表。多面板拼一张图（`(a)(b)(c)` 共用一个图注）用
    `feishu_chart_figure`。`document_id` 留空就只出 PNG（返回 `image_path`），可拿去塞进 Word/PPT 或
    `[SEND:]` 发给用户。图注**自动按文档里已有的序号续「图 N」**，别自己写编号。返回里带 `warning`
    说明本机缺中文字体、中文会变方框，**要如实告诉用户**，别报成功了事。
27. **飞书没有专用工具的接口**：用 `feishu_api(method, uri, body_json, query_json, paths_json, prefer, ...)`
    直接打任意开放平台端点。它走的是和专用工具**同一条 `_invoke`**，所以鉴权、tenant→user 令牌降级、
    429 重试、错误码 `hint` 全都照旧。**端点清单在 `feishu-api` 技能里**（通讯录 / 考勤 / 云文档搜索 /
    审批查询 / 日历读取 / 任务 / 群 / 知识库 / 培训），先读技能再拼 `uri`。`uri` 里的 `:name` 占位符
    **原样留着**、值放 `paths_json`（让 SDK 转义）——没填的占位符会在**发请求前**被拒（`missing_path_params`），
    不会变成一个莫名的 404。只认 UAT 的端点（全组织搜人、全库搜文档）传 `prefer="user"`。
    **动手前先想有没有专用工具**：传二进制的上传端点会被直接拒并告诉你该用哪个工具
    （`use_dedicated_tool`，文件句柄塞不进 JSON 字符串）；sheet / bitable / authen 路径会带 `warning`
    点名你正在绕过谁的护栏（裸 `!A1` 静默丢数据、列名对不上静默丢值都是这么来的）。
    这里 `uri` 写错就是一次真实写入，**炸的范围比任何一个窄工具都大**。
