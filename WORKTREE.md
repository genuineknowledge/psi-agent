# Git worktree 工作结构（给后续开发者）

本仓库推荐用 **`git worktree` 多工作目录并行开发**，不要复制整份项目文件夹。

- **共用**：同一份 Git 历史（commit / branch / remote）
- **隔离**：各树未提交工作区互不可见、不自动同步
- **规则**：同一 Git 分支同一时间只挂一棵树；换树 = 打开另一个文件夹 / 编辑器窗口
- **建议分工**：一棵前端（`Haitun-develop-spa-v2`）、一棵后端/workspace（`…-workspace`）、一棵主线/流程（`…-main` / `…-workflow`）
- **Agent**：可两窗各挂一树各干各的；要对齐契约时再靠 commit / fetch（或人口头转述），不必时刻互通脏改动
- **命名**：文件夹名应直接体现角色（`spa-v2` / `main` / `workflow` / `workspace`），避免无后缀的模糊目录名

完整约定与本机路径示例见根目录 **`AGENTS.md` →「本地并行开发：推荐用 `git worktree`」**。

```bash
git worktree list
# 本机示例：
# D:/Haitun-develop-spa-v2      [feat/…]
# D:/Haitun-develop-main        [main]
# D:/Haitun-develop-workflow    [chore/…]
```