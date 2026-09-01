---
name: hermes-agent
description: "Configure, extend, or contribute to Hermes Agent — the open-source AI agent framework by Nous Research (terminal / desktop / messaging / IDE, same category as Claude Code and Codex). LOAD whenever a task needs to install or run Hermes (`hermes` CLI), pick/configure an inference provider (OpenRouter, Anthropic, OpenAI, Ollama, vLLM, custom endpoints, credential pools, fallback/routing), manage config (`~/.hermes/config.yaml` + `.env`), enable tools/skills/MCP/webhooks/cron, run the multi-platform gateway, troubleshoot (`hermes doctor`), or extend/contribute to the framework (repo layout, dev setup, tests, PR conventions). Shell-only recipe driven through the `bash` tool — no dedicated Python tool and no new dependency."
category: autonomous-ai-agents
---

# Hermes Agent（配置 / 扩展 / 贡献）

用这个技能帮用户**安装、配置、运行、扩展或给 Hermes Agent 贡献代码**。Hermes Agent 是
Nous Research 开源的 AI agent 框架（MIT，跨 linux/macOS/windows），能跑在终端、原生桌面
app、消息平台和 IDE 里，和 Claude Code、Codex 属同一类"自主编码/对话 agent"。

它全部通过命令行 `hermes` 操作，是**外部 CLI**（`pip install hermes-agent` 或官方安装脚本装），
不是 Python 包依赖。所有操作都用 workspace 的 `bash` 工具跑，**没有专门的 Python tool，也不
需要新增依赖**（因此不动 `pyproject.toml` / nuitka / pyinstaller）。凭据是**用户自己的**，
存进 `~/.hermes/.env` 或系统凭据库，**绝不打印、绝不粘进聊天、绝不提交进 git**。

默认用中文回答，除非用户明显用别的语言。

> 这是一份精炼操作指南，**不是唯一事实来源**。CLI 迭代很快，给"不支持/没有"这类否定结论前，
> 先以真机 `hermes --help` / `hermes <command> --help`、`~/.hermes/config.yaml`、官方文档
> （<https://hermes-agent.nousresearch.com/docs/>）和仓库 <https://github.com/NousResearch/hermes-agent>
> 为准。

## 何时用

- 用户要装 / 起步 Hermes，或问"怎么跑起来 / 第一次配置"。
- 配置推理 provider：OpenRouter、Anthropic 原生、OpenAI、Ollama、vLLM、自定义端点、
  多凭据轮询池、fallback / 路由。
- 管理配置文件（`~/.hermes/config.yaml` 与 `~/.hermes/.env`）。
- 启用工具 / 技能 / MCP server / webhook / cron / gateway（多平台消息网关）。
- 排错（`hermes doctor`、连不上模型、工具调用不生效、上下文太短等）。
- **扩展或贡献**：加自定义工具 / 技能、写插件、跑测试、提 PR。

## 安装与起步

```bash
# 官方安装脚本（装 uv、Python、venv、launcher）
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# 或用 PyPI
pip install hermes-agent          # 或 uv pip install hermes-agent

hermes                            # 交互式聊天（默认界面）
hermes chat -q "帮我总结这段话"    # 单次提问
hermes setup                      # 配置向导
hermes model                      # provider / 模型选择器（终端里跑，非会话内）
hermes doctor                     # 健康检查（--fix 尝试自动修）
```

先确认"一次干净的对话能跑通"，再叠加 gateway / cron / skills / voice / 路由等功能。
若要跑 Ink TUI 而非 CLI，把 `config.yaml` 的 `display.interface` 设成 `tui`。

其它入口：`hermes desktop`（原生桌面 app，别名 `hermes gui`）、`hermes dashboard`
（网页管理面板 + 内嵌聊天）、`hermes proxy`（OpenAI 兼容的本地代理）。

## 关键路径与配置分层

| 路径 | 用途 |
|------|------|
| `~/.hermes/config.yaml` | 主配置：模型、provider、端点、显示、功能开关（**非机密**） |
| `~/.hermes/.env` | 机密：API key、token（**机密只放这里**） |
| `~/.hermes/hermes-agent/` | 若是 git 方式安装，源码在此 |

原则（框架自身的硬规矩）：**config 值放 `config.yaml`，机密放 `.env`**；`.env` 里的
`LLM_MODEL` 已废弃；`OPENAI_BASE_URL` 只对 `openai-api` provider 生效。

查看 / 编辑配置：

```bash
hermes config                 # 查看当前配置
hermes config path            # 打印 config.yaml 路径
hermes config env-path        # 打印 .env 路径
hermes config edit            # 用 $EDITOR 打开 config.yaml
hermes config set KEY VALUE   # 设单个键
hermes config check           # 校验配置
hermes config migrate         # 迁移旧格式
```

## 配置推理 Provider

`hermes model` 是从终端跑的**完整向导**（加 provider、跑 OAuth、填 API key、配端点）；
会话内的 `/model` **只能在已配好的 provider/模型间切换**，不能新增。

**OpenRouter（默认 provider）** — 在 `.env` 里设 `OPENROUTER_API_KEY`，可配路由：

```yaml
# config.yaml
provider_routing:
  sort: "throughput"          # "price"(默认) | "throughput" | "latency"
  # only: ["anthropic"]        # 只用这些 provider
  # ignore: ["deepinfra"]      # 跳过这些
  # order: ["anthropic", "google"]  # 按序尝试
  # require_parameters: true   # 只用支持全部请求参数的 provider
  # data_collection: "deny"    # 排除可能存/训练数据的 provider
```
模型名后缀快捷方式：`:nitro`=按吞吐排序，`:floor`=按价格排序。

**Anthropic 原生** — 三种鉴权：OAuth（需 Claude Max + 额度）、`ANTHROPIC_API_KEY`、
或手动 setup-token。

```bash
export ANTHROPIC_API_KEY=***          # 机密最终应落到 ~/.hermes/.env
hermes chat --provider anthropic --model claude-sonnet-4-6
hermes model                          # 推荐：可复用 Claude Code 的凭据库
```
```yaml
# config.yaml
model:
  provider: "anthropic"
  default: "claude-sonnet-4-6"        # default: 与 model: 等价，两种写法都行
```
别名 `claude` / `claude-code` 是 `anthropic` 的简写。

**OpenAI 直连** — `.env` 设 `OPENAI_API_KEY`，provider 用 `openai-api`，可选 `OPENAI_BASE_URL`
（base URL `https://api.openai.com/v1`）。

**Ollama（本地）** — 走自定义端点，无需 key：

```yaml
model:
  default: qwen2.5-coder:32b
  provider: custom
  base_url: http://localhost:11434/v1
  context_length: 64000
```
⚠️ 坑：Ollama 默认上下文很小，Hermes 至少要 64000 token，**要在服务端设**，不是走 API：
```bash
OLLAMA_CONTEXT_LENGTH=64000 ollama serve
```

**vLLM（GPU 部署）** — 工具调用必须带下面两个 flag，否则模型会把 tool call 当普通文本吐出来：
```bash
vllm serve meta-llama/Llama-3.1-70B-Instruct \
  --port 8000 --max-model-len 65536 --tensor-parallel-size 2 \
  --enable-auto-tool-choice --tool-call-parser hermes
```
然后 Hermes 指向 `http://localhost:8000/v1`（同上 custom provider 写法）。

**命名自定义 provider** —（会话内用三段式 `/model custom:local:qwen-2.5` 切换）：
```yaml
custom_providers:
  - name: local
    base_url: http://localhost:8080/v1
  - name: work
    base_url: https://gpu-server.internal.corp/v1
    key_env: CORP_API_KEY
    api_mode: chat_completions
```
⚠️ 安全：本地自定义端点通常**无鉴权**。任何 bind 到 `0.0.0.0` 的端点会被同网段其它主机直接
访问。除非确实要暴露，否则 bind 到 localhost 或用防火墙挡住端口。

**Fallback（模型不可用时中途无损切换）**：
```yaml
fallback_providers:
  - provider: openrouter
    model: anthropic/claude-sonnet-4
  - provider: anthropic
    model: claude-sonnet-4
```
交互式配：`hermes fallback`。

**凭据与轮询池** — 同一 provider 配多个 key 会组成**自动轮询池**，跳过用尽的 key：
```bash
hermes auth                       # 查看鉴权状态
hermes auth add [PROVIDER]        # 加一个（如 anthropic --type oauth / codex-oauth / xai-oauth）
hermes auth list
hermes auth remove PROVIDER INDEX
hermes auth reset PROVIDER
```

## 工具 / 技能 / MCP / Webhook / Cron

```bash
# 工具
hermes tools list / tools enable NAME / tools disable NAME

# 技能（可从 hub ID 或直接 …/SKILL.md URL 装；--name 覆盖缺失的 frontmatter name）
hermes skills list / search QUERY / install ID / inspect ID
hermes skills check / update / uninstall N / publish PATH / browse
hermes skills tap add REPO        # 把一个 GitHub 仓库加为技能来源

# MCP server
hermes mcp add NAME --url ... | --command ...
hermes mcp list / test / remove / configure / install <name>

# Webhook（路由在 /webhooks/<name>）
hermes webhook subscribe N / list / remove NAME / test NAME

# Cron（调度接受 '30m' / 'every 2h' / '0 9 * * *'）
hermes cron list / create SCHED / edit ID / pause ID / resume ID / run ID / remove ID / status
```

## Gateway（多平台消息网关）

```bash
hermes gateway setup
hermes gateway run                # 前台跑
hermes gateway install / start / stop / restart / status
```
支持 20+ 平台：Telegram、Discord、Slack、WhatsApp、iMessage（Photon，`hermes photon setup`）、
Signal、Email、SMS、Matrix、Teams、LINE 等，多数适配器在 `plugins/platforms/` 下。

## Profile / 会话 / 其它

```bash
hermes profile list / create NAME [--clone|--clone-all|--clone-from] / use / delete / show
hermes sessions list / browse / export OUT / rename ID T / delete ID / prune --older-than N / stats
hermes --resume SESSION   |  --continue [NAME]   |  --worktree   |  --profile NAME  |  --skills SKILL  |  --yolo
```
`--worktree/-w` 用独立 git worktree 跑并行 agent；`--yolo` 跳过危险命令确认（谨慎）。

## 扩展与贡献

仓库 <https://github.com/NousResearch/hermes-agent>；文档 <https://hermes-agent.nousresearch.com/docs/>；
技能 hub `agentskills.io`。git 安装的源码在 `~/.hermes/hermes-agent/`。

- **加自定义工具/技能**：本地写工具需带 `check_fn`；所有路径统一用 `get_hermes_home()`，
  别硬编码。命令入口在 `hermes_cli/main.py`，slash 命令注册表在 `hermes_cli/commands.py`。
- **agent loop（高层）**：拼 system prompt → 循环（调 LLM、经 `handle_function_call()` 分发
  tool_calls、回填结果，或返回文本）→ 接近 token 上限时自动压缩上下文。
- **system prompt 环境块**由 `agent/prompt_builder.py::build_environment_hints()` 产出。不变式：
  用远程终端后端（docker/singularity/modal/daytona/ssh/managed_modal）时会隐藏宿主信息，
  所有文件工具在后端容器内执行。
- **跑测试**（用官方 runner 保证与 CI 一致：干净环境、清空凭据、TZ=UTC、xdist 并行；
  会把 `HERMES_HOME` 重定向到临时目录）：
  ```bash
  scripts/run_tests.sh
  scripts/run_tests.sh tests/tools/
  scripts/run_tests.sh tests/tools/test_x.py
  scripts/run_tests.sh -v --tb=long
  ```
  跨平台守卫用 `@pytest.mark.skipif`（symlink、POSIX 文件权限、`signal.SIGALRM`、Windows 专属）。
  ⚠️ 只 monkeypatch `sys.platform` 不够——要连 `platform.system` / `platform.release` /
  `platform.mac_ver` 一起 patch，因为它们会重读真实 OS。
- **提交约定**：`fix:` / `feat:` / `refactor:` / `docs:` / `chore:`。
- **硬规矩**：绝不破坏 prompt 缓存；消息角色必须交替（不能连续两条 assistant 或两条 user）。

## 排错

- 起不来 / 配置有问题：先 `hermes doctor`（`--fix` 尝试自动修）、`hermes config check`、
  `hermes status --all`。
- 连不上模型：确认 `.env` 里 key 存在且未过期、`config.yaml` 的 provider/base_url 正确；
  自定义/本地端点确认服务在跑、context ≥ 64000。
- 工具调用被当文本输出（本地模型）：vLLM 要带 `--enable-auto-tool-choice --tool-call-parser hermes`。
- 命令不确定：一律以 `hermes --help` / `hermes <command> --help` 真机为准，别凭记忆下否定结论。

## 与 claude_code / codex 的关系

三者都是"自主编码/对话 agent"（同属 `autonomous-ai-agents`）。区别：
`claude_code`（本 workspace 的 tool）和 `codex`（skill）是把编码任务**委派**给外部 CLI；
本技能是教用户**配置、扩展、贡献 Hermes Agent 这个框架本身**。若用户是要把编码活外包给某个
agent，用 claude_code / codex；若是要搭建 / 调 / 改 Hermes，用本技能。

