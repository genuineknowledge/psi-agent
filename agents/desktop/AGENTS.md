# agents/desktop — 桌面版 (ToC) 能力包 (海豚 / Haitun agent 🐬)

A consolidated psi-agent workspace. Its persona is fixed: a **Haitun agent** (always stated
in the system prompt). It merges the most useful parts of the other example workspaces:

- **Prompt engine** — a layered builder (system prompt + per-turn context block, skills
  index, bootstrap context files), with **all configuration kept inside this
  workspace** (there is no global config directory). The prompt is built once per Session
  and reused byte-for-byte; the clock and the runtime line are re-rendered **every turn** by
  `turn_context_builder()` and delivered at the *tail* of the request, on the turn's own user
  message, so staying current leaves the prompt and every earlier turn untouched. `USER.md` and the dynamic
  context files stay in the prompt and trigger a rebuild only when their **content** changes.
- **Workflow** — `workflow` hosts the formal-language workflow system
  defined by `FusionFlow.g4`; `workflow_graph` stores checked Step–Artifact
  structure, `workflow_execution` executes inspectable plans, and the workspace
  runner dispatches Agent and Program Steps plus resumable Human waits.
  Node/Fuclaw `fusion-flow-legacy` + `flow_run` remains an explicit `.flow.ts` fallback.
  `flow_manage` supports both and prefers G4 assets.
- **Skills + file tools** — the full hermes-skills domain skill set plus selected curated
  skills, on top of clean async file/shell tools.

## No global config

**Nothing is read from `~/` — there is no global config directory.** The agent's identity,
user profile, and bootstrap files all live at the workspace root:

| File | Role |
|---|---|
| `SOUL.md` | Personality/values; augments the built-in Haitun agent identity (top of prompt). |
| `USER.md` | User profile; injected into the system prompt. Edit it and the prompt is rebuilt on the next turn. |
| `IDENTITY.md` | Haitun identity details; loaded as a bootstrap context file. |
| `TOOLS.md` | Local, environment-specific notes; bootstrap context file. |
| `BOOTSTRAP.md` | First-run onboarding. **Delete it** to skip onboarding. Triggers the "Bootstrap Pending" section while present. |
| `HEARTBEAT.md` | Dynamic context. Picked up on the next turn after its **content** changes (`system_prompt_rebuild_checker()` compares a digest), not re-rendered on every turn. |
| `AGENTS.md` | This file; also loaded as a bootstrap context file. |

## 出厂内容与用户数据的边界 (ToC 独有, 尚未落地)

**判据候选是「谁有写权」。** 安装器写的算出厂内容, 用户与 agent 自己写的算用户数据。
ToB 没有安装器, 结构上不存在这个问题 —— 这一节只对 ToC 的安装形态成立。

| 类 | 内容 | 谁写 |
|---|---|---|
| 出厂内容 | `systems/` `tools/` `skills/` `triggers/` `channel_events/` `bin/` `config/` `docs/` `flows/`, 以及 `AGENTS.md` / `IDENTITY.md` / `TOOLS.md` / `BOOTSTRAP.md` / `HEARTBEAT.md` 这些提示词模板 | 安装器 (每次安装覆盖为本版内容) |
| 用户数据 | `SOUL.md` `USER.md` `schedules/` | agent 自己改写 / 用户积累 / `schedule_manage` 写 |

**当前状态: `.iss` 里这两类仍混在同一条通配 `Source` 里, 结构上分不出来。**
按上表把 `[Files]` 拆成两组 `Source` 的改法试过一次又撤回了 —— 它牵动升级时的保数据语义,
归属讨论后单独开 PR, 不属于架构重排。讨论项见
`docs/superpowers/specs/2026-08-28-gateway-workspace-refactor-report.md` 第九章。

**为什么不能只拆 `Source` 就算完**: `{app}\app` 会被 `[Code]` 段的 `SwapComponent('app')`
整目录换掉, 所以光把三项单列出来、`Flags` 不变的话, 升级时用户数据的存活情况一点没变 ——
真正要定的是保护策略, 不是清单。

**这里还有一处结构性的不一致, 本轮未改**〔实测〕: 提示词读 `SOUL.md` / `USER.md` 用的是
**agent 包根** (`System.__init__` 把 `self._agent_dir` 传给 `_load_soul_md` /
`_build_volatile`), 而 `write` / `edit` 这些工具的相对路径落在**用户 workspace**
(`_runtime_paths.resolve_user_path`)。装机形态下两个根不是一个目录 (`--default-agent {app}`
对 `--default-workspace {Desktop}\haitun交付`), 于是 agent「改写自己的 SOUL.md」写出去的
那份**不会被下一轮提示词读到** —— 它落在 workspace, 提示词读的是包根。想让自我改写真正
生效, 得先决定 `SOUL.md` / `USER.md` 归哪个根, 那是一个行为变更, 不属于本步的分类落位。

## Fusion Memory

Desktop Fusion Memory is embedded in the existing Session process. Its durable scope is the normalized absolute workspace path, hashed as `workspace_id`: Sessions in that workspace share evidence, and other workspaces cannot read it. Files default to `<workspace>/.fusion-memory/evidence.jsonl` and `<workspace>/.fusion-memory/memory.sqlite3`.

JSONL is the append-only authority for raw `evidence_span` and `scope_clear` records. SQLite contains only `evidence_spans`, `memory_items`, `summary_cards`, `ingest_checkpoints`, and the `fts_memory` virtual table, and can be rebuilt from JSONL. Ingestion keeps only ordinary chat user/assistant visible text confirmed by the successful module-level `system_after_turn` hook and never modifies psi-agent history. It deliberately does not guess or backfill older history rows that lack finish provenance.

Do not add an MCP service, sidecar, watcher, daemon, subprocess, or model server. Model and SQLite errors must not fail a completed chat. `memory_search` returns raw evidence, `memory_answer_context` returns bounded evidence-grounded context, and `memory_add` promotes existing source IDs only.

Embedding and rerank read `DASHSCOPE_API_KEY` only. LLM extraction uses `FUSION_MEMORY_MODEL_*`, or the complete `PSI_AI_PROVIDER`/`PSI_AI_MODEL`/`PSI_AI_API_KEY`/`PSI_AI_BASE_URL` group. Credentials are launcher-managed and never persisted in memory files.

## Runtime display and service credentials

The following optional variables either change runtime display metadata or enable their named
service tools:

| Variable | Purpose |
|---|---|
| `HAITUN_MODEL` | Override the model name shown in the runtime line. |
| `HAITUN_AGENT_ID` | Agent ID shown in the runtime line. |
| `HAITUN_CHANNEL` | Channel name shown in the runtime line. |
| `TZ` | Standard IANA time zone for the date/time section, e.g. `Asia/Shanghai` (when unset, follows the system's local time zone). Also the zone scheduled-task cron fields and `once_at` are interpreted in — a UTC base image serving Beijing users must set this, or reminders resolve against the wrong clock. |
| `HAITUN_KNOWLEDGE_CUTOFF` | Knowledge-cutoff anchor stated in the date/time section, e.g. `2026-01`. When unset the section says `unknown` and tells the agent to verify anything recent online — it never invents a date. Set it so the agent knows where its memory stops. |
| `XFYUN_STT_APP_ID`, `XFYUN_STT_API_KEY`, `XFYUN_STT_API_SECRET` | iFLYTEK streaming STT credentials. |
| `XFYUN_TTS_APP_ID`, `XFYUN_TTS_API_KEY`, `XFYUN_TTS_API_SECRET` | iFLYTEK online TTS credentials. |
| `XFYUN_APP_ID`, `XFYUN_API_KEY`, `XFYUN_API_SECRET` | Optional shared fallback when both services use one app. |

## Channel events: 本能力包没有

`channel_events/` 整个目录**不在** `agents/desktop` 里 (ToB 有 41 个文件)。事件源是飞书
平台推送 + 本 agent 合成两类, `source` 枚举里除 `haitun` 外全是聊天平台; 桌面版是本机
单用户直接对话, 没有「平台把事件推给我」这个形态。

`triggers/` 与 `trigger_manage` 保留了 —— 定时任务和触发器机制本身是通用的, 只是没有
飞书那一路信号源。ToB 版这一节讲的 `source` / `event` 两层设计与注册改哪一层, 对本能力包
用不上, 故未照抄; 原文在 `agents/feishu/AGENTS.md`。

## Tools (`tools/`)

### Path roots（workspace / agent ContextVar + AppData）

当 Session `agent ≠ workspace` 时，工具必须分清两根目录。统一入口：
`tools/_runtime_paths.py`（也经 `_session_helpers.current_workspace` /
`current_agent` 暴露）。AppData（todos / history / Gateway state）经
``psi_agent._appdata`` / ``resolve_appdata_root()``，**不**进 ContextVar。

| 解析 API | 优先顺序 | 典型用途 |
|----------|----------|----------|
| `workspace_dir()` / `resolve_workspace()` | 显式参数 → `get_workspace()` → `WORKSPACE_DIR` → 本包父目录 | 相对路径读写、`bash`/`powershell` cwd、`schedules/`、`flows/`、feishu UAT |
| `agent_dir()` / `resolve_agent()` | 显式参数 → `get_agent()` → 回落 `workspace_dir()` | `skills/`（`skill_manage`） |
| system prompt「Workspace」段 | `system_prompt_builder` 经 `get_workspace()` 注入用户打开目录（**刻意为之**：勿用 `__file__` 当文件 IO 根，否则 agent≠workspace 时模型会把产出写进能力包） | 引导模型相对路径 / `[SEND:]` 落在用户工作区 |
| `resolve_user_path(path)` | 相对 → 拼到 workspace；绝对路径原样 | `read` / `write` / `edit` / `list_dir` / `find_files` |
| AppData todos（第 4B） | `resolve_appdata_root()` → `{appdata}/todos/{session_id}.json`；读时双读 legacy `{workspace}/.psi/todos/` | `todo` tool / Gateway `GET …/todos` |
| AppData todo segments | 同根 → `{appdata}/todos/{session_id}.segments.json`（`merge=false` 开新段） | spa-v2「任务历史」/ `GET …/todo-segments` |
| AppData history（第 4C） | 同上根 → `{appdata}/histories/{session_id}.jsonl`；读时双读 legacy `{workspace}/histories/` | Session JSONL / `sessions_list` / `GET …/history` |
| AppData Gateway state（第 4D） | 同上根 → `{appdata}/state/latest.json`；读时双读 cwd `state/latest.json` | Gateway 重启恢复 AI/Session/Title |

**刻意为之**：AppData 路径用 `platformdirs` / `--appdata` / `PSI_APPDATA`，禁止手写死 `%AppData%`；不把 AppData 塞进 Session ContextVar。

| Tool | Notes |
|---|---|
| `profile_update` | Manually update the workspace-local topic-aware learner profile; successful `finish_reason="stop"` turns are aggregated automatically by `system_after_turn`. Only per-topic dimensions and statistics are persisted, not raw transcripts. This profile is keyed by workspace, not by channel user identity. |
| `bash` | Shell commands (anyio, Windows-aware bash detection). On Windows the installer bundles MSYS2 at `{app}\msys64`, added to PATH by the launcher, so bash works out-of-the-box. **cwd = workspace**. |
| `powershell` | Windows-native shell. **默认 cwd = workspace**. |
| `read` / `write` / `edit` | Async file ops；相对路径相对 **workspace**. |
| `list_dir` / `find_files` | List one directory level; recursively find files by glob (`**/*.py`), sorted newest-first；默认根为 **workspace**. |
| `write_excel` | Build a real `.xlsx` from a 2D array (bold header, column-width fitting). |
| `write_word` | Build a real `.docx` from structured blocks (headings/paragraphs/tables); sets the East-Asian font (`w:eastAsia`) on every style so Chinese text isn't "字体不齐". |
| `skill_manage` | CRUD on **agent** `skills/<name>/SKILL.md`（经 `get_agent()`）。**先 list 再 create**：同类 skill 已存在则 `patch`，禁止平行新建。`patch` 允许 `created_by: agent` 或 `agent_editable: true`（如 `feishu-resume-review`）。判定/写法：`skill-authoring-when` / `skill-authoring-how`（**先于**自进化落库）。 |
| `flow_manage` | CRUD + promote on workflow assets under **workspace** `flows/`; prefers `.workflow` / `.g4` over `.flow.ts`. |
| `run_flow` / `run_flow_resume` | Execute Workflow plans. Runs without Human Steps finish in the initial call; Human Steps return a checkpointed request that resumes only through `run_flow_resume`. |
| `flow_run` | Legacy Node/Fuclaw `.flow.ts` runner retained for explicit fallback use. |
| `trigger_manage` | CRUD on **agent** `triggers/<name>/TRIGGER.md`。`event` 名应对齐 agent ``channel_events/`` 已接通能力；Session 不再用 catalog 硬拒。`fire=tool` 命中后直调工具。见 `skills/feishu-event-remind`；事件定义见 ``channel_events/README.md``。 |
| `haibao_list_datasets` / `haibao_ask` | Bundled Haibao MCP Adapter tools for real business-data queries. They require an operator-provisioned private MCP server; no private server or database onboarding is bundled. |
| `search` (`search.py` + `_mcp.py`) | Serper web search via MCP. Requires the `mcp` extra and `uvx serper-mcp-server`. **`serper_google_search` 常驻**（普通网页搜索，唯一有实测流量的）；图片/地图/学术/专利/新闻/购物/评论等 12 个垂直搜索走 **`serper_call(tool, args_json)`**，参数表在 `serper-mcp` 技能里。 |
| `x_search` (`x_search.py` + `_x_search_impl.py`) | Search recent public posts on X (Twitter) via the X API v2 recent-search endpoint (last ~7 days). `x_search(query, max_results, sort_order)` supports X search operators (`from:`, `#tag`, `"phrase"`, `lang:`, `-is:retweet`). Uses `aiohttp` (already a core dep), no extra packages. Requires `X_BEARER_TOKEN` (X API v2 App-only OAuth 2.0 bearer token). |
| `canvas` (`canvas.py` + `_canvas_impl.py` + `_mcp.py`) | 共享的 Excalidraw 实时画布（架构图/流程图/思维导图/线框图）。**26 个能力全部经 `canvas_call(tool, args_json)`**，一个常驻工具，参数表在 `canvas-mcp` 技能里。画布状态存在 canvas 服务器里、跨调用保留；截图和 mermaid 渲染要用户打开 `http://127.0.0.1:3000`。Requires Node.js/`npx`。**已知问题**：`describe_scene` / `query_elements` 在刚建元素后可能回空（直调 MCP 也一样，非派发引入）。 |
| `browser` (`browser.py` + `_browser_impl.py` + `_mcp.py`) | Browser automation via Playwright MCP driving the system browser (Edge). **六个高频工具常驻** —— `browser_navigate` / `browser_snapshot` / `browser_click` / `browser_type` / `browser_tabs` / `browser_take_screenshot`（实测 146 个会话里 ≥8 次调用的全部）。**其余 35 个走 `browser_call(tool, args_json)`**，参数表在 `browser-mcp` 技能里（生成的，别手改）。上游 schema 不由我们写，只能选暴露几个：42 个全常驻要吃 26% 的工具上下文。One long-lived `npx @playwright/mcp` server with `--shared-browser-context` keeps page state across calls. Requires Node.js/`npx`. |
| `browser_cdp` (`browser_cdp.py` + `_browser_cdp_impl.py`) | Send a **raw Chrome DevTools Protocol** command to a browser — the escape hatch for anything the `browser_*` tools don't wrap (any CDP domain: `Page.*`, `Network.*`, `Emulation.*`, `Runtime.*`, `Browser.*`, `Target.*`, …). `browser_cdp(method, params, target="page"/"browser", timeout_s)` where `params` is a **JSON object string** (e.g. `'{"url": "https://example.com"}'`, empty for no-arg methods); returns the raw CDP result JSON. Launches a **dedicated** debug browser (Edge, then Chrome, with `--remote-debugging-port` + isolated profile — separate from the Playwright MCP browser) on first use and reuses it, or connects to an existing browser when `CDP_ENDPOINT` is set. CDP is JSON-over-WebSocket; uses `aiohttp` (already a core dep), no extra packages. |
| `speech_to_text` | iFLYTEK streaming STT for WAV/PCM/MP3 files received through `[RECV:]`. |
| `text_to_speech` | iFLYTEK online TTS; creates MP3 files delivered through `[SEND:]`. |
| `computer_use` | Apple toolset. Drive the macOS desktop in the background (screenshot/click/type/scroll/drag) via the `cua-driver` CLI — no cursor/focus/Space theft. macOS only; needs `cua-driver` installed + Accessibility & Screen Recording permissions. See `skills/macos-computer-use/`. |
| `llm_wiki` (`llm_wiki.py` + `_llm_wiki_impl.py`) | Build/query an interlinked Markdown knowledge base (Karpathy's "LLM wiki" pattern): compile knowledge into durable, cross-referenced pages under `<workspace>/wiki/` instead of re-searching from scratch. Tools `wiki_write`, `wiki_read`, `wiki_search`, `wiki_list`, `wiki_links`, `wiki_delete`. Each page has YAML frontmatter (title/tags/timestamps/aliases) + a body linking others with `[[wikilink]]`; `wiki_links` reports back-links & broken links. Async `anyio` file IO + `pyyaml` frontmatter, both already core deps — no extra packages. |
| `todo` (`todo.py` + `_todo_store.py`) | **本 Session 执行步骤清单**（非跨会话 goal、非外部看板）。权威约定：`skills/task-planning/SKILL.md` — 有分拆价值才写；**写即承诺随进程维护**（禁止建表复读后空过不勾就当结束；仅计划则须声明且 status 诚实）。禁止为 UI 进度凑装饰清单。`todo()` 读；`todo(todos='[...]')` 写（`content` 必须是字符串）；`merge=true` 按 id 更新。自指 content（更新清单/回复用户等）仍写入但返回 `warnings[]`（软劝，不硬失败）。落盘 AppData `todos/{session_id}.json`（legacy `.psi/todos` 双读）。Gateway `GET …/todos` / spa-v2 `N/M` **只消费**已有清单；spa 回合后若仍有 `in_progress` 仅 toast（不自动改状态）。 |
| `goal` (`goal.py` + `_goal_impl.py`) | Define and track **high-level goals** for the agent — durable intent that outlives one task (e.g. "ship payments v2", "reach 90% coverage"), which neither `todo` (one session's steps) nor the `taskflow` skill (a task/project board) captures. Tools `goal_set`, `goal_progress`, `goal_get`, `goal_list`, `goal_delete`. Each goal is a Markdown file under `<workspace>/goals/` with YAML frontmatter (title/slug/status[active,paused,achieved,abandoned]/priority/progress 0-100/target_date/tags/timestamps) + an append-only progress `log`, and a body that links related/sub-goals with `[[slug]]`. `goal_progress` records a dated log entry and moves %/status (100% ⇒ achieved); `goal_list` rolls up status counts. Async `anyio` file IO + `pyyaml` frontmatter, both already core deps — no extra packages. |
| `clarify` | Ask the user a question when you need clarification, feedback, or a decision before proceeding. Two modes: multiple choice (up to 4 `options` + an auto-appended "Other" free-text) or open-ended (omit `options`). Returns a formatted question block to show the user; then **end the turn** and wait — the reply arrives as the next message (the runtime has no blocking-input primitive). Pure-Python, no extra deps. |
| `c_drive_cleanup` (`c_drive_cleanup.py` + `_c_drive_cleanup_impl.py`) | Windows C-drive `scan` / `status` / `clean` tool. The first scan in a Session requires confirmation; cleanup requires the user's affirmation and deletes only unchanged candidates from allowlisted temporary/cache locations. Large files, exact duplicates, and stale Downloads are report-only. See `skills/windows-c-drive-cleanup/SKILL.md` for the agent workflow. |

本表比 ToB 版少 27 行: 飞书那批工具与依赖飞书身份的组织记忆 / `assignment_*` /
`handbook_onboarding_*` / `channel_event_check` 都不在本能力包里。原表在 `agents/feishu/AGENTS.md`。

## Skills (`skills/`)

- `_universal` — always-relevant working discipline.
- `skill-authoring-when` — **whether** to create/patch（复用价值门 + **先 list，有同类则 patch，无则 create**；自进化前同样遵守）。
- `skill-authoring-how` — **how** to write body and call `skill_manage`（禁止 raw `write` under `skills/`）。
- The hermes domain skill set (cryptanalysis, image-segmentation, ml-inference, …).
  `python-static-analysis`, `user-preferences-and-language`, `example-skill`).
- `task-planning` — **何时必须 / 禁止**用 `todo` 拆步；**建表即承诺维护**（推进配方 + 禁止空表收工）；spa/Gateway 进度 UI 只消费结果，不定义策略。
  撤除脚手架，直到员工能独立交付；本地 DOCX 使用 `read_document`，其余能力组合现有工具与 Skill。
- `haibao` — bundled real business-data query workflow for the two Haibao MCP Adapter tools;
  requires the separately operated private server.
- `speech-to-text` / `text-to-speech` — iFLYTEK voice input/output recipes.
- `gif-search` — search & download animated GIFs/stickers from a hosted GIF API (Giphy; `api.giphy.com`) with `curl` + `jq` (via `bash`); `media` category, shell-only, no extra deps. Delivers files via `[SEND:]`; needs `GIPHY_API_KEY`. Note: Google's Tenor API was shut down 2026-06-30, so this uses Giphy, not Tenor.
- `github-auth` — GitHub authentication setup (HTTPS PAT, SSH keys, `gh` CLI login); shell-only, no extra deps.
- `github-code-review` — review GitHub PRs with the `gh` CLI (via `bash`): overview, diff, read/write inline and top-level comments. Complements `github-auth`.
- `github-issues` — create, triage, label, assign, comment on, and close GitHub issues with the `gh` CLI / `gh api` (via `bash`); shell-only, no extra deps. Complements `github-auth`.
- `llm-wiki` — build/maintain a self-growing, interlinked Markdown knowledge base (Karpathy's "LLM wiki" pattern): compile knowledge into durable, cross-referenced pages under `<workspace>/wiki/` (YAML frontmatter + `[[wikilink]]` body) instead of re-searching raw sources. `coding` category; pure conventions over the existing `read`/`write`/`edit`/`find_files`/`search_content`/`bash` tools — no dedicated tool, no extra deps.
- `macos-computer-use` — drive native Mac apps in the background via `computer_use` (`cua-driver`).
- `apple-notes` — manage Apple Notes from the terminal via the `memo` CLI (list/search/view/create/edit); shell-only, macOS + Homebrew `memo`.
- `apple-imessage` — send/receive iMessages & SMS via the `imsg` CLI (`bash`-driven, macOS only; needs `imsg` + Full Disk Access & Messages Automation). No dedicated tool.
- `opencode` — delegate coding & PR review to the OpenCode CLI (`opencode run` / `opencode pr`, non-interactive with `--auto`); autonomous-ai-agents category, `bash`-driven, needs `opencode` installed + authenticated. No dedicated tool, no extra deps.
- `claude-code` — delegate a coding task (features, fixes, PRs) to Anthropic's Claude Code CLI headless (`claude -p`); shell-only via `bash`, no extra deps. Autonomous-AI-agents toolset.
- `codex` — Autonomous-AI-agents skill: delegate coding (features, fixes, PRs) to the OpenAI Codex CLI via `codex exec` through the `bash` tool; needs `codex` installed (`npm i -g @openai/codex`) + authenticated, no extra deps.
- `hermes-agent` — configure, extend, or contribute to Hermes Agent (Nous Research's open-source agent framework); `bash`-driven `hermes` CLI recipe covering install, providers (OpenRouter/Anthropic/OpenAI/Ollama/vLLM/custom + pools/fallback), config (`~/.hermes/config.yaml` + `.env`), tools/skills/MCP/gateway/cron, and repo/dev/test/PR conventions. `autonomous-ai-agents` category; no extra deps. No dedicated tool.
- `obsidian` — read/search/create/edit Markdown notes in an Obsidian vault (a folder of `.md` files with YAML frontmatter, `[[wikilink]]` backlinks, and `#tags`); uses the existing `read`/`write`/`edit`/`find_files`/`search_content`/`list_dir` + `bash` tools directly — no Obsidian app, no CLI, no extra deps. `knowledge-base` category; can act as the storage layer under `llm_wiki` (same frontmatter + `[[wikilink]]` convention). No dedicated tool.
- `simplify-code` — behavior-preserving cleanup of **recent** code changes by fanning out **3 parallel subagents** over the changed files: split the git diff into 3 disjoint buckets, delegate each to a background subagent (via the `subagent-orchestration` recipe), then merge their edits and re-verify against a baseline. `coding` category; composes existing `bash`/`read`/`edit`/`subagent_*` tools — no dedicated tool, no extra deps.
- `research-paper-writing` — write an ML research paper for NeurIPS / ICML / ICLR end to end (design the contribution → draft sections → revise → official-template LaTeX build → rebuttal / camera-ready); `research` category. Composes the existing `read`/`write`/`edit`/`bash` tools plus `arxiv` (verify related work) and `subagent-orchestration` (parallel section drafting) — no dedicated tool, no extra deps. LaTeX (`texlive`/`tectonic`) is driven through `bash` when producing the PDF; hard rule against fabricating results or citations.
- `ocr-and-documents` — extract text from PDFs / scans / images. Two tiers: (1) fast, free text-LAYER extraction with **PyMuPDF** (`import fitz`, already a core dep) for born-digital PDFs, and (2) high-accuracy **OCR + layout → Markdown/JSON** via the external **marker-pdf** CLI (`marker_single` / `marker`) for scanned/image-only PDFs. Decision rule: probe the PyMuPDF text layer first (instant, no models); only fall back to marker-pdf OCR when it's empty/garbled or the user needs layout-faithful Markdown/tables. `research` category; `bash`-driven. PyMuPDF needs nothing extra; **marker-pdf is a heavy external tool (PyTorch + Surya OCR model weights, optional GPU) installed on demand via `pip install marker-pdf` — NOT a bundled dependency**, so no pyproject / nuitka / pyinstaller changes. Read-only (extraction), not PDF editing.
- `task-self-check` — 发出「任务完成」类最终回复前的**静默**自查：核对工具调用、工具结果与最终输出是否一致，有没有静默漏项或降级。每个会以用户可见答复收尾的回合都应加载，不限于用户主动要求 review 时；自查过程不写进回复。
  `business_context_json`（业务类型、稳定业务 ID、发起人、当前状态等收件方 agent 独立处理所需事实）和
  `action_handlers_json`（按钮 `value.action` 到 handler 标识符的完整映射）。工具会把原卡片、发送来源、
  信封中的 `source` 是发卡方 Session / open_id 与接收目标，`card` 是原始完整卡片，
  Channel **只选择 handler，不直接执行 handler，也不绕过 LLM**。映射键和 handler 必须是无首尾空白的
  canonical 字符串；配置非空映射后，未知 action 必须得到
  `dispatch.matched=false` 和 `handler=null`；点击者 agent 不得臆造或执行未匹配 handler。只有未配置映射的
  v1/v2 snapshot 才回退到把 `action.value.action` / `action_id` 本身作为 handler；snapshot 缺失或损坏时
  必须 fail closed，不能假定它是旧卡片。首个回调留下持久 `.consumed` tombstone，后续进程/重启后的重复点击
  直接忽略（传 `multi_use=True` 时墓碑降为 per-action `{message_id}.{action}.consumed`，逐行各拒一次）。原卡片的只读“已选择”已经确认点击，回调 agent 不得再生成“你点击了…”或“我来处理/通知…”等过程文本；
  应先按匹配 handler 完成必要工具调用。成功且无额外必要信息时以零 assistant 文本结束，不得输出 `NO_REPLY`
  或成功确认；只有警告、部分失败、权限问题、未匹配 handler 或必要后续步骤才回复，且不得把失败说成成功。
  Gateway/workspace tool 必须使用同一根，推荐统一设置 `PSI_APPDATA`；未显式传 `--appdata` 且配了 `gateway_url`
  时，Channel 会经 `GET /defaults` 向 Gateway 现问该根（Gateway 只把 `PSI_APPDATA` 导出到自己进程，
  Channel 是兄弟进程继承不到），显式传参仍然优先。两者落在不同根时读不到快照，卡片会被整张换成通用
  「已提交」兜底卡且 `dispatch.matched=false`。
  按钮组/表单优先用旧版卡片；
  Card 2.0 不支持旧版 `action` 标签。按钮 `value` 必须包含明确动作名和稳定业务 ID（如 `request_id`），
  且不同按钮使用不同值；选择器/日期输入放进 `form` 后提交，让结果进入 `form_value`，不要依赖 SDK 1.2.0
  无法完整区分选项变化的 `standalone` 回调。**默认**每张卡片按 `message_id` 只接受首个有效操作，随后保留原卡片
  标题和正文，并把交互区替换为“已选择: <选项>”只读提示；再次收集输入必须发新卡片。**唯一例外是显式传
  `multi_use=True`**：消费粒度降到单个 `value.action`，勾一行只结那一行（渲染成 `● ~~文字~~` 并原地更新卡片）、
  其余行按钮保留，重复点同一行仍恰好被拒一次；每行 `action` 必须唯一且规范，没有可用 action id 的行退回整卡去重。
  工具成功后卡片已经对用户可见：若卡片已承载全部必要信息，本轮以零 assistant 文本结束，不得输出
  `NO_REPLY`、发送确认或重复卡片内容/按钮；若仍有卡片未承载的必要信息（风险、部分失败、必要后续步骤），
  则必须只回复这些信息。若卡片已发送但 snapshot 保存失败，工具返回
  `ok=false, sent=true, callback_context_saved=false`；必须告知这项必要的部分失败，且不要重发卡片造成重复。
- `workflow` — immutable Workflow skill for the formal G4 language and checked Step–Artifact plans.
- `flow` (`skills/fusion-flow-legacy/`) — immutable legacy Node/Fuclaw `.flow.ts` fallback.


本节比 ToB 版少 43 个 skill 条目: 飞书域技能 (30 个 `feishu-*`) 与依赖飞书身份的
合同 / 行政财务 / 组织记忆类技能都不在本能力包里。原文见
`agents/feishu/AGENTS.md`。
## Schedules (`schedules/`)

- Use `schedule_manage` to add / list / view / update / delete tasks instead of editing
  `schedules/<name>/TASK.md` by hand.
- `schedules/heartbeat/` uses `visibility: silent` so HEARTBEAT turns stay out of Web Console
  history and are not injected into the next chat SSE.
- **Schedules belong to the *workspace*, not to this agent package.** The Session loads
  `{workspace}/schedules/`, but **activation is per (session × schedule)**: every Session sees
  all entries, and only the ones its lists select actually fire — `--active-schedules a,b` for a
  named subset, `--active-schedules '*'` for everything, `--deactive-schedules x` to carve out
  entries (the blacklist wins). The default is empty, so a user session fires nothing; the
  per-workspace **scheduler session** spawned by Gateway `SchedulerManager` is activated with
  `'*'`. Each schedule must be activated by exactly one Session — otherwise one reminder
  would fire once per online session (a chat channel spawns one Session per user).
  Use `'*'` plus a blacklist rather than an enumerated whitelist when a session should own
  "everything except these": a whitelist cannot cover `TASK.md` files created after startup.
  Consequence: when this package is used as a **separate agent root** (`--agent` ≠
  `--workspace`), the `schedules/heartbeat/` shipped here is **not** loaded. Put schedules
  under the workspace if you need them to run. Single-root usage (`agent` ≡ `workspace`) is
  unaffected.
- `visibility: display` results are stashed to pending, but the scheduler session has no
  channel attached, so under Gateway they do not reach any user. Use `fire=tool` (e.g.
  `sessions_create`) for anything that must actually be delivered.

## Prerequisites

- **Haibao ChatBI**: The Adapter, `haibao_list_datasets` / `haibao_ask` tools, and Haibao Skill
  are bundled. They require an operator-provisioned private MCP server; that server, its OAuth
  configuration, credentials, core implementation, and database onboarding are not bundled.
  See [`docs/haibao-integration.md`](docs/haibao-integration.md). This is not a claim that a
  production service is deployed, and direct workspace-to-private-API calls remain prohibited.
  `HAIBAO_MCP_TOKEN` is process-global, so one Haitun process/workspace deployment is one
  configured Haibao principal and security boundary; it does not provide per-session identity
  forwarding. Never use one token/process for users who require distinct authorization. Deploy
  a separate Haitun process, container, or workspace with a distinct token per principal or
  distinct authorization cohort.


- **Workflow**: bundled Python parser/compiler and executor; no separate setup.
- **Fusion Flow Legacy**: Node.js / `npm` / `npx`. First use:
  `cd skills/fusion-flow-legacy && npm install`.
- **Serper search**: install psi-agent with the `mcp` extra and have `uvx` available.
- **Browser tools**: Node.js / `npx` (first run downloads `@playwright/mcp`) and a system
  browser (Edge by default). Optional env: `BROWSER_CHANNEL` (`msedge`/`chrome`),
  `BROWSER_HEADLESS` (`1`/`0`), `BROWSER_CAPS` (default `vision,devtools`),
  `BROWSER_MCP_PACKAGE`, `BROWSER_STARTUP_TIMEOUT`, `BROWSER_PROFILE_DIR` (browser profile
  location; defaults to a stable per-user cache dir so cookies/logins survive restarts),
  `BROWSER_HEALTH_TIMEOUT` (seconds to wait for the server health probe, default `5`). If Node is
  missing the `browser_*` tools are skipped at load time (logged), not fatal. A server that died or
  went half-dead is detected and replaced on the next call, so the tools self-heal without a
  Gateway restart.
- **`browser_cdp` (raw CDP)**: a Chromium-family browser (Edge/Chrome) installed, **or**
  `CDP_ENDPOINT` pointing at a browser started with `--remote-debugging-port` (e.g.
  `http://localhost:9222`). No Node needed — it launches the browser directly and speaks
  CDP over a WebSocket with `aiohttp`. Optional env: `CDP_ENDPOINT`, `CDP_BROWSER_CHANNEL`
  (`msedge`/`chrome`), `CDP_HEADLESS` (`1`/`0`, default headed), `CDP_STARTUP_TIMEOUT`,
  `CDP_COMMAND_TIMEOUT`. If no browser is found the tool returns `ok=false` (not fatal).

## ⚠️ Intentionally-kept un-wired code (future extension)

psi-agent's session loader calls `system_before_turn()` (when defined, excluding `schedule.*`
turns), `system_prompt_builder()`, an optional `system_prompt_rebuild_checker()`, and
`turn_context_builder()`, `compact_history()`, and `system_after_turn()` after a committed
visible answer; it also loads `tools/*.py` and runs `schedules/*/TASK.md`. Before-turn advice
is ephemeral and must never enter history. Haitun's hook calls the isolated supervisor with an allowlisted payload
only: current question, hashed identities, profile/stage summary, map/heatmap summaries, and
prior advice. It must never supply main-answer text, reasoning, drafts, tool calls, or tool
results. The first eligible learning turn is warmed after the answer; subsequent eligible
turns may use a validated cache or live advice, and ordinary failures degrade to the normal
answer path. The supervisor workspace has no lifecycle hooks or tools, preventing recursion.

**The child-supervisor spawn is off by default** (`PSI_HAITUN_SUPERVISOR_CHILD`, unset = off).
`SupervisorManager.ensure_supervisor()` returns `None` before touching `_dependencies()`, so
no child process is started and no socket is waited on; `supervise()` degrades to
`empty_advice()` and `render_advice_prompt()` renders nothing for it. Measured in production
on 2026-09-01: 251 before-turn hook timeouts, 240 children started, **0** `handle ready` and
**0** `readiness check failed` — both mutually exclusive branches at zero, i.e. it never
reached a verdict, because `<agents-parent>/haitun-supervisor-workspace` is not deployed at
the path `ensure_supervisor` computes. The wait cost a fixed 30s per eligible turn (the
kernel's `system_before_turn` timeout), 33.7% of a short turn's p50, booked as
"unattributed" because it happens before the session lock. Re-enable only once that child
workspace is actually deployed **and** `wait_fn` has a real readiness predicate; the check is
that `handle ready` becomes non-zero. Pinned by
`tests/integration/test_haitun_supervisor_child_disabled.py`.

The following remain deliberately included as **future-extension hooks** and are **NOT**
invoked by the current framework — do not "clean them up" as dead code:

- `systems/system.py`: `System.compact_history()`, `System.after_turn()`, and the
  `_run_self_evolution_review` / self-evolution helpers. (The **module-level**
  `compact_history()` is a separate implementation — now re-exported from
  `psi_agent.session._compaction` — and *is* invoked on a compaction signal; the
  identically-named `System` method is not reached from it, and its four-layer heartbeat
  guards are only called from within that un-wired method.)
- `systems/curator.py`, `systems/background_review.py`, `systems/threat_patterns.py`,
  `systems/prompt_constants.py` — standalone modules from the hermes-style design, kept for
  when matching hooks are wired into the framework. They are not imported by `system.py`.

## Smoke test

```bash
uv run python agents/desktop/systems/system.py   # prints the assembled prompt
```
