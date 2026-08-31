# guoshu-weekly-bff

国数周报 Agent 的 BFF(Backend For Frontend),对应方案文档第六章 6.3 的四项职责:

1. **登录态校验** — 未登录请求不进 Gateway。
2. **身份映射** — 已认证用户 → 专属 Session 与 workspace,并注入 MCP bearer token。
3. **密钥注入** — Gateway 共享密钥只存在于 BFF 与 Gateway 之间,浏览器永不可见。
4. **限流与 SSE 透传** — 按用户限流;SSE 逐行转发不缓冲(缓冲会毁掉首 token 流式)。

## 全链路启动(从零)

本目录只是 B 线的前端侧中间层,**跑通对话还需要三样前置**(不在本目录):

1. **psi-agent Gateway**:主仓启动 `uv run psi-agent gateway --listen http://127.0.0.1:8766 --default-agent <guoshu agent 包路径>`。
2. **guoshu agent 包**(A 线,仓库内 `examples/guoshu-weekly-workspace/` 或 A 线分支):Gateway 需要 `GUOSHU_WEEKLY_MCP_URL` / `GUOSHU_WEEKLY_MCP_TOKEN` 指向取数服务;demo 阶段用 A 线自带的 mock-mcp + MySQL mock 库。
3. **取数 MCP 服务**:入口组正式服务,或 A 线 mock 服务(`examples/guoshu-weekly-workspace/mock-mcp/server.py`)。

然后是 B 线自身两段:

```bash
# 1. BFF(本目录)
uv sync
BFF_SESSION_SECRET=<随机串> \
GUOSHU_WEEKLY_MCP_TOKEN=<与 gateway 一致的 token> \
uv run uvicorn bff.main:app --host 127.0.0.1 --port 8780

# 2. 前端(../guoshu-weekly-frontend)
npm install && npm run dev   # http://localhost:5173,/api 代理到 8780
```

浏览器打开 http://localhost:5173,试用账号 demo/demo 登录。整体链路:

```
浏览器(5173)→ vite 代理(/api)→ BFF(8780)→ gateway(8766)→ agent 包 → MCP 取数服务
```

## 当前边界

- 登录为**开发期共享账号**(`BFF_DEV_USERNAME` / `BFF_DEV_PASSWORD`);公网试用替换为注册 + 邀请码,正式环境替换为 OA 单点登录。
- MCP token 为**单 token 模式**(与 A 线 demo 一致);`GUOSHU_WEEKLY_TOKEN_MAP_FILE` 已实现 per-user 读取(附录 C 形态、0600 校验、一 token 一身份),等入口组答复后切换。
- `PSI_GATEWAY_SHARED_SECRET` 设置后转发带 `X-Gateway-Secret` 头;Gateway 侧的校验需在公网暴露前落地(方案 7.4)。

## 配置

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `BFF_SESSION_SECRET` | 是 | 会话签名密钥(随机长串,进程启动方持有) |
| `PSI_GATEWAY_BASE_URL` | 否 | 默认 `http://127.0.0.1:8766` |
| `GUOSHU_WEEKLY_MCP_URL` | 否 | BFF 直连取数服务的地址(周报总结生成用),默认 `http://127.0.0.1:18901/mcp` |
| `GUOSHU_WEEKLY_MCP_TOKEN` | 否 | 单 token 模式的 token(周报总结生成用;为空时不带 Authorization 头) |
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
POST /api/sessions/{id}/chat           → SSE 透传(限流;body 可带 files 附件,单个 ≤20MB、最多 5 个)
GET  /api/sessions/{id}/history        → 透传
GET  /api/reports/weekly-summary       → 周报总结报告生成(P1-1,Word,文件名带日期)
GET  /api/sessions/{id}/export?format=excel|pdf   → 对话历史导出(P1-3,按轮次)
```

身份只来自签名会话,不取请求头自报,不取模型可见文本自称(方案 5.3)。

## 测试

```bash
uv run pytest tests/ -q     # 31 个逻辑测试:签名/令牌映射/限流/协议转换/附件校验/导出渲染
```
