# Git worktree 工作结构（给后续开发者）

本仓库推荐用 **`git worktree` 多工作目录并行开发**，不要复制整份项目文件夹。

- **共用**：同一份 Git 历史（commit / branch / remote）
- **隔离**：各树未提交工作区互不可见、不自动同步
- **规则**：同一 Git 分支同一时间只挂一棵树；换树 = 打开另一个文件夹 / 编辑器窗口
- **施工三角**：前端 `Haitun-develop-spa-v2` + workspace `Haitun-develop-workspace` + 流程参谋 `Haitun-develop-workflow`；`Haitun-develop-main` 仅可选只读对照
- **Agent**：可两窗各挂一树各干各的；要对齐契约时再靠 commit / fetch（或人口头转述），不必时刻互通脏改动
- **命名**：文件夹名应直接体现角色（`spa-v2` / `workspace` / `workflow` / `main`），避免无后缀的模糊目录名

完整约定见根目录 **`AGENTS.md` →「本地并行开发」**（含提交范围：共享约定进 Git；**`AGENT_BOOTSTRAP.md` 与探讨纪要留本机**）。  
每棵树可自备本机 **`AGENT_BOOTSTRAP.md`**（gitignore，不进远程）；新开 Agent 先读它，再读共享 `AGENTS.md`。

```bash
git worktree list
# 施工三角：
# D:/Haitun-develop-spa-v2       [feat/…]   — 前端
# D:/Haitun-develop-workspace    [feat/…]   — workspace
# D:/Haitun-develop-workflow     [chore/…]  — 流程参谋
# （可选）D:/Haitun-develop-main [main]     — 只读对照
```
