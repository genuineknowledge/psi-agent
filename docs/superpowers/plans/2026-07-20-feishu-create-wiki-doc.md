# 计划：给 haitun agent 加「创建飞书文档 / 知识库(wiki)文档」能力

## 背景 / 缺口

当前 `tools/feishu_*.py` 里的飞书工具**只有读取类**，没有任何「创建文档」接口：

- `feishu_doc_read` — 读 docx/doc/sheet 正文
- `feishu_wiki_get_node` — 解析 wiki 节点 token → 底层 obj_token（只读）
- `feishu_docs_search` — 搜索云文档

因此 agent 目前做不到直接生成飞书文档。本计划补齐「创建 + 写正文」的写能力。

## 已查证的飞书官方 API

1. 建 docx 文档：`POST /open-apis/docx/v1/documents`，body `{title, folder_token?}`，返回 `data.document.document_id`。
2. 建 wiki 节点：`POST /open-apis/wiki/v2/spaces/:space_id/nodes`，body `{obj_type:"docx", node_type:"origin", parent_node_token?, title?}`，返回 `data.node`（`node_token` + `obj_token`）。注意 `doc` 已废弃(131010)，必须用 `docx`。
3. 列知识空间：`GET /open-apis/wiki/v2/spaces`，query `page_size`(≤50)/`page_token`，返回 `data.items[]`（`space_id`, `name`, `has_more`, `page_token`）。
4. 写正文：`POST /open-apis/docx/v1/documents/:document_id/blocks/:block_id/children`，根块 `block_id` = `document_id`，body `{children:[...]}`（1–50 个）。块类型：`block_type=2` 正文段落(`text`)、`3` 一级标题(`heading1`)、`4` 二级标题(`heading2`)…每块 `elements:[{text_run:{content}}]`。

## 接入方式（沿用现有惯例）

- 业务逻辑集中到 `tools/_feishu_impl.py`：手搭 `BaseRequest`（`http_method`/`uri`/`paths`/`add_query`/`body`/`token_types={TENANT,USER}`），经 `_invoke` 走 SDK `arequest`。
- 每个工具在 `tools/feishu_*.py` 里以 `async def feishu_*(...) -> str` 暴露，`return _f.dumps_result(await _f.xxx_impl(...))`，函数被自动发现为工具。
- `AGENTS.md` 工具表格补登记行。
- `tests/test_feishu.py` 用 `_CapturedInvoke` 范式补单测（校验 uri/method/body，不打真实网络）。
- 零新依赖，不动 pyproject/nuitka/pyinstaller。

## 具体改动

### 1. `tools/_feishu_impl.py` — 新增 impl 函数

- `_build_docx_create_request(title, folder_token)` + `create_docx_impl(title, folder_token)` → 返回 `{ok, document_id, title, url}`（url 由 document_id 拼 `.../docx/<id>`）。
- `_build_wiki_node_create_request(space_id, obj_type, node_type, parent_node_token, title)` + `create_wiki_node_impl(space_id, title, obj_type="docx", parent_node_token="")` → 返回 `{ok, node_token, obj_token, obj_type, space_id, url}`。
- `_build_list_spaces_request(page_size, page_token)` + `list_wiki_spaces_impl(page_size, page_token)` → 返回 `{ok, spaces:[{space_id,name}], page_token, has_more}`。
- `_build_blocks_append_request(document_id, children)` + `append_doc_content_impl(document_id, content, ...)` → 把纯文本/Markdown 轻量映射成 children 块（`#`→heading1、`##`→heading2、其余→text），分批 ≤50，返回 `{ok, added}`。

### 2. `tools/feishu_doc.py` — 新增创建/写入工具（与现有 `feishu_doc_read` 同文件）

- `feishu_doc_create(title, folder_token="")` — 建独立 docx 云文档。
- `feishu_doc_append_content(document_id, content)` — 往 docx 追加正文（标题/段落）。

### 3. `tools/feishu_wiki.py` — 新增 wiki 创建工具（与现有 `feishu_wiki_get_node` 同文件）

- `feishu_wiki_list_spaces(page_size=20, page_token="")` — 列可访问知识库空间拿 `space_id`。
- `feishu_wiki_create_doc(space_id, title, parent_node_token="")` — 在知识库建一篇 docx 文档节点，返回 node_token + obj_token(document_id) + url；随后可用 `feishu_doc_append_content(obj_token, ...)` 写正文，形成完整闭环。

### 4. `AGENTS.md`

在 `feishu_doc` / 新增 `feishu_wiki` 行补上创建工具说明，并点明「建 wiki 文档 = list_spaces → wiki_create_doc → doc_append_content」的推荐用法与所需权限（docx:document / wiki 编辑权限）。

### 5. `tests/test_feishu.py`

补单测：
- create_docx 构造正确 uri/method/body、解析 document_id。
- wiki_create_node 构造 `space_id` path + `obj_type=docx`/`node_type=origin` body。
- list_spaces 分页 query。
- append_content 的 Markdown→block 映射（heading/段落、批次切分）。
- 各新工具是 async + 有 docstring（沿用现有 `*_are_async_with_docstrings` 断言）。

## 验证

- `cd` 进 worktree 根，跑 `pytest tests/test_feishu.py --no-cov -o addopts=""`（记忆：本仓 pytest 必须禁 cov 否则超时）。
- `ruff check` + `ruff format --check`（记忆：CI 两个都跑）。
- 无凭证下不打真实飞书网络（全 mock `_invoke`/`_get_client`）。

## 交付

- 独立 worktree `C:\Users\12815\psi-agent-feishu-tools2`（分支 `add-feishu-tools2`）。
- 测试 + ruff 通过后 commit 并 push 到 `origin/add-feishu-tools2`（记忆：功能分支做完即 push，不 merge 进 main）。

## 不做

- 不做文档删除/移动（本次只补「创建 + 写正文」缺口）。
- 不做复杂块类型（表格/图片/代码块）——首版只覆盖标题+段落，够生成知识库文档正文。
