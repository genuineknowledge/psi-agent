# 修复飞书文档搜索授权 —— 设备流换授权码流（国内版 feishu.cn）

日期：2026-07-14
分支：`add-feishu-tools1`
状态：待批准

## 背景 / 根因（已实测确认）

- 已加的 `feishu_auth_start/complete` 用 SDK 设备流，走 `/open-apis/authen/v2/oauth/device_authorization`。
- **实测该端点在国内版飞书 open.feishu.cn / passport.feishu.cn / accounts.feishu.cn 全部返回纯文本 `404 page not found`** —— 国内版飞书不提供设备流。这是搜索授权失败的真因（不是凭证/权限，凭证错会返 JSON 错误码而非 404）。
- 用户确认用的是**国内版 feishu.cn**。

## 国内版支持的授权方式：授权码流（authorization code，已查证端点）

1. **浏览器授权**（用户点同意）：GET `https://accounts.feishu.cn/open-apis/authen/v1/authorize`
   query：`client_id`(=app_id)、`redirect_uri`、`response_type=code`、`scope`（空格分隔，含 `offline_access` 才给 refresh_token）、`state`。
   用户同意后飞书带 `?code=...&state=...` 跳到 `redirect_uri`。
2. **code 换 user_access_token**：POST `https://open.feishu.cn/open-apis/authen/v1/access_token`
   header 用 **app_access_token**；body `{grant_type:"authorization_code", code}`。
   返回 `access_token`(UAT) + `refresh_token` + `expires_in` + `refresh_expires_in`。
3. **刷新**：POST `https://open.feishu.cn/open-apis/authen/v1/refresh_access_token`
   header 用 app_access_token；body `{grant_type:"refresh_token", refresh_token}`。
4. app_access_token（内部自建应用）：POST `/open-apis/auth/v3/app_access_token/internal`，body `{app_id, app_secret}`。

## 方案选择：手动粘贴 code（推荐，workspace-only，不碰框架源码）

授权码流要有 `redirect_uri`。两条路：

- **A. 手动粘贴 code（推荐）**：`redirect_uri` 注册成一个占位地址（如 `http://localhost/`）。用户点同意后浏览器跳转，**地址栏出现 `?code=XXX`**；用户把 code（或整段跳转 URL）贴回给 agent，`feishu_auth_complete(code)` 拿去换 token。
  - 优点：**只动 workspace，不改框架 `src/`**；不需要 gateway 固定端口；不需要 gateway 回调路由。契合本地单人场景。
  - 缺点：用户要从地址栏复制一次 code（多一步手动）。
- **B. gateway 回调路由**：给 `src/psi_agent/gateway/server.py` 加 `GET /feishu/oauth/callback`，自动收 code 写进 `<workspace>/.psi/feishu/oauth_code.json`，tool 轮询读取。
  - 缺点：**改框架源码**；gateway 必须用固定 `--listen` 端口（默认随机端口，回调地址不稳定就没法注册）；跨进程写文件要对齐 workspace 路径。重且侵入。

**本计划按 A 实施。** 若你要 B，说一声我改。

## 实现（改 `_feishu_impl.py` + 两个工具，替换现有失效授权工具）

### 1. `_feishu_impl.py`

- **删除设备流实现**：`auth_start_impl`/`auth_complete_impl` 里对 `DeviceFlowClient` 的用法（该 import 走 v2 设备流，国内 404）。保留 `search_docs_impl`、UAT 存储（`FileTokenStore`）、`_get_valid_uat`、`_get_uat_client` 不变。
- **新增授权码流 helper（用 SDK 自带 httpx 或 aiohttp 直接打，端点如上）**：
  - `_redirect_uri()`：默认 `http://localhost/`（可被 env `PSI_FEISHU_REDIRECT_URI` 覆盖）。
  - `auth_start_impl(scopes)`：拼授权 URL（`accounts.feishu.cn/.../authorize`，带 client_id/redirect_uri/response_type=code/scope/state），生成随机 `state` 存 `.psi/feishu/pending_auth.json`，返回 `authorize_url` + 提示"打开→同意→把地址栏里的 code 贴回来"。
  - `_get_app_access_token()`：POST `/open-apis/auth/v3/app_access_token/internal` 拿 app_access_token。
  - `auth_complete_impl(code)`：读 pending（可校验 state）→ 取 app_access_token → POST `/authen/v1/access_token` 换 UAT → 存进 `FileTokenStore` → 删 pending。返回 open_id/scopes。
  - `_get_valid_uat` 的刷新改走 `/authen/v1/refresh_access_token`（原来调 DeviceFlowClient.refresh，也走 v2，要一并换掉）。
  - code 里若用户贴了整段 URL，做个宽松解析：能从 `code=` 参数里抠出 code。

### 2. 工具壳 `feishu_auth.py`（改签名）

- `feishu_auth_start(scopes="")` → 返回 `authorize_url`（agent 转达给用户去点）。
- `feishu_auth_complete(code)` → 收用户粘贴的 code（或整段回调 URL），换并缓存 UAT。
  （去掉 `timeout_seconds`，不再轮询。）

### 3. `feishu_docs.py`（搜索工具）不变

`feishu_docs_search` 逻辑不变（未授权→提示先 auth）。

## 测试（`tests/test_feishu.py` 更新）

- 删设备流相关的旧测试（`test_auth_start_returns_url` 里 mock 的 `DeviceFlowClient`）。
- 新增：mock httpx/aiohttp（或 mock 内部 `_post` helper），验证
  - `auth_start_impl` 拼出的授权 URL 含正确 client_id/redirect_uri/scope/state，pending 落盘。
  - `auth_complete_impl` 用 code 换 token（断言打到 `/authen/v1/access_token`、body 带 code）、UAT 入 store。
  - 整段 URL 粘贴时能抠出 code。
  - `search_docs_impl` 现有测试保持绿。
- ruff / ruff format / ty / pytest 全绿。

## 依赖与打包

- 零新增依赖（httpx 是 lark-channel-sdk 传递依赖；或用已有 aiohttp）。不改 pyproject/nuitka/pyinstaller。

## 用户侧前置（飞书后台）

- 应用「安全设置」里**注册重定向 URL**（redirect_uri，与工具用的一致，如 `http://localhost/`）。
- 开通 scope：`docs:doc:readonly` / `drive:drive:readonly` + `offline_access`（要 refresh）。
- 发布版本。

## 安全

- UAT + refresh_token 仍明文存 `<workspace>/.psi/feishu/uat.json`（`.psi/` 已 gitignore，不进库）。返回里提示。

## 不做（YAGNI）

- 不做 gateway 回调路由（方案 B）除非用户要。
- 不做多用户。
