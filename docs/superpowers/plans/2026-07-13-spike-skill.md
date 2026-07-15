# Plan: 新增 `spike` 技能（一次性抛弃式技术预研）

## 目标

给 haitun-workspace 加一个 `spike` 技能，指导 Agent 在正式开发前做**时间盒内的一次性抛弃式实验**，
只为回答一个具体的技术未知问题（"这个库/API/方案能不能做到 X？"），验证完保留**结论**、丢弃**代码**。
场景：技术预研 / 选型对比 / 假设验证 / 第三方 API 摸底 / 性能预估。

## 决策（已与用户对齐）

- **纯 markdown、零依赖**：复用已有 `bash`/`read`/`write`/`edit`/`find_files`/`search_content`。
  试第三方库时用**临时隔离环境**（`uv run --with`、临时 venv），**不动仓库 pyproject.toml**——
  这正是"抛弃式"的体现。因此**不改** `pyproject.toml` / nuitka / pyinstaller 三处打包。
- **category: coding**（同 tdd / simplify-code）。
- 与相邻技能的分工：与 `task-planning`（正式执行清单）、`test-driven-development`（追求质量的正式实现）
  相对；spike 明确**不写测试、不追质量、不进主干**。产出结论后若要真做，走 tdd/正式流程**重写**。

## 交付物

单文件：`examples/haitun-workspace/skills/spike/SKILL.md`

frontmatter：
- `name: spike`
- `description`：中英双语关键词触发（预研/摸底/验证可行性/spike/prototype/proof-of-concept/选型），
  说明零依赖、复用已有工具、抛弃式定位。
- `category: coding`

## SKILL.md 章节结构（仿 simplify-code / task-planning 风格，中文正文）

1. **标题 + 一段定位**：什么是 spike、为什么"代码是垃圾、结论才是产出"。
2. **铁律（最高优先级）**：
   - 单一明确问题 + 成功/失败判据，动手前写下来。
   - 时间盒（默认给个上限，如 30–60 min 或用户指定），到点必须停下做决策。
   - 隔离在 `spikes/<topic>/` 目录，**绝不**改生产代码/生产依赖。
   - 试库用临时环境（`uv run --with`），不写进 pyproject。
   - 不写测试、不做错误处理、不追代码质量——够回答问题即可。
   - 结束**必产出**：结论文档（可行性 + 发现 + 建议）；spike 代码丢弃或标记为不可复用。
   - spike 代码**永不**直接进正式实现；要用就在正式流程里重写。
3. **何时用 / 不用**：用=未知技术风险要先验证；不用=需求已明确该直接实现（走正式流程）、
   或只是查文档就能答（直接查）。
4. **流程 Step 0–5**：
   - Step 0 定义问题与判据（一句话问题 + 可判定的成功条件）。
   - Step 1 定时间盒。
   - Step 2 隔离环境搭最小原型（`spikes/` 目录 + 临时依赖）。
   - Step 3 跑实验、观察、记录发现（不美化）。
   - Step 4 到点决策：可行 / 不可行 / 需再探（缩小问题再来一轮）。
   - Step 5 产出结论文档 + 处理代码（丢弃/归档），清理临时环境。
5. **结论文档模板**：问题、判据、做了什么、发现、结论、对正式实现的建议。
6. **反模式表**：spike 代码偷偷进主干 / 无时间盒无限投入 / 装依赖进 pyproject / 
   给 spike 写测试追质量 / 问题太宽泛无法判定 / 验证完不留结论只留代码。
7. **自检清单**。
8. **相关**：链接 `task-planning`、`test-driven-development`、`subagent-orchestration`（大范围预研可并行）。

## 验证

- 技能靠 `system.py` 运行时扫描 `skills/*/SKILL.md` 自动进索引，无中央注册表——放对目录即可。
- 校验：frontmatter 三字段齐全、`_strip_frontmatter` 能正确解析（`---` 包裹）、
  相对链接指向本 workspace 内确实存在的技能目录。
- 纯 markdown 无需跑 ruff/pytest；确认无 dangling 跨技能链接。

## Git

- 独立 worktree `psi-agent-spike`（已建），分支 `add-spike`。
- commit 后按 [[psi-agent-no-merge-to-main]] 只 push 到 `add-spike`，是否合入 main 由用户决定。
