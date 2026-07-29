# 后端 / workspace 树远程同步指南

**给谁看：** `D:/Haitun-develop-workspace` 上的 haitun-workspace / 后端能力施工 Agent / 开发者
**前提假设：** 三区路径 + AppData 4A–4D 已合进 `origin/main`（agent/workspace 拆分、`_runtime_paths`、todos/history/Gateway state → AppData）。
**目标：** 把 `main` 拉进本树功能分支，**不丢掉**飞书 / schedule / skill 等 WIP。

---

## 1. 你这棵树是什么

| 项 | 约定 |
|----|------|
| 路径 | `D:/Haitun-develop-workspace` |
| 角色 | **主要改** `examples/haitun-workspace/`，以及必要的 Session/Gateway 服务端 |
| 典型分支 | `feat/…` |
| 与参谋树关系 | 与 `Haitun-develop-workflow` **同 remote**；对方合进 main ≠ 自动出现在你磁盘上 |

---

## 2. 同步前先认清「会碰到什么」

| 能力 | 后端要知道的 |
|------|----------------|
| Session `agent` | 能力包从 **agent** 加载；用户文件 IO 在 **workspace** |
| ContextVar | 回合内 `get_workspace()` / `get_agent()` / `get_session_id()`；**禁止** AppData/密钥进 ContextVar |
| `_runtime_paths` | 相对路径 → workspace；`skill_manage` → agent/`skills/` |
| `GET /defaults` | `agent` + `workspace` + `appdata` |
| AppData（已搬家） | **写** `{appdata}/todos|histories|state/`；**读**优先 AppData，缺则 legacy 双读；助手在 `psi_agent._appdata` |

合并时以各层 `AGENTS.md`（尤其 `session/`、`gateway/`、`examples/haitun-workspace/`）为准。

---

## 3. 推荐操作（保留当前功能分支）

在 **workspace 树根**执行。原则：**继续停在你的 `feat/…` 上** 接入 `main`。

```powershell
cd D:\Haitun-develop-workspace
git status -sb
git branch --show-current
git fetch origin
# 保护 WIP：stash 或先 commit
git merge origin/main
# 冲突：对照 AGENTS.md 保留三区 / AppData / ContextVar 约定后再叠你的功能
uv run ruff check examples/haitun-workspace/tools src/psi_agent/session
uv run pytest examples/haitun-workspace/tests/test_runtime_paths.py -q --override-ini=addopts=
```

**不要** `git reset --hard origin/main` 除非用户明确要求丢掉本地提交。

---

## 4. 对齐后再开发

1. 先读：根 `AGENTS.md` → `session/AGENTS.md` → `gateway/AGENTS.md` → `examples/haitun-workspace/AGENTS.md`
2. 新工具路径走 `_runtime_paths` / `_appdata`，勿手写 `%AppData%`、勿把 AppData 塞 ContextVar
3. 目录名仍是 **`examples/haitun-workspace`**（未改名为 `haitun`）；Gateway 软默认认此名
