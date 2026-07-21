# SPA v2 — 任务驱动工作台（Gateway 联调版）

> 与 `../spa/`（对话气泡 v1）并行。UI 来自任务/宝箱设计包；**运行时已接 Gateway**：
> Task ≈ Session，对话走 `/sessions/{id}/chat` SSE，历史走 `/history`。
>
> **Gateway 默认控制台**：`GET /` → `/spa-v2/index.html`（`--browser` / webview 打开根地址即 v2）。v1 仍在 `/spa/`。

## 与 spa v1

| | spa (v1) | spa-v2 |
|--|----------|--------|
| 产品隐喻 | Session 对话气泡 | 任务卡片 + 交付物宝箱 |
| 技术栈 | Vue 3 + Pinia | React 19 + Vite |
| base | `/spa/` | `/spa-v2/` |
| 对话 | Gateway SSE | 同左（同一套 API） |
| 交付物 | 气泡 blob chip | 宝箱 UI；SSE `blob` 会写入 `deliverables` |
| 模板/收件箱 | Hub / 侧栏 | 模板仍为本地 Mock；收件箱暂空（无独立通知 API） |

## 映射

```text
任务卡          ↔  Gateway Session（同 workspace）
新建任务        ↔  POST /sessions + POST /titles + 首条 chat SSE
卡片内对话      ↔  POST /sessions/{id}/chat（multipart chunks）
任务历史文案    ↔  GET /sessions/{id}/history
打开即用 AI     ↔  空池先开模型面板；「免费」清空配置；对话时惰性 POST `/ais`（远程默认）
```

### 历史展示隔离（对齐敲定协议 / spa v1）

- Gateway `/history` 按 Session ``kind`` **白名单**过滤：只返回 `chat` 气泡，以及 `schedule.display` 的 assistant；`schedule.silent`（含 heartbeat）不返回。
- `historyToChat` 再剥 `[SEND:]`/`[RECV:]`，并丢弃空行 / 泄漏的 `schedule.silent`（防御）。
- 气泡渲染同样 `stripTransferMarkers`（与 v1 一致）。

任务 `status` / `deliveryState` 仍是前端展示字段（Gateway 尚无 Task/Delivery 资源）；`blob` 到达时会把文件名并入宝箱，不自动把任务标为已完成。

## 本地开发

需先有 Gateway 在跑。Vite 默认把 API 代理到 `http://127.0.0.1:8765`：

```bash
# 终端 1 — Gateway（端口以日志为准，若不是 8765 则设 GATEWAY_ORIGIN）
uv run psi-agent gateway --listen tcp:127.0.0.1:8765

# 终端 2
cd src/psi_agent/gateway/spa-v2
# PowerShell: $env:GATEWAY_ORIGIN="http://127.0.0.1:8765"
npm run dev
# → http://localhost:5174/spa-v2/
```

生产/联调：`npm run build` 后 Gateway 自动挂载 `spa-v2/dist` → `http://<gateway>/spa-v2/`。

## 目录

```text
src/
  App.tsx                 # 工作区门禁 → 工作台
  components/WorkspaceGate.tsx
  services/               # api / sse / chatStream / sessionBridge / bootstrapAi
  haitun-agent/           # 任务 UI（设计包）
  components/user-hub/    # 用户中心（自 v1：资料 / 大模型 / 登录 / 设置）
  styles/globals.css
```
