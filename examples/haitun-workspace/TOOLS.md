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

### 飞书权限总原则：优先用机器人（tenant）权限，必须时才让用户授权

飞书工具**默认已经是「tenant 优先」**——绝大多数工具会先用机器人自己的 app 权限去做，
只有当机器人权限确实不够时才回退到用户身份。所以你**不用再纠结该不该传 `user_key`**：

- **无脑把 `<feishu_context>` 里的 `sender_open_id` 当作 `user_key` 传给飞书工具**（尤其是
  写入/创建/删除/知识库/下载类）。它只是一个「后备身份」——机器人能做的事就用机器人做（内容归
  机器人所有），机器人做不了时工具才自动改用这个用户的授权身份重试。单聊/单用户场景也可留空。
- **只有工具明确返回 `need_auth=True` 时，才需要引导用户授权**。此前不要主动发起授权，
  也不要预设「机器人没权限」。授权一次后凭证会缓存并自动续期，**之后同类操作不会再要求授权**。
- 收到 `need_auth=True` 时**不要反复重试**，而是按下面「引导用户授权」的分步提示带用户走一遍。

哪些操作**必须**用户授权（机器人 tenant 权限天生做不了，会直接 `need_auth`）：
- `feishu_docs_search`（全库搜「当前用户能看到的文档」）；
- `feishu_wiki_create_space`（新建知识库，新库归授权用户所有）。
其余读/写/删除/下载类都是 tenant 优先、失败才回退，通常无需授权。

### 引导用户授权（提示要明显，一次授权后不再问）

当工具返回 `need_auth=True`，按工具返回的 `message` 分步引导用户（把 `sender_open_id` 作为
`user_key` 贯穿以下三步，多人场景各自授权、互不覆盖）：

1. 调 `feishu_auth_start(user_key=<sender_open_id>)`，把返回的 `authorize_url` **原样发给用户**，
   让其打开并点「同意授权」；
2. **明确告诉用户**：同意后浏览器会跳转到一个新网址，让他**看浏览器地址栏**——地址形如
   `http://localhost/?code=xxxxxxxx&state=...`，把 `code=` 后面、`&` 之前那一串复制回来
   （复制整段网址也行，工具会自动提取）；
3. 拿到 code 后调 `feishu_auth_complete(code, user_key=<sender_open_id>)`。成功后凭证缓存并
   自动续期，之后同类操作不会再让用户授权。

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
12. **卡点找人（判定归属 + 给联系方式）**：员工私聊说"工作上卡在某个点了"，按 [`feishu-blocker-routing`]
    技能给他指路。先读一张**职责归属多维表格**（业务领域/职责 → 负责人 open_id）
    `feishu_bitable_list_records(app_token, table_id)` 把卡点匹配到负责人，再用
    `feishu_user_get(user_ids=<负责人 open_id>)` 取其**联系方式**（`mobile`/`email`/`enterprise_email`/
    `job_title`），回员工"①这归谁负责 ②去找谁 ③怎么联系"。台账里存的是姓名不是 open_id 时，先
    `feishu_department_members(recursive=True)` 或 `feishu_chat_find_member` 按名反查 open_id。
    **联系方式只在私聊回给来问的本人，不群发**；`mobile`/`email` 读到空多是缺
    `contact:user.phone:readonly`/`contact:user.email:readonly` 或通讯录权限范围没覆盖，**如实说明**并
    退回到"在飞书里 @他"，不编号码；台账查不到归属就如实说查不到，别硬安负责人。
13. **代人带话/转达（署名，不发裸气泡）**：当用户让你替他给别人捎句话（"帮我给张三带句话：…"
    "转告李四…"）时，用 `feishu_message_send(receive_id=<对方>, text=<原话>, on_behalf_of=<sender_open_id>)`——
    传 `<feishu_context>` 的 `sender_open_id` 作为 `on_behalf_of`，收件人会看到「张三给你发了一条消息：「…」」
    这样清楚是谁托带的，**而不是机器人自己冒出来一句裸消息**。姓名由 open_id 自动解析，解析不到才回退
    成 open_id 本身。**只有代他人转达时才传 `on_behalf_of`**；机器人自己发的通知/看板/播报不要传（保持无前缀）。
