---
name: feishu-permission
description: 飞书云文档权限（drive permissions）接口表 —— 给文档/表格/多维表格/wiki 加人、列出协作者、撤销权限，做「全员可查」和「不同人不同权限」。用 feishu_api 按表调用。
---

# 飞书云文档权限接口

用 `feishu_api` 按下表调用。表里每一行都对应一个真实接口；`rules` 块是同一份知识的可执行副本，
参数不合规会在**发请求之前**被拦下来。

本域管的是**单个文件的访问控制**：一份 docx / sheet / bitable / wiki / 文件夹，谁能看、谁能编辑。
要做「一张多维表格、不同角色看到不同行和不同字段」不在这里 —— 那是多维表格的角色（role），
见 `feishu-bitable`。

## 三个接口

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 给人/群/部门加权限 | POST | `/open-apis/drive/v1/permissions/:token/members` | query `type`、`need_notification`；body `member_type`、`member_id`、`perm`、`type` |
| 列出所有显式协作者 | GET | `/open-apis/drive/v1/permissions/:token/members` | query `type` |
| 撤销某人的权限 | DELETE | `/open-apis/drive/v1/permissions/:token/members/:member_id` | query `type`、`member_type`；body `type` |

## 三个「type」是三件不同的事，混了必错

这是本域唯一真正容易错的地方，而且飞书三处都叫 `type`：

- **query 里的 `type`** = **文件是什么**：`docx` / `doc` / `sheet` / `bitable` / `file` / `wiki` / `folder`。
- **body 里的 `member_type`** = **id 是什么形态**：`openid` / `userid` / `unionid` / `openchat` /
  `opendepartmentid` / `email` / `groupid` / `wikispaceid`。注意是 `openid` 而**不是** `open_id`，
  这一个下划线之差是本域最常见的报错。
- **body 里的 `type`** = **成员是哪一类**：`user` / `chat` / `department` / `group`。

`member_type` 和 body 的 `type` 必须**互相对得上**：`opendepartmentid` 要配 `department`，
`openchat` 要配 `chat`，`groupid` 要配 `group`。给部门发了 `member_type=opendepartmentid` 却把
`type` 留成默认的 `user`，飞书不会告诉你哪个字段错了。

删除时 `member_type` 走的是 **query**（加人时它在 body 里），这条也别照抄。

## 两个常见目标怎么写

**「全员可查」** —— 加根部门、只给 view：

```
POST /open-apis/drive/v1/permissions/:token/members
paths:  {"token": "<文件 token>"}
query:  {"type": "docx"}
body:   {"member_type": "opendepartmentid", "member_id": "<根部门 open_department_id>",
         "perm": "view", "type": "department"}
```

**给某人编辑权** —— 用他的 open_id：

```
body:   {"member_type": "openid", "member_id": "ou_xxx", "perm": "edit", "type": "user"}
```

`perm` 只有三档：`view` / `edit` / `full_access`。`full_access` 是所有者级别（能改权限、能删文件），
给出去之前先跟用户确认清楚，这不是「编辑权限的加强版」。

## token 从哪来、以谁的身份调

`:token` 是文件自己的 token，从它的网址里取。**wiki 节点要用 `obj_token`**，不是网址里的
node token —— 先 `feishu_api` 打 `wiki/v2/spaces/get_node` 拿 `obj_token`。

调用身份：机器人往往**不是**这份文件的协作者，那它就无权改别人文件的权限。所以写操作
（加/删）默认按文件所有者的身份走，要带上 `user_key`，并用 `prefer=user`；读列表用机器人
token 就够（它能看到的文件才列得出）。`need_notification=true` 会给被加的人发一条通知。

```rules
- endpoint: POST /open-apis/drive/v1/permissions/:token/members
  token: tenant_then_user
  required: [query.type, body.member_type, body.member_id, body.perm, body.type]
  fields:
    query.type: {choices: [docx, doc, sheet, bitable, file, wiki, folder],
                 on_fail: "query 的 type 是文件类型, 只认 docx/doc/sheet/bitable/file/wiki/folder"}
    body.type: {choices: [user, chat, department, group],
                on_fail: "body 的 type 是成员类别, 只认 user/chat/department/group"}
    body.perm: {default: view, choices: [view, edit, full_access]}
    body.member_type: {default: openid,
                       choices: [openid, userid, unionid, openchat, opendepartmentid, email, groupid, wikispaceid],
                       on_fail: "member_type 是 id 形态, 注意是 openid 而不是 open_id"}
    query.need_notification: {choices: ["true", "false"]}
  pitfalls:
    - 三个 type 是三件事:query 的 type 是文件类型, body 的 member_type 是 id 形态, body 的 type 是成员类别(user/chat/department/group)。
    - member_type 要和 body 的 type 对得上:opendepartmentid 配 department, openchat 配 chat, groupid 配 group。
    - 全员可查 = 加根部门 + perm=view + type=department。
    - full_access 是所有者级别(能改权限、能删文件), 不是 edit 的加强版。
    - wiki 节点要用 obj_token, 不是网址里的 node token。

- endpoint: GET /open-apis/drive/v1/permissions/:token/members
  token: tenant_then_user
  required: [query.type]
  fields:
    query.type: {choices: [docx, doc, sheet, bitable, file, wiki, folder]}
  pitfalls:
    - 只列出"显式"授过权的成员;靠链接分享或继承文件夹权限看到的人不在这里。

- endpoint: DELETE /open-apis/drive/v1/permissions/:token/members/:member_id
  token: tenant_then_user
  required: [query.type, query.member_type, body.type]
  fields:
    query.type: {choices: [docx, doc, sheet, bitable, file, wiki, folder]}
    body.type: {choices: [user, chat, department, group]}
    query.member_type: {default: openid,
                        choices: [openid, userid, unionid, openchat, opendepartmentid, email, groupid, wikispaceid]}
  pitfalls:
    - 删除时 member_type 在 query 里, 加人时它在 body 里, 别照抄。
    - 'body 仍要带 {"type": "<成员类别>"}, 和加人时的 body type 同义。'
    - 撤销的是显式授权;文件夹继承来的权限删不掉, 要去父文件夹上改。
```

授权与权限：需要 `drive:drive` scope（多维表格是 `bitable:app`、wiki 是 `wiki:wiki`）。
被操作的文件如果不是机器人创建的，几乎一定要走用户身份 —— 报 403 / 无权限时先确认
`user_key` 传了没有，而不是去改 `member_type`。
