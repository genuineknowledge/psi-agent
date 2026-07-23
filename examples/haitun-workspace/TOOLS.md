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

### 飞书群聊上下文

收到飞书群聊消息时，消息开头会带一段 `<feishu_context>` 元数据（chat_id / chat_type /
message_id / sender_open_id）。需要群里之前的上下文时：

- `feishu_message_list(container_id=<chat_id>, container_id_type="chat")` 拉取本群历史消息
- 消息里提到的飞书文档链接：从 URL 取 file_type + token，用 `feishu_doc_read` 读正文
- 群里分享的附件/图片：用 `feishu_file_download` 下载后再处理

### 飞书全局文档搜索（需用户授权，先问再授权，按用户隔离）

`feishu_docs_search`（按关键词全库搜文档）需要 user_access_token（UAT）——它代表
「当前用户能看到的文档」，机器人自己的凭证给不了。规则：

1. **不要在用户没要求时擅自发起授权。** 只有当用户的诉求确实需要全库搜索（如「帮我在
   公司知识库里找报销 SOP」而你手上没有文档链接）时才走这条路。
2. **优先免授权路径**：如果用户已给出文档/wiki 链接，直接 `feishu_doc_read` /
   `feishu_wiki_get_node` 读即可，**无需** UAT，不要多此一举去授权。
3. **需要搜索时，先征得同意再授权**：先一句话问用户「这需要用你的飞书身份做一次授权
   （只读文档/云盘），是否继续？」得到同意后再调 `feishu_auth_start`，把返回的
   `authorize_url` 发给用户，让其批准后回传地址栏里的 `code`（或整条跳转 URL），
   再调 `feishu_auth_complete(code)`。授权一次后 UAT 会缓存并自动续期。
4. **识别「未授权」信号**：`feishu_docs_search` 未授权时返回 `need_auth=True` /
   `"Not authorized..."`。**收到这个信号时，不要反复重试搜索**，而是按第 3 步询问用户是否授权。
5. **按用户隔离（多人场景）**：调 `feishu_auth_start` / `feishu_auth_complete` /
   `feishu_docs_search` / `feishu_wiki_create_space` 时，把 `<feishu_context>` 里的
   `sender_open_id` 作为 `user_key` 传入，使每个人各自授权、各自操作，互不覆盖。同一用户
   多处要传相同的 `user_key`。单聊/单用户场景可留空（共用 `default` 槽）。
6. **创建知识库也需授权**：`feishu_wiki_create_space(name, description, open_sharing, user_key)`
   只吃 UAT（新库归授权用户所有），未授权时返回 `need_auth=True`，按第 3 步先征得同意再授权。
7. **往用户自己的库/文档里写，要带 user_key**：如果知识库是用户用自己身份建的（机器人不是协作者，
   而且机器人应用通常搜不到、加不进协作者），那么在里面建文档、写正文时也要以该用户身份操作——
   给 `feishu_wiki_create_doc` / `feishu_doc_create` / `feishu_doc_append_content`
   （以及 bitable 写入、drive 评论、task 创建等写入类工具）传相同的 `user_key`。
   一条「建库→建文档→写正文」链路要**全程用同一个 user_key**，否则机器人身份没权限。
   写入类工具未授权时同样返回 `need_auth=True`。
8. **建带内容的 wiki 文档，优先用一步到位工具**：要在知识库里新建一篇**有正文**的文档，
   优先用 `feishu_wiki_create_doc_with_content(space_id, title, content, parent_node_token, user_key)`——
   它一次调用完成「建节点 + 写正文」，避免分两步（先 `feishu_wiki_create_doc` 再
   `feishu_doc_append_content`）时因第二步失败/漏调而留下**空文档**。若正文写入失败，它会连
   `node_token`/`obj_token` 一并回报，可用相同 `user_key` 调 `feishu_doc_append_content` 补写。
9. **删除文档/文件**：用 `feishu_drive_delete_file(file_token, file_type, user_key)`——删除进
   **回收站可恢复**。file_type 是 docx/doc/sheet/bitable/mindnote/slides/file/folder/shortcut。
   删用户自己的文件/库里的东西要带 `user_key`（须是所有者或对父文件夹有编辑权）。
   删**知识库(wiki)里的文档**：飞书没有独立删 wiki 节点的接口——先 `feishu_wiki_get_node`
   取 `obj_token`+`obj_type`，再 `feishu_drive_delete_file(file_token=obj_token, file_type=obj_type, user_key=...)`。
   删除是不可轻率的操作，动手前先跟用户确认清楚删的是哪一个。
9. **访问/浏览知识库要带 user_key（读也一样）**：`feishu_wiki_list_spaces` / `feishu_wiki_list_nodes`
   / `feishu_wiki_get_node` 默认只用机器人 token，而机器人通常不是任何知识库的成员，会返回**空**。
   要列出/浏览用户能看到的知识库，必须把 `<feishu_context>` 的 `sender_open_id` 作为 `user_key` 传入：
   `feishu_wiki_list_spaces(user_key=...)` 列库 → `feishu_wiki_list_nodes(space_id, user_key=...)` 列文档
   → `feishu_wiki_get_node(token, user_key=...)` 拿 obj_token → `feishu_doc_read` 读正文。
   **不要因为 list_spaces 返回空就说"企业没有知识库"或让用户手动加机器人为协作者**——先带 user_key 重试。
   未授权时按第 3 步先征得同意再授权。
10. **读知识库里的 PDF/附件（下载也要带 user_key）**：飞书文档 API 只能直接读 docx/doc/sheet；
    PDF、图片等要先下载再解析。`feishu_file_download(source, save_path, user_key=...)` 默认用机器人
    身份，下用户自己/知识库里的文件会因无权限失败——**必须带 `user_key`**（发送者 open_id）以用户
    身份下载。流程：`feishu_wiki_get_node(token, user_key)` 拿 `obj_token` → `feishu_file_download`
    (带 user_key) 存到本地 → 用 `ocr-and-documents` 技能（PyMuPDF）抽文本。**下载失败不要直接让用户
    手动复制粘贴，先确认带了 user_key**；未授权时按第 3 步先征得同意再授权。
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
