# 前端树远程同步指南（spa-v2）

**给谁看：** `D:/Haitun-develop-spa-v2` 上的前端施工 Agent / 开发者
**前提假设：** 三区路径 + AppData 4A–4D 已合进 `origin/main`。
**目标：** 把 `main` 拉进本树功能分支，**不丢掉** spa-v2 WIP。

---

## 1. 你这棵树是什么

| 项 | 约定 |
|----|------|
| 路径 | `D:/Haitun-develop-spa-v2` |
| 角色 | **只改** `src/psi_agent/gateway/spa-v2/`（及必要的 Gateway 壳 / spa v1） |
| 典型分支 | `feat/…` |
| 与参谋树关系 | 与 `Haitun-develop-workflow` **同 remote**；对方合进 main ≠ 自动出现在你磁盘上 |

---

## 2. 同步前先认清「会碰到什么」

| 能力 | 前端要知道的 |
|------|----------------|
| `GET /defaults` | `agent`、`workspace`、`appdata`；UI 主要消费 agent/workspace；**不要**直读 AppData 盘符 |
| `POST /sessions` | 可带 `agent`；省略时 Gateway 默认 |
| `/history` / `/todos` | Gateway 已 dual-read AppData；前端仍只调 REST |
| Session 列表 | 可有 `agent` 字段 |

详情：`spa-v2/AGENTS.md`、`gateway/AGENTS.md`。

---

## 3. 推荐操作（保留当前功能分支）

```powershell
cd D:\Haitun-develop-spa-v2
git status -sb
git branch --show-current
git fetch origin
# 保护 WIP：stash 或先 commit
git merge origin/main
cd src\psi_agent\gateway\spa-v2
npm test
npm run build
```

**不要** `git reset --hard origin/main` 除非用户明确要求。改完经 Gateway 验收前先 `npm run build`。

---

## 4. 对齐后再开发

1. 先读：`spa-v2/AGENTS.md` → `gateway/AGENTS.md`（defaults / history / todos）→ 根 `AGENTS.md` 三区 ADR
2. 新建任务继续 `GET /defaults` 后 `POST /sessions` 带 `agent`
3. 历史/进度只走 `/history`、`/todos`，假设服务端已处理 AppData
