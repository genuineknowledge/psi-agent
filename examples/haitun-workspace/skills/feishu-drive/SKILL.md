---
name: feishu-drive
description: 飞书云盘（drive）接口表 —— 文档元数据、建文件夹、列云盘目录、复制/移动/重命名、删除云文档、异步任务状态、快捷方式、列文档评论与回复。用 feishu_api 按表调用。发评论、回复评论、下载、上传、导出仍然是专用工具。
---

# 飞书云盘接口

用 `feishu_api` 按下表调用。表里每一行都对应一个真实接口；`rules` 块是同一份知识的可执行副本，
参数不合规会在**发请求之前**被拦下来。

本域**读**的部分进了表格，**写**的部分留在专用工具里，分界线只有一条：
写评论的 body 是嵌套的 `elements` 数组，而校验只能按顶层键查、钻不进数组 ——
正文拼错了飞书照收，评论发出去是空的却返回成功。这属于静默失败那一类，所以不表格化。

三个概念先分清：

- `file_token` 是**云文档本身**的 token，从它的网址里取（`feishu.cn/docx/<token>`）。
- `file_type` 必须跟 token 的真实类型一致，写错飞书会报"找不到"而不是纠正你。
- `comment_id` 是**一条评论主楼**的 id，从评论列表里取；主楼底下的每条回复另有 `reply_id`。

## 评论

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 列整篇文档的评论（主楼） | GET | `/open-apis/drive/v1/files/:file_token/comments` | `file_type`、`is_whole`、`is_solved` |
| 列某条评论下的回复 | GET | `/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies` | `file_type` |
| 发一条整篇评论 | POST | `/open-apis/drive/v1/files/:file_token/comments` | **用 `feishu_drive_add_comment`** |
| 在评论下回复（可@人） | POST | `/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies` | **用 `feishu_drive_reply_comment`** |

`is_whole=true` 只列"整篇文档"评论，也就是没有挂在具体段落上的那些。
划词评论（挂在某段文字上的）不带这个参数才看得见，两者在飞书里是同一个接口的两种视图。

`is_solved` 不传是"全都要"，传 `false` 只看未解决的，传 `true` 只看已解决的 ——
不传和传 `false` 是不同的意思，别用它当默认值。

## 文件与文件夹

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 查文档元数据（标题/所有者/时间/链接） | POST | `/open-apis/drive/v1/metas/batch_query` | `request_docs`（1-200 项）、`with_url` |
| 列文件夹下的子项 | GET | `/open-apis/drive/v1/files` | `folder_token`、`page_size`(≤200)、`order_by`、`direction` |
| 新建文件夹 | POST | `/open-apis/drive/v1/files/create_folder` | `name`、`folder_token`（都必填） |
| 复制一份（换名字/换位置） | POST | `/open-apis/drive/v1/files/:file_token/copy` | `name`、`type`、`folder_token` |
| 移动到别的文件夹 | POST | `/open-apis/drive/v1/files/:file_token/move` | `type`、`folder_token` |
| 建快捷方式（不复制内容） | POST | `/open-apis/drive/v1/files/create_shortcut` | `parent_token`、`refer_entity` |
| 查异步任务做完了没 | GET | `/open-apis/drive/v1/files/task_check` | `task_id`（在 query 里） |
| 删除云文档 / 文件夹（进回收站） | DELETE | `/open-apis/drive/v1/files/:file_token` | `type`（必填） |
| 下载文档里的图片/附件 | GET | `/open-apis/drive/v1/medias/:file_token/download` | **用 `feishu_file_download`** |
| 下载云盘里的资源文件（PDF 等） | GET | `/open-apis/drive/v1/files/:file_token/download` | **用 `feishu_file_download(source_type="file")`** |
| 上传本地文件到云盘 | POST | `/open-apis/drive/v1/medias/upload_all` | **用 `feishu_drive_upload`** |
| 上传本地文件（files 端点） | POST | `/open-apis/drive/v1/files/upload_all` | **用 `feishu_drive_upload`** |

`metas/batch_query` 一次问 1-200 份，查不到的**不报错**，落在 `failed_list[]` 里各带一个 code：
`970002` 类型不支持、`970003` 没有元数据权限、`970005` token 和 `doc_type` 对不上或文件不存在。
只看 `metas[]` 会把这三种失败当成「没这份文件」。

列目录**只列这一层**，不递归进子文件夹。`folder_token` 留空是「我的空间」根目录，而根目录
**不分页、也不含快捷方式** —— 传了 `page_size` 也一次全给。想按修改时间排就 `order_by=EditedTime`。

复制和移动都要 `type`，且必须跟文件的真实类型一致；飞书**不支持复制文件夹**（移动可以）。
移动**文件夹**和删除文件夹一样是异步的：响应里回 `task_id`，拿它打 `task_check`，
`data.status` 是 `success` / `fail` / `process` 三种之一。移动文档没有这个字段。

一层文件夹（含根目录）最多挂 **1500** 个节点，超了报 `1062507`；建/复制/移动都是 5 QPS、
1 万次/天，`1061045` 是撞了这个限而不是参数错 —— 等一下重试就行，别改参数。

## 重命名：飞书没有统一接口

这是本域最容易凭直觉试错的地方。**没有**「改任意云文档名字」的那一个端点，三种情况三种做法：

- **电子表格**：走 `feishu-sheet` 那份接口表里的改标题端点（body 只有 `title`，传空串会变成
  「未命名表格」而不是保持原名）。护栏写在那边，这里不重复声明，免得两份 rules 抢同一个端点。
- **知识库节点**：`POST /open-apis/wiki/v2/spaces/:space_id/nodes/:node_token/update_title`，
  body `{"title": "新标题"}`。**只支持 doc / docx / shortcut 三种节点类型**。
- **独立 docx**：**没有改标题的接口**。只能 `copy` 成一份新名字的再删掉旧的 —— 那是两步操作，
  换了 token，评论和编辑历史都留在旧文件上。这个后果要先告诉用户再动手，不要当成「改个名而已」。

## 回收站：没有公开 API

删除是**可恢复**的（进回收站，不是物理删除），但「列出 / 恢复 / 清空回收站」在开放平台上
**没有对应端点**。用户要恢复只能自己去飞书客户端的回收站里操作。别去猜一个 `/trash` 路径反复试 ——
那不是权限问题，是接口不存在。

## 云盘文件搜索

已经有了，不在本表里：工具 `feishu_docs_search`，或 `feishu-api` 里那行
`POST /open-apis/suite/docs-api/search/object`（**只吃 user token**，搜到的是那个人有权限看的）。

删除是**可恢复**的，进回收站不是物理删除。但调用方必须是文件所有者，或者对父文件夹有编辑/管理权限
—— 所以删用户自己的文件要带上他的 `user_key` 以他的身份删。

删**文件夹**跟删文档不一样：飞书是异步做的，响应里回一个 `task_id`，删除并没有当场完成。
要确认结果得拿这个 task_id 去查任务状态。删文档没有这个字段。

要删的东西在**知识库（wiki）里**的话，`file_token` 不能直接用 wiki 节点的 token：
先用 `feishu_api` 打 `wiki/v2/spaces/get_node` 换出 `obj_token` / `obj_type`，再删那个。

## 导出成 pdf / docx / xlsx / csv

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 建导出任务 | POST | `/open-apis/drive/v1/export_tasks` | **用 `feishu_doc_export`** |
| 查导出任务结果 | GET | `/open-apis/drive/v1/export_tasks/:ticket` | **用 `feishu_doc_export`** |
| 下载导出的文件 | GET | `/open-apis/drive/v1/export_tasks/file/:file_token/download` | **用 `feishu_doc_export`** |

导出是**三步**：建任务拿 `ticket` → 轮询到 `job_status == 0` → 拿返回的 `file_token` 下载二进制。
轮询和写磁盘都不是「一个响应」，所以整条链是工具 `feishu_doc_export(token, file_type,
file_extension, save_path, sub_id="")`，一次调用做完三步。

格式**按源类型配对**，配错了报 `1069918`：`docx` → pdf / docx，`doc`（旧版文档，已不推荐）→ pdf / docx，
`sheet` → xlsx / csv，`bitable` → xlsx / csv。**导 csv 必须给 `sub_id`**（表格的 `sheet_id` 或
多维表格的 `table_id`），缺了报 `1069904` —— 因为一个表格里有多张工作表，csv 装不下多张。

`job_status` 不为 0 时各有含义：`1` 初始化 / `2` 处理中 / `3` 内部错误 / `107` 文档过大 /
`108` 超时 / `109` 块无权限 / `110` 无权限 / `111` 文档已删除 / `123` 文档不存在 / `6000` 图片过多。
107 和 6000 重试也没用，是文档本身的问题。

**导出结束 10 分钟后文件被删**，所以 `file_token` 不能存下来隔天下载，工具是一次性走完的。

## 为什么下载、上传、导出不在表格里

这三处是本域通用工具**表达不了**的地方：

- **下载**的产物是**磁盘上的一个文件**，不是 JSON 响应。`feishu_file_download` 从二进制响应里取字节写到
  本地路径，还管着 tenant→用户授权的两段降级（机器人看不见的文件才回退到用户身份）。
  它的 `source_type` 分三种：`media`（默认，文档里的图片/附件，走 `medias/:file_token/download`）、
  `file`（**云盘里的资源文件**如 PDF，走 `files/:file_token/download`）、`url`（审批表单里那种**直链**，
  约 12 小时失效，过期要重读审批实例换新链接）。选错了会 404 —— `files/download`
  **不含**飞书文档/表格/多维表格，那些要走上面的导出；文档内嵌的图片附件反过来只在 `medias` 里。
- **上传**的 body 里要放**真的文件句柄**，JSON 字符串表达不了。`medias/upload_all` 和 `files/upload_all`
  两个端点已经被通用工具硬拒，绕不过去。超过 20MB 要走分片上传，`feishu_drive_upload` 会直接告诉你大小。
- **导出**要轮询（第二步要重复打到状态变 0）又要写磁盘，rules 两样都写不出来。

## 往文档正文里插图片 / 附件

也不是一个请求：建一个空块 → 把文件上传**进这个块** → PATCH 绑 token，三步缺一不可（少了第三步
块会一直显示占位符）。用 `feishu_doc_append_image(document_id, image_path)` /
`feishu_doc_append_file(document_id, file_path)`，它们替你走完三步，中途失败会把留下的空块删掉。

```rules
- endpoint: POST /open-apis/drive/v1/metas/batch_query
  token: tenant_then_user
  required: [request_docs]
  fields:
    request_docs:
      min_items: 1
      max_items: 200
      on_fail: "request_docs 要 1-200 项, 每项是 {doc_token, doc_type}"
  pitfalls:
    - 字段名是 doc_token / doc_type(单数), 不是 docs_token / docs_type。
    - 查不到的不报错, 落在 failed_list[] 里: 970002 类型不支持 / 970003 无元数据权限 / 970005 token 与 doc_type 不匹配或文件不存在。
    - doc_type 只认 doc/docx/sheet/bitable/mindnote/file/wiki/folder/synced_block/slides。
    - with_url 默认 false, 要链接才传 true。

- endpoint: GET /open-apis/drive/v1/files
  token: tenant_then_user
  fields:
    page_size: {default: 100, max: 200, on_fail: "page_size 上限 200, 超了报 1064001"}
    order_by: {choices: [EditedTime, CreatedTime]}
    direction: {choices: [ASC, DESC]}
  paginate: {items: files, page_size: 100}
  pitfalls:
    - 只列这一层, 不递归子文件夹。
    - folder_token 留空是"我的空间"根目录, 而根目录不分页也不含快捷方式。
    - 每项的 type 是 doc/sheet/mindnote/bitable/file/docx/folder/shortcut;shortcut 另有 shortcut_info.target_token。
    - created_time / modified_time 是秒级时间戳字符串。

- endpoint: POST /open-apis/drive/v1/files/create_folder
  token: tenant_then_user
  required: [name, folder_token]
  fields:
    # 256 是字节数而不是字符数(中文一个字 3 字节), 而 rules 只能按字符数拦 ——
    # 所以这里按最宽松的 256 字符设界: 拦住明显过长的, 中文名超字节的仍由飞书报错。
    # 写成 pattern 而不是 max, 因为 max 用 float() 转换, 对字符串一律放过。
    name: {pattern: '^[\s\S]{1,256}$', on_fail: "文件夹名 1-256 字节(中文一个字算 3 字节)"}
  pitfalls:
    - folder_token 也是必填的, 建在根目录要显式传空串 ""。
    - 一层最多 1500 个节点, 超了报 1062507;整个云空间上限 40 万节点。
    - 1061045 是 5 QPS / 1万次每天的限流(不支持并发), 等一下重试即可, 不是参数错。
    - 1061004 是对目标文件夹没有编辑权限, 带上文件夹所有者的 user_key 重试。

- endpoint: POST /open-apis/drive/v1/files/:file_token/copy
  token: tenant_then_user
  required: [name, type, folder_token]
  fields:
    name: {pattern: '^[\s\S]{1,256}$', on_fail: "副本名 1-256 字节(中文一个字算 3 字节)"}
    type:
      choices: [file, doc, docx, sheet, bitable, mindnote, slides]
      on_fail: "type 必须跟源文件真实类型一致, 且不能是 folder(飞书不支持复制文件夹)"
  pitfalls:
    - 不支持复制文件夹;type 写 folder 会失败。
    - type 空着或跟真实类型不一致都会失败, 飞书不会替你纠正。
    - 需要对源文件有读/编辑权, 且对目标文件夹有编辑权, 否则 1061004。
    - 跨租户/跨地域 1064510、跨品牌 1064511 一律拒绝。

- endpoint: POST /open-apis/drive/v1/files/:file_token/move
  token: tenant_then_user
  required: [type, folder_token]
  fields:
    type:
      choices: [file, doc, docx, sheet, bitable, mindnote, slides, folder]
      on_fail: "type 必填且必须跟真实类型一致;移动可以是 folder(复制不行)"
  pitfalls:
    - 移动文件夹是异步的, 响应回 task_id, 拿它打 GET /open-apis/drive/v1/files/task_check;移动文档没有这个字段。
    - 1062524 是对源父文件夹没权限, 1062535 是对目标文件夹没权限 —— 两个要分开看。
    - 目标一层超 1500 个节点报 1062507。

- endpoint: GET /open-apis/drive/v1/files/task_check
  token: tenant_then_user
  required: [query.task_id]
  pitfalls:
    - task_id 在 query 里(这个端点没有占位符), 来自删文件夹或移动文件夹的响应。
    - data.status 是 success / fail / process 三种;process 表示还在做, 要再问一次。

- endpoint: POST /open-apis/drive/v1/files/create_shortcut
  token: tenant_then_user
  required: [parent_token, refer_entity]
  pitfalls:
    - refer_entity 是 {refer_token, refer_type}, refer_type 只认 file/docx/bitable/doc/sheet/mindnote/slides。
    - 快捷方式不复制内容, 删掉它不影响源文件;反过来源文件删了快捷方式就空了。

- endpoint: POST /open-apis/wiki/v2/spaces/:space_id/nodes/:node_token/update_title
  token: tenant_then_user
  required: [title]
  pitfalls:
    - 只支持 doc / docx / shortcut 三种节点类型, 别的类型改不了名。
    - 独立 docx(不在知识库里)没有改标题的接口, 只能 copy 成新名字再删旧的 —— 换了 token, 评论和历史留在旧文件上。

- endpoint: POST /open-apis/drive/v1/export_tasks
  prefer_tool: feishu_doc_export
  hard: true
  why: >
    导出是三步(建任务→轮询 job_status→下载二进制), 中间要重复问到状态变 0, 末尾产物是磁盘上的文件;
    轮询和落盘 rules 两样都表达不了。工具一次调用走完三步, 并把 job_status 的 107/108/110/111/6000 翻成人话。

- endpoint: GET /open-apis/drive/v1/export_tasks/:ticket
  prefer_tool: feishu_doc_export
  hard: true
  why: 这是导出三步里的第二步, 单独打它只会拿到一个中间状态;整条链用 feishu_doc_export。

- endpoint: GET /open-apis/drive/v1/export_tasks/file/:file_token/download
  prefer_tool: feishu_doc_export
  hard: true
  why: >
    产物是二进制而不是 JSON, 且导出结束 10 分钟后文件就被删 —— file_token 存不住,
    必须和前两步连着走。

- endpoint: GET /open-apis/drive/v1/files/:file_token/download
  prefer_tool: feishu_file_download
  hard: true
  why: >
    产物是磁盘上的文件而不是 JSON 响应。用 feishu_file_download 并把 source_type 设成 "file" ——
    这个端点只管云盘里的资源文件(如 PDF), 不含飞书文档/表格/多维表格(那些走导出),
    文档内嵌的图片附件反过来只在 medias 那条路上。

- endpoint: GET /open-apis/drive/v1/files/:file_token/comments
  token: tenant_then_user
  required: [file_type]
  fields:
    file_type: {choices: [docx, doc, sheet, bitable, file], on_fail: "file_type 必须跟 file_token 的真实类型一致"}
    is_whole: {choices: ['true', 'false']}
    is_solved: {choices: ['true', 'false']}
    page_size: {default: 50, max: 100}
  paginate: {items: items, page_size: 50}
  pitfalls:
    - is_whole=true 只列整篇评论; 划词评论(挂在某段文字上)不带这个参数才看得见
    - is_solved 不传是"全都要", 传 false 只看未解决 —— 不传和传 false 不是一回事

- endpoint: POST /open-apis/drive/v1/files/:file_token/comments
  prefer_tool: feishu_drive_add_comment
  hard: true
  why: >
    body 是 reply_list.replies[].content.elements[] 三层嵌套, 校验按顶层键查、钻不进数组,
    正文拼错飞书照收 —— 评论发出去是空的却返回成功。工具替你拼这层结构。

- endpoint: GET /open-apis/drive/v1/files/:file_token/comments/:comment_id/replies
  token: tenant_then_user
  required: [file_type]
  fields:
    file_type: {choices: [docx, doc, sheet, bitable, file]}
    page_size: {default: 50, max: 100}
  paginate: {items: items, page_size: 50}
  pitfalls:
    - comment_id 是主楼 id(从评论列表取); 主楼下每条回复另有 reply_id, 两者不能互换

- endpoint: POST /open-apis/drive/v1/files/:file_token/comments/:comment_id/replies
  prefer_tool: feishu_drive_reply_comment
  hard: true
  why: >
    同上的 elements 嵌套, 而且@人要在 elements 最前面插一个 person 节点;
    顺序错了@不生效但依然返回成功。

- endpoint: DELETE /open-apis/drive/v1/files/:file_token
  token: tenant_then_user
  required: [type]
  fields:
    type:
      choices: [file, docx, doc, sheet, bitable, mindnote, slides, folder, shortcut]
      on_fail: "type 必填且必须是这 9 种之一"
  pitfalls:
    - 进回收站, 可恢复; 但调用方必须是所有者或对父文件夹有编辑权限, 删用户的文件要带他的 user_key
    - 删文件夹是异步的, 响应回一个 task_id, 删除没有当场完成; 删文档没有这个字段
    - wiki 里的文档不能直接用节点 token, 先 `feishu_api` 打 `wiki/v2/spaces/get_node` 换出 obj_token 再删

- endpoint: POST /open-apis/drive/v1/medias/upload_all
  prefer_tool: feishu_drive_upload
  hard: true
  why: body 里要真的文件句柄, JSON 字符串表达不了; 硬发出去会拿到 400 boundary not found。

- endpoint: POST /open-apis/drive/v1/files/upload_all
  prefer_tool: feishu_drive_upload
  hard: true
  why: 同 medias/upload_all, body 要真文件句柄。

- endpoint: GET /open-apis/drive/v1/medias/:file_token/download
  prefer_tool: feishu_file_download
  hard: true
  why: >
    产物是磁盘上的文件而不是 JSON 响应, 通用工具表达不了落盘;
    工具还管着 tenant→用户授权的两段降级, 以及审批直链(约 12 小时失效)那条 is_url 分支。
```
