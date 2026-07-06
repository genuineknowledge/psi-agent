# 修复 haitun-workspace 工具暴露错配 — 设计

日期:2026-07-06
分支:add-web-fetch(worktree `C:\Users\12815\psi-agent-webfetch`)

## 背景与问题

haitun-workspace 里"agent 真实拥有的工具"与"系统提示告诉 agent 的工具"是两条互不校验的管线:

1. **真实可调用**:`src/psi_agent/session/tool_registry.py` 的 `ToolRegistry` 加载每个非 `_` 文件里所有 public async 函数 → `agent.py` 拼成 function-calling schema 发给模型。
2. **提示里"看到"**:`examples/haitun-workspace/systems/system.py` 的 `_scan_tool_names`(第 691 行)只按**文件名 stem** 列进 ## Tooling 段;描述取自 `prompt_sections.py` 的 `CORE_TOOL_SUMMARIES` / `TOOL_ORDER` 白名单。`system_prompt_builder()` 调用时不传 tool_names,永远走文件名 fallback。

由此产生三处错配:

- **`search` 是幽灵名**:`search.py` 靠 `@mcp` 装饰器在运行时注入真实工具 `serper_*`,顶层无 `async def`。提示按文件名列出的 `search` 根本不存在;真实的 `serper_*` 反而不在提示里。
- **`background_list` 列不出**:定义在 `background_stop.py` 里(一个文件两个函数),真实已注册,但 Tooling 段按文件名扫,`background_list` 这个名字进不了清单。
- **5 个工具缺描述**:`flow_run` / `find_files` / `search_content` / `describe_image` / `generate_image` 在 `CORE_TOOL_SUMMARIES` 里无条目,提示中只有裸名字。

## 方案

只改 workspace,不碰 `src/`。采用 AST 静态解析(无副作用、不执行工具代码),对 mcp 文件加一份 serper 映射补充运行时注入的名字。

### 改动文件

1. `examples/haitun-workspace/systems/system.py` — `_scan_tool_names`
2. `examples/haitun-workspace/systems/prompt_sections.py` — `CORE_TOOL_SUMMARIES` / `TOOL_ORDER`

### `_scan_tool_names` 新逻辑

- 对每个 `tools/*.py`(跳过 `_` 前缀文件),用 `ast` 解析源码,收集模块顶层 `ast.AsyncFunctionDef` 且函数名不以 `_` 开头的。
  - `background_stop.py` → `background_stop`、`background_list`(修复"列不出")
  - `search.py` → 顶层无 async def → 0 个 → 幽灵名 `search` 自动消失
  - 其余文件 → 函数名 == 文件名,行为不变
- **mcp 补充**:检测文件顶层是否使用 `@mcp` 装饰器(AST 层面识别 `mcp` 装饰名)。命中时补入该 mcp 的已知工具名。当前唯一 mcp 文件 `search.py` 补入 `serper_google_search`(主工具),不铺满 13 个变体。
- 解析失败(语法错误等)对单个文件安全跳过,不影响其它文件;整体保持返回排序后的名字列表。

### serper 呈现策略

`serper-mcp-server`(AGENTS.md 记载 `uvx serper-mcp-server`,即 garylab/serper-mcp-server)运行时注册 13 个工具,加 `serper_` 前缀:
google_search / google_search_images / _videos / _places / _maps / _reviews / _news / _shopping / _lens / _scholar / _patents / _autocomplete / webpage_scrape。

Tooling 段只列主工具 `serper_google_search`,其描述点明存在 `serper_*` 变体系列,避免 13 行噪音,同时让模型知道联网搜索能力与命名规律。

### 补齐的描述(取自各工具 docstring 首行)

加入 `CORE_TOOL_SUMMARIES` 并加进 `TOOL_ORDER`:

- `find_files`: Recursively find file paths matching a glob pattern, newest first
- `search_content`: Search file contents for a regex or literal string and return matching lines
- `flow_run`: Run a Fusion Flow (.flow.ts) in the background and poll node-level progress
- `describe_image`: Return a text description or answer about an image file
- `generate_image`: Create an image file from a scene description
- `serper_google_search`: Web search via Serper (needs SERPER_API_KEY); serper_* variants exist for images/news/scholar/webpage_scrape etc.

## 验证

无现成测试。用 system.py 的 `__main__` smoke 入口:

```
python examples/haitun-workspace/systems/system.py
```

打印完整系统提示,确认 ## Tooling 段:
- 出现 `background_list`
- 出现 `serper_google_search`(带变体提示的描述)
- 6 个新描述工具都带一句话说明
- 幽灵名 `search` 不再出现

改动为纯 Python 逻辑 + 数据,无副作用。

## 非目标(YAGNI)

- 不改 `src/`,不打通 tool_names 从 src 传入 workspace 的根因管线(用户选择只改 workspace)。
- 不为 mcp 文件做真实 import(有副作用:连 serper、读 .env)。
- 不列全 13 个 serper_* 变体。
- skill 侧无缺 SKILL.md 目录,本次不改。
