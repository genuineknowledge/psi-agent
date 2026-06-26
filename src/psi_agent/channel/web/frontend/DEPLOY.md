# Web 演示：四 Agent 路由部署

前端（Vue）通过 `POST /api/chat` 把 `{message, modules, compare}` 发给 web channel，
web channel 按 `modules.flow` / `modules.security` 路由到 4 个 agent 之一。

## 路由表

| flow | security | workspace (`/home/ecs-user/Dolphin-Agent/examples/...`) |
|------|----------|---------------------------------------------------------|
| on   | off      | `fusion-offguard`  （有 flow / 无安全）                  |
| on   | on       | `fusion-onguard`   （有 flow / 有安全）                  |
| off  | off      | `hermes-offguard`  （无 flow / 无安全，裸 Hermes 对照）  |
| off  | on       | `hermes-onguard`   （无 flow / 有安全）                  |

前端「模块总开关」全关 = flow+security 全 off → `hermes-offguard`；
再单独打开「安全护栏」→ `hermes-onguard`。其余 5 个开关只做展示，不参与路由。

## 一、在 101.37.22.237 起 4 个 session

每个 agent 是一个 `psi-agent session`，绑定 workspace，监听一个 TCP 端点。
（这里用 8851–8854，按需调整。`<ai-endpoint>` 是你们 AI 后端的地址。）

```bash
WS=/home/ecs-user/Dolphin-Agent/examples
AI=<ai-endpoint>          # 例如 http://127.0.0.1:8800/v1

psi-agent session \
  --workspace $WS/fusion-offguard \
  --channel-socket http://127.0.0.1:8851 \
  --ai-socket $AI &

psi-agent session \
  --workspace $WS/fusion-onguard \
  --channel-socket http://127.0.0.1:8852 \
  --ai-socket $AI &

psi-agent session \
  --workspace $WS/hermes-offguard \
  --channel-socket http://127.0.0.1:8853 \
  --ai-socket $AI &

psi-agent session \
  --workspace $WS/hermes-onguard \
  --channel-socket http://127.0.0.1:8854 \
  --ai-socket $AI &
```

> `--channel-socket` 也可用 unix socket 路径（如 `/tmp/fusion-offguard.sock`），
> 本机连接更省事；跨机访问用 `http://0.0.0.0:<port>`。

## 二、起 web channel，连上 4 个 session

```bash
psi-agent channel web \
  --session-socket   http://127.0.0.1:8851 \
  --fusion-offguard  http://127.0.0.1:8851 \
  --fusion-onguard   http://127.0.0.1:8852 \
  --hermes-offguard  http://127.0.0.1:8853 \
  --hermes-onguard   http://127.0.0.1:8854 \
  --listen           http://127.0.0.1:8765
```

- `--session-socket` 是兜底端点（任一路由未配置时用它），这里指向 fusion-offguard。
- web channel 暴露 `/api/chat`、`/api/upload`、`/api/download` 在 `:8765`。

## 三、前端

开发：
```bash
cd src/psi_agent/channel/web/frontend
npm install
VITE_API_TARGET=http://127.0.0.1:8765 npm run dev -- --host
```
`vite.config.js` 已把 `/api` 代理到 `VITE_API_TARGET`（默认 `http://127.0.0.1:8848`，按上面的 8765 覆盖）。

生产：
```bash
npm run build          # 产物在 dist/
npm run preview -- --host
```

## 路由验证（不依赖 AI，先验证分发）

```bash
curl -N http://127.0.0.1:8765/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"hi","modules":{"flow":false,"security":true},"compare":false}'
# 观察 web channel 日志：Web chat routed to flow=False security=True -> http://127.0.0.1:8854
```
