# Haitun Feishu Web

飞书端海豚应用 B 端前端。技术栈与 C 端一致：Vite + React 19 + TypeScript，
图标使用 `lucide-react`，消息渲染使用 `marked`。

## 目录结构

- `src/main.tsx`：应用入口，挂载 `App` 并包一层错误边界
- `src/App.tsx`：页面与交互（任务总览、对话、任务上下文、交付物侧栏等）
- `src/api.ts`：后端 API 封装（飞书身份登录、AI/会话/历史/聊天等）
- `src/styles.css`：全局样式
- `vite.config.ts`：开发服务器配置，`/auth`、`/ais`、`/sessions`、`/feishu` 等
  接口代理到本机 `feishu_gateway`（默认 `http://127.0.0.1:8080`）

## 开发命令

```bash
npm ci
npm run dev
npm run build
npm run preview
```
