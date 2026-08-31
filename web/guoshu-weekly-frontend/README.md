# guoshu-weekly-frontend

国数周报 Agent 的专属对话前端(方案 6.1),React + TypeScript + Vite。

四视图:对话(主视图)、历史(占位,第 5 期)、报告(占位,第 5 期)、登录(试用账号)。

## 启动

```bash
npm install
npm run dev      # http://localhost:5173,/api 经 vite 代理转发到 BFF(127.0.0.1:8780)
```

依赖:BFF 已启动(见 ../guoshu-weekly-bff/README.md),BFF 背后是 gateway 与 mock 取数服务。

## 联调链路

```
浏览器(5173)→ vite 代理(/api)→ BFF(8780)→ gateway(8766)→ agent 包 → mock MCP(18901)→ MySQL 8.4(3307)
```

前端只认识 `/api`,永远不知道 Gateway 地址与密钥(方案 6.2/6.3)。

## 边界与约定

- **登录**:试用账号(demo/demo),BFF 签发 httponly 会话 cookie;注册与邀请码在 B5(公网试用)落地。
- **流式渲染**:fetch + ReadableStream 逐行消费 SSE(EventSource 不支持 POST),收到一行渲染一行——首 token 可见依赖这里不做缓冲。
- **个性化面板**:身份(领导/个人)与输出偏好(结论/过程优先)为 demo 阶段页内切换,由 BFF 注入为「回答组织指令」;生产阶段身份来源换成登录角色(方案 6.2:身份取自可信标识,前端切换不得成为越权入口)。
- **会话**:sessionId 存 localStorage;401(会话失效)自动退回登录页。
- **视觉基准**:《周报 POC 离线交付包》03 核心对话版(绿色主色、卡片对话、结论先行)。
- **演示数据**:页面常驻「演示数据,非集团真实周报」明示(方案 10.2)。

## 构建

```bash
npm run build     # 产物 dist/,由 BFF/反向代理托管(第 3 期起)
npx tsc -b        # 类型检查
npx vitest run    # 单元测试
```
