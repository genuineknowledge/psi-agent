# 工具发现元工具:tool_search / tool_search_code / tool_describe

## 背景与动机

haitun-workspace 现有 **66 个工具**,[agent.py:179](../../../src/psi_agent/session/agent.py) 每轮把 `self._tool_registry.tools.values()` **全量无差别**塞进 LLM 请求。工具目录越大,提示词开销越高、选择越难。

本任务给 agent 加三个"操作系统级工具发现"元工具,让 agent 能在大型工具目录里**搜索 / 按需读取**工具定义,而不是靠一次性全塞:

| 工具 | OS 类比 | 行为 |
|---|---|---|
| `tool_search` | `apropos` | 按关键词匹配工具**名 + 描述首段**,返回精简列表(name + 一行摘要) |
| `tool_search_code` | `grep` | 在工具**源码正文**里搜正则/子串,按实现细节、依赖、API 名找工具 |
| `tool_describe` | `man` | 按名字返回单个工具**完整定义**:人读签名 + 完整 docstring |

## 关键约束(来自代码调查)

1. **不执行工具模块**:workspace 工具是独立 async 函数,但直接 import/exec 会触发副作用(如 `_mcp` import 期连 Playwright MCP 阻塞 ~90s、browser MCP 崩溃循环)。因此扫描工具定义**必须走 `ast` 静态解析**,绝不执行被扫描的模块。
2. **零新依赖**:`ast` 是 Python 标准库 → **不需要动** pyproject.toml / nuitka.yml / pyinstaller.yml。
3. **一切异步**:文件 IO 全走 `anyio.Path`(`glob`/`read_text`);`ast.parse` 是纯 CPU 操作,无 IO。禁止 `pathlib` 做 IO、禁止 `asyncio` 原生 API。
4. **工具即 `tools/` 下 async 函数**:与 ToolRegistry 加载规则一致 —— 只认 `async def`,跳过 `_` 前缀文件。索引逻辑放 `_tool_index.py`(下划线前缀,不会被当工具加载)。
5. **目标仅 haitun-workspace**(用户已确认)。

## 变更清单

### 新增文件

1. `examples/haitun-workspace/tools/_tool_index.py` — 共享索引/解析实现(下划线前缀,非工具)
2. `examples/haitun-workspace/tools/tool_search.py` — `async def tool_search(...)`
3. `examples/haitun-workspace/tools/tool_search_code.py` — `async def tool_search_code(...)`
4. `examples/haitun-workspace/tools/tool_describe.py` — `async def tool_describe(...)`
5. `examples/haitun-workspace/tests/test_tool_discovery.py` — 三工具 + 索引的单元测试

### 不改动

- pyproject.toml / .github/workflows/nuitka.yml / .github/workflows/pyinstaller.yml(零新依赖)
- src/(框架层不动;这是纯 workspace 能力)

## 详细设计

### `_tool_index.py`(核心,纯 AST + anyio)

职责:扫描 `tools/*.py`,对每个文件用 `ast.parse` 提取顶层 `async def`(公共名,非 `_` 开头)。

导出:

- `async def iter_tool_files(tools_dir: anyio.Path) -> ...` — glob `*.py`,跳过 `_` 前缀文件名。
- `async def index_tools(tools_dir) -> list[ToolMeta]` — 每个 async 函数产出一条 `ToolMeta`:
  - `name`:函数名
  - `file`:所在文件名
  - `summary`:docstring 首段第一行(截断)
  - `description`:docstring `Args:` 之前的完整段落
  - `signature`:从 `ast.arguments` 重建的人读签名 `name(param: type = default, ...)`(类型注解用 `ast.unparse(annotation)`,默认值用 `ast.unparse(default)`)
  - `docstring`:完整 docstring(`ast.get_docstring`)
- 复用点:签名重建与 `ToolFunction._parse_description` 的分段规则保持一致(遇 `Args:`/`Returns:`/`Yields:` 截断),但**独立实现于 AST**,不 import ToolFunction(那会牵扯 src 依赖且无必要)。
- 容错:单个文件 `SyntaxError`/读失败 → 跳过并在结果里可选记一条 error,不整体崩。

`tools_dir` 解析:`anyio.Path(__file__ 所在目录)`,即工具自己所处的 `tools/`。用 `Path(__file__).resolve().parent` 取字符串路径(纯路径运算,非 IO),再转 `anyio.Path`。

### `tool_search(query: str, limit: int = 20) -> str`

- 大小写不敏感,`query` 拆词后匹配 `name + summary + description`。
- 返回精简列表:每行 `name — summary (file)`,按匹配度/名字排序,截断到 `limit`。
- 空结果给明确提示。

### `tool_search_code(pattern: str, limit: int = 50, ignore_case: bool = True) -> str`

- 用 `re` 在每个工具文件**源码正文**里搜,返回 `file:line: 匹配行` 命中(类 grep)。
- `re.error` → 回退为字面子串搜索并说明。
- 截断到 `limit` 行命中。

### `tool_describe(name: str) -> str`

- 在索引里按精确名找;找不到时做前缀/包含的近似建议("did you mean")。
- 命中返回:`名字`、`所在文件`、`签名`(人读)、`完整 docstring`。纯 AST,不执行模块。

### docstring 规范

三个工具函数自身写 Google 风格 docstring(`Args:` 段每参数一行),以便被 ToolRegistry 正确解析成 LLM 工具定义 —— 与仓库现有工具一致。

## 测试(`examples/haitun-workspace/tests/test_tool_discovery.py`)

参照 test_fetch.py 的 `sys.path.insert(TOOLS_DIR)` + `importlib.import_module` 范式。用 `@pytest.mark.anyio`。

用例:
1. `index_tools` 能在真实 `tools/` 目录索引出已知工具(如 `find_files`/`fetch`),且**不触发任何工具副作用**(不连 MCP)。
2. 建临时目录放一个假工具文件,验证签名/docstring/summary 提取正确(含类型注解、默认值、可选参数)。
3. 含语法错误的文件被跳过,不影响其余。
4. `tool_search` 关键词命中已知工具、空结果提示、limit 截断。
5. `tool_search_code` 正则命中行号正确、非法正则回退、limit 截断。
6. `tool_describe` 命中返回完整定义、未命中给近似建议。
7. `_` 前缀文件(如 `_fetch_impl.py`)不出现在索引里。

## 验证

- `uv run pytest examples/haitun-workspace/tests/test_tool_discovery.py -q`
- `uv run ruff check` + `uv run ruff format --check`(仓库 CI 两个都跑)
- 三个工具能被 ToolRegistry 正确加载(签名/docstring 可解析)——加一条断言:`ToolFunction.from_callable` 对三个函数不抛异常。

## 分支与提交

- worktree:`/c/Users/12815/psi-agent-tool-tool`,分支 `add-tool-tool-work`(跟踪 origin/add-tool-tool)。
- 只 push 到功能分支 `add-tool-tool`,是否合入 main 由用户决定。
