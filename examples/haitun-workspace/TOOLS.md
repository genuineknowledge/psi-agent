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

### 飞书全局文档搜索（需用户授权，先问再授权）

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
