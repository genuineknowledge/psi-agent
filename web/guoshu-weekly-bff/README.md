# guoshu-weekly-bff

国数周报 Agent 的 BFF(Backend For Frontend),对应方案文档第六章 6.3 的四项职责:

1. **登录态校验** — 未登录请求不进 Gateway。
2. **身份映射** — 已认证用户 → 专属 Session 与 workspace,并注入 MCP bearer token。
3. **密钥注入** — Gateway 共享密钥只存在于 BFF 与 Gateway 之间,浏览器永不可见。
4. **限流与 SSE 透传** — 按用户限流;SSE 逐行转发不缓冲(缓冲会毁掉首 token 流式)。

## 当前边界(B3 阶段,拆解文档 B3 行)

- 登录为**开发期共享账号**(`BFF_DEV_USERNAME` / `BFF_DEV_PASSWORD`);第 3 期(公网试用)替换为注册 + 邀请码,第 6 期替换为 OA 单点登录。
- MCP token 为**单 token 模式**(与 A 线 demo 一致);`GUOSHU_WEEKLY_TOKEN_MAP_FILE` 已实现 per-user 读取(附录 C 形态、0600 校验、一 token 一身份),等 Q3 答复后切换。
- `PSI_GATEWAY_SHARED_SECRET` 设置后转发带 `X-Gateway-Secret` 头;Gateway 侧的校验需在公网暴露(B5)前落地(方案 7.4)。

## 运行

```bash
uv sync
BFF_SESSION_SECRET=<随机串> \
PSI_GATEWAY_BASE_URL=http://127.0.0.1:8766 \
BFF_WORKSPACE_ROOT=C:/Users/<you>/AppData/Local/Temp/guoshu-b1-workspaces \
uv run uvicorn bff.main:app --host 127.0.0.1 --port 8780
```

## 配置

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `BFF_SESSION_SECRET` | 是 | 会话签名密钥(随机长串,进程启动方持有) |
| `PSI_GATEWAY_BASE_URL` | 否 | 默认 `http://127.0.0.1:8766` |
| `GUOSHU_WEEKLY_MCP_TOKEN` | 否 | 单 token 模式的 demo token |
| `GUOSHU_WEEKLY_TOKEN_MAP_FILE` | 否 | per-user token map(JSON,0600,附录 C 形态) |
| `BFF_WORKSPACE_ROOT` | 否 | 每用户 workspace 的根目录,默认公共目录 |
| `PSI_GATEWAY_SHARED_SECRET` | 否 | 设置后转发带 `X-Gateway-Secret`(预留) |
| `BFF_DEV_USERNAME` / `BFF_DEV_PASSWORD` | 否 | 开发期共享账号,默认 demo/demo |
| `BFF_RATE_LIMIT_PER_MINUTE` | 否 | 默认 20 |
| `BFF_LISTEN` | 否 | 默认 `127.0.0.1:8780` |

## 接口(方案 7.4 出口 + 登录)

```
POST /api/login                        → 签发 httponly 会话 cookie
POST /api/logout
GET  /api/health                       → BFF 自身 + Gateway 连通性
POST /api/sessions                     → 建会话(每用户 workspace,自动绑定 AI)
POST /api/sessions/{id}/chat           → SSE 透传(限流)
GET  /api/sessions/{id}/history        → 透传
```

身份只来自签名会话,不取请求头自报,不取模型可见文本自称(方案 5.3)。
