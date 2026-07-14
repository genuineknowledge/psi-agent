# 飞书按名字搜索文档工具 —— 实现计划

日期：2026-07-14
分支：`add-feishu-tools1`（当前工作树 `psi-agent-feishu-check-todo`）
状态：待批准

## 目标

让用户在与 agent 对话时说"帮我搜一下叫 XX 的飞书文档"，agent 按关键词搜索用户能访问的云文档，返回标题 + token + 类型，再配合已有 `feishu_doc_read` 读全文。

## 核心约束（已查证，决定整个设计）

- 飞书文档搜索接口 `POST /open-apis/suite/docs-api/search/object` **只接受 user_access_token（UAT，用户身份），不接受 tenant_access_token（机器人身份）**。我们现有全部飞书工具用的都是 tenant token，所以搜索**必须**引入 UAT。
- UAT 代表"某个真人"，搜出来的是**那个授权用户能看到的文档**，不是全公司全局。
- UAT 需用户授权获取、约 2 小时过期、靠 refresh_token（需 `offline_access` scope）刷新。

## 关键发现：SDK 自带 OAuth 设备流，无需 gateway 回调

`lark_channel` SDK 内置完整 UAT OAuth **设备流（device flow）**，直接用，**不需要**授权码回调那套重活：

- `channel/auth/device_flow.py::DeviceFlowClient(app_id, app_secret)`
  - `start(scopes) -> DeviceFlowInit`：POST `/open-apis/authen/v2/oauth/device_authorization`，返回 `verification_uri`（+ `verification_uri_complete`）、`user_code`、`device_code`、`interval`、`expires_in`。
  - `poll(device_code, interval, timeout_seconds) -> UAT`：POST `/open-apis/authen/v2/oauth/token`（grant_type=device_code）轮询直到用户完成授权。
  - `refresh(refresh_token) -> UAT`：同端点，grant_type=refresh_token。
- `channel/auth/token_store.py::FileTokenStore`：`get(user_id)/set(user_id, uat)` 持久化 UAT（含 refresh_token、expires_at）。
- `channel/types.py::UAT`：`access_token / refresh_token / expires_at / open_id / scopes`。
- `channel/auth/uat_runner.py::uat_needs_refresh(uat, slack_seconds=300)` 判断是否该刷新。

**设备流优点（相比授权码回调）**：不用注册 redirect_uri、不用给 gateway 加回调路由、不用固定端口。用户只需打开一个飞书链接、输入 user_code、点同意。这契合"本地 agent + 命令行/UI"场景。

调 UAT-required 接口：SDK 无 `docs-api/search` 的生成模型，故手搭 `BaseRequest`（`token_types={AccessTokenType.USER}`），client 用 `.enable_set_token(True)` 构建，调用时传 `RequestOption.builder().user_access_token(uat).build()`，transport 自动注入 `Authorization: Bearer <uat>`（参考 `channel/bot_identity.py::_raw_request`）。

## 架构（沿用现有分层，全部落在 haitun-workspace 内，src/ 零改动）

### A. `tools/_feishu_impl.py` 新增 UAT 管理 + 搜索

- **UAT 存储**：用 SDK `FileTokenStore`，落到 `<workspace>/.psi/feishu/uat.json`（沿用项目 `.psi/` 存储惯例，已被 .gitignore 忽略——`.psi/` 在 workspace .gitignore 里）。**flag：refresh_token 明文存盘**，需在文档/返回里提示。
- **UAT 获取/刷新 helper**：
  - `_get_uat(user_key)`：读 store → 若不存在返回 None（提示需授权）→ 若快过期用 `refresh()` 刷新并回存。
  - 独立的授权入口（见 B 的 `feishu_auth_*` 工具）。
- **搜索 impl** `search_docs_impl(search_key, count, offset, docs_types, user_key)`：
  - 取 UAT（无则返回 `ok=false` + "需先授权，调用 feishu_auth_start"）。
  - 手搭 `BaseRequest`：POST `/open-apis/suite/docs-api/search/object`，body `{search_key, count, offset, docs_types?}`，`token_types={USER}`。
  - 用带 `enable_set_token(True)` 的 client + `RequestOption(user_access_token=uat)` 调用。
  - 解析 `docs_entities[]` → `[{title, token: docs_token, obj_type: docs_type, owner_id}]` + `has_more` + `total`。

### B. 授权工具（设备流，两步交互）

设备流天然是"两步"，契合工具模型（tool 快速返回，不长阻塞；参考 `clarify.py` 的 return-and-wait）：

- **`tools/feishu_auth.py`**：
  - `feishu_auth_start(scopes="docs:doc:readonly search:docs:read offline_access")`：调 `DeviceFlowClient.start()`，返回给用户 `verification_uri_complete`（或 uri + user_code）让其打开授权，并把 `device_code`/`interval`/`expires` 暂存到 `.psi/feishu/pending_auth.json`。
  - `feishu_auth_complete()`：读回 pending，调 `poll(device_code, timeout_seconds=~60)` 等用户授权完成，拿到 UAT 存进 FileTokenStore，返回成功 + 授权到的 open_id。（bounded poll，不无限阻塞；超时提示"授权未完成，点了同意后再调一次"。）
- 单用户场景：user_key 固定用 `"default"`（本地单人用），不做多用户区分（YAGNI）。

### C. 搜索工具薄壳

- **`tools/feishu_docs.py`**：
  - `feishu_docs_search(search_key, count=20, offset=0, docs_types="")`：`docs_types` 逗号分隔（doc/sheet/slides/bitable/mindnote/file），空=全部。返回候选文档 `{title, token, obj_type}`，agent 再按需 `feishu_doc_read(obj_type, token)` 读全文。

### 完整用户流程

1. 用户对 agent 说"搜一下 XX 文档" → agent 调 `feishu_docs_search` → 若未授权返回"请先授权"。
2. agent 调 `feishu_auth_start` → 给用户一个飞书授权链接 + user_code。
3. 用户浏览器打开、输码、点同意。
4. agent 调 `feishu_auth_complete` → 拿到并存 UAT。
5. agent 重试 `feishu_docs_search` → 返回文档列表 → 用户选一个 → `feishu_doc_read` 读全文。
（之后 UAT 缓存，2 小时内免授权；过期自动 refresh；refresh_token 约 30 天。）

## 飞书后台前置（用户侧配置，非代码）

- 应用开通「获取用户信息」相关能力 + scope：`search:docs:read`（或 `drive:drive:readonly`）+ `offline_access`（要 refresh_token）。
- 设备流需应用启用相应 OAuth 能力。
- 这些不到位则 `feishu_auth_start` 返回飞书 error，原样透传。

## 测试

`tests/test_feishu.py` 追加（沿用 `_CapturedInvoke` mock，不打真实 API、不走真实 OAuth）：
- `search_docs_impl`：mock UAT + mock `_invoke`，断言组装的 BaseRequest（POST、uri、body 的 search_key/count/offset/docs_types）、`token_types={USER}`、解析 docs_entities。
- 未授权路径：store 无 UAT → `ok=false` + 提示。
- 设备流工具：mock `DeviceFlowClient.start/poll`，断言返回结构与 pending 存取；不打网络。
- 工具壳 async + docstring 校验。
- 跑 `ruff check` + `ruff format --check` + `ty check` + pytest 全绿。

## 依赖与打包

- **零新增依赖**：`lark-channel-sdk` 已在 pyproject，设备流/FileTokenStore/UAT 都是它自带。**不改 pyproject / nuitka.yml / pyinstaller.yml。**

## 安全注意

- UAT + refresh_token 明文存 `<workspace>/.psi/feishu/uat.json`。`.psi/` 已被 workspace .gitignore 忽略，不会进 git。在工具返回/文档里提示这是本地明文存储。
- 授权 URL / user_code 不是长期密钥，但也不该乱发。

## 不做（YAGNI）

- 不做授权码回调流（用设备流，省掉 gateway 回调 + redirect_uri 注册 + 固定端口）。
- 不做多用户 token 管理（单人本地场景，user_key 固定 "default"）。
- 不做搜索结果分页自动全量抓取（透传 count/offset）。
- 不改 tenant-token 的现有工具。

## 落地顺序

1. `_feishu_impl.py`：加 UAT store/get/refresh helper + `search_docs_impl`。
2. `tools/feishu_auth.py`（start/complete）+ `tools/feishu_docs.py`（search）。
3. `tests/test_feishu.py` 补测试。
4. ruff + ty + pytest 全绿。
5. 交付：用户飞书后台配 scope → 真机走一次设备流授权 → 搜文档联调。
