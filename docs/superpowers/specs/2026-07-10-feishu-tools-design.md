# Feishu 工具集设计（haitun workspace）

日期：2026-07-10
分支：`add-feishu-tools-work`（基于 origin/add-feishu-tools）
状态：已批准，待实现

## 背景

给 haitun agent 增加飞书文档读取与云文档评论能力，共 5 个工具：

| 工具 | 优先级 | 说明 |
|---|---|---|
| `feishu_doc_read` | P1 | 读取飞书文档全文（Docx / Doc / Sheet），按 file_type + token |
| `feishu_drive_add_comment` | P1 | 在飞书文档/文件上加一条全文级评论 |
| `feishu_drive_list_comments` | P1 | 列出文件的全文评论，按时间倒序 |
| `feishu_drive_list_comment_replies` | P1 | 列出某条评论线程的回复 |
| `feishu_drive_reply_comment` | P1 | 在评论线程下发回复，可选 @ 提及 |

### 调研结论（关键前提）

- `add-feishu-tools` 分支上**没有**这些工具代码，只有既有的 feishu **channel**（收发消息，
  非文档工具）。5 个工具需从零实现。
- 现有 feishu channel 用 **`lark_channel`（lark-channel-sdk，已在 pyproject 依赖）**。
  该 SDK 提供 `Client.arequest`（原生 async）+ 鉴权（`PSI_FEISHU_APP_ID/SECRET`），
  且 `api/drive/comment.py` 已有现成的 comment builder。
- **零新增依赖**：drive 评论用 SDK 现成 builder；doc 读取和 create-reply 端点 SDK 未预置，
  但可像 `comment.py` 那样手搭 `BaseRequest`（设 uri/paths/query/body）走同一 client + 鉴权。
  故 **不改 pyproject / nuitka.yml / pyinstaller.yml**。

## 架构

遵循现有 `fetch.py`（薄壳）+ `_fetch_impl.py`（实现）分层。**不用 MCP 桥接**（那是 browser
因 Playwright 是 Node server 才需要；feishu 是纯 Python SDK）。

- **`tools/feishu_doc.py`** — 薄壳，1 个工具 `feishu_doc_read`。
- **`tools/feishu_drive.py`** — 薄壳，4 个 drive 评论工具。
- **`tools/_feishu_impl.py`** — 共享实现层：
  - 懒加载 `lark_channel`，module 级缓存一个 authenticated `Client`
    （首次调用时经 `Client.builder().app_id().app_secret().build()` 构建，读 env）。
  - `async _invoke(request: BaseRequest) -> dict`：统一执行 `client.arequest`，
    归一化返回 `{ok, code, msg, data}`。
  - 手搭 SDK 未预置的 BaseRequest：docx/doc/sheet raw-content、create-reply。

## 工具签名与语义

所有函数 `async def`，返回 JSON 字符串，参数只用 str/int/bool（避免 dict 参数被
ToolRegistry 跳过，browser 那次踩过）。

| 工具 | 签名 | 底层端点 |
|---|---|---|
| `feishu_doc_read` | `(file_type: str, token: str, max_chars: int = 20000)` | docx→`GET /open-apis/docx/v1/documents/{token}/raw_content`；doc→`GET /open-apis/doc/v2/{token}/raw_content`；sheet→`GET /open-apis/sheets/v3/spreadsheets/{token}/sheets/query` 列 sheet + `GET /open-apis/sheets/v2/spreadsheets/{token}/values/{range}` 取值拼文本 |
| `feishu_drive_add_comment` | `(file_token: str, file_type: str, content: str)` | `build_comment_create_request`（SDK 现成） |
| `feishu_drive_list_comments` | `(file_token: str, file_type: str, page_size: int = 50, page_token: str = "")` | `build_comment_list_request`，`is_whole=true`，时间倒序 |
| `feishu_drive_list_comment_replies` | `(file_token: str, file_type: str, comment_id: str, page_size: int = 50, page_token: str = "")` | `build_comment_reply_list_request`（SDK 现成） |
| `feishu_drive_reply_comment` | `(file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str = "")` | 手搭 `POST /open-apis/drive/v1/files/{file_token}/comments/{comment_id}/replies`；at_user_id 非空时在 rich-text elements 加一个 person mention |

要点：
- `file_type` 每个 drive 工具显式收（飞书评论 API 必带），不猜。
- reply 的 content 是 rich-text elements 数组，复用 comment.py `_reply_content` 结构；
  `at_user_id` 给了就加 `{"type":"person","person":{"user_id":...}}`。
- list 工具透传 `page_size`/`page_token`，返回带 `has_more` + `page_token`，不自动全量抓取。
- `max_chars` 保护上下文，同 fetch。

## 错误处理与返回结构

- 统一返回 JSON：`{"ok": true, ...}` 或 `{"ok": false, "message": "..."}`（同 `_fetch_impl._error`）。
- **鉴权缺失**：app_id/secret 未配 → `ok=false` + "Feishu app not configured. Set
  PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET."，不抛异常、不影响其它工具加载。
- **飞书 API 错误**：`BaseResponse.code != 0` → 原样带回飞书 `code` + `msg`
  （如权限不足 99991672、token 无效），让 agent 看到真实原因。
- **懒加载失败**：`import lark_channel` 失败也 `ok=false` 而非 crash。
- **file_type 校验**：doc_read 只接受 docx/doc/sheet，其它值 `ok=false` 提示支持类型。
- **截断**：doc_read 超 `max_chars` 截断并标 `truncated=true`。

## 依赖与打包

- **零新增依赖**：`lark-channel-sdk>=1.1.0` 已在 pyproject。**不改 pyproject /
  nuitka.yml / pyinstaller.yml**。手搭端点走 SDK 已有的 `Client.arequest` + `BaseRequest`。

## 测试

新增 `tests/test_feishu.py`，仿现有 tool 测试模式，**不打真实飞书 API**：
- mock `_feishu_impl._invoke`（或 `client.arequest`），验证每个工具正确组装 BaseRequest
  （method/uri/paths/query/body）、正确解析响应、鉴权缺失返回 `ok=false`。
- 重点覆盖：doc_read 三种 file_type 端点分派、reply_comment 的 @ mention 拼接、
  list 工具分页参数透传、飞书 error code 透传。
- push 前：`ruff check` + `ruff format --check` + `ty check` + pytest。

## 非目标（YAGNI）

- 不做评论的删除/解决（solve）/更新，不做文档写入/编辑，不做 bitable/wiki。
- 不做自动全量翻页抓取（暴露 page_token 让 agent 决定）。
- 不改动既有 feishu channel 代码。
