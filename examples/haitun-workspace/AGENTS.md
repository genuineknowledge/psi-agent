# haitun-workspace (海豚 / Haitun agent 🐬)

A consolidated psi-agent workspace. Its persona is fixed: a **Haitun agent** (always stated
in the system prompt). It merges the most useful parts of the other example workspaces:

- **Prompt engine** — a layered builder (stable prefix + cache boundary + dynamic
  suffix, skills index, bootstrap context files), with **all configuration kept inside this
  workspace** (there is no global config directory).
- **Fusion Flow** — full workflow-authoring capability (`flow_manage`, the bundled node
  runtime under `skills/fusion-flow/`, the `bin/` stateful-session shim, the `flows/`
  layout, and authoring guidance injected into the prompt).
- **Skills + file tools** — the full hermes-skills domain skill set plus selected curated
  skills, on top of clean async file/shell tools.

**并行开发**：改本 workspace 时建议单独一棵 `git worktree` + 独立功能分支；勿与 spa-v2 前端施工共挂同一分支。约定见仓库根 `WORKTREE.md` 与 `AGENTS.md`（「本地并行开发」）。

## No global config

**Nothing is read from `~/` — there is no global config directory.** The agent's identity,
user profile, and bootstrap files all live at the workspace root:

| File | Role |
|---|---|
| `SOUL.md` | Personality/values; augments the built-in Haitun agent identity (top of prompt). |
| `USER.md` | User profile; injected into the dynamic suffix (below the cache boundary). |
| `IDENTITY.md` | Haitun identity details; loaded as a bootstrap context file. |
| `TOOLS.md` | Local, environment-specific notes; bootstrap context file. |
| `BOOTSTRAP.md` | First-run onboarding. **Delete it** to skip onboarding. Triggers the "Bootstrap Pending" section while present. |
| `HEARTBEAT.md` | Dynamic context, re-read every turn (below the cache boundary). |
| `AGENTS.md` | This file; also loaded as a bootstrap context file. |

## Remote Fusion Memory configuration

These optional launcher settings connect Haitun to an operator-provisioned remote Fusion Memory
MCP Streamable HTTP service. The bearer token is the only source of user identity: the same user
shares memory across sessions and workspaces by default, while different users/tokens are
isolated. Workspace and session IDs are context only, never client-supplied user identity. Keep
the token in deployment-managed secrets; never commit or log it. Haitun consumes this MCP service
only and must not use legacy REST routes.

| Variable | Purpose |
|---|---|
| `FUSION_MEMORY_MCP_URL` | Remote Fusion Memory MCP Streamable HTTP endpoint; TLS is terminated by its reverse proxy. |
| `FUSION_MEMORY_TOKEN` | Operator-issued bearer token for Fusion Memory. Keep it in deployment-managed secrets; never commit or log it. |
| `FUSION_MEMORY_WORKSPACE_ID` | Memory workspace context (defaults to `haitun`). |
| `FUSION_MEMORY_SESSION_ID` | Optional memory session context supplied by the launcher. |

Before calling any memory tool, obtain the user's explicit consent. Server provisioning and token
creation are operator actions.

## Runtime display and service credentials

The following optional variables either change runtime display metadata or enable their named
service tools:

| Variable | Purpose |
|---|---|
| `HAITUN_MODEL` | Override the model name shown in the runtime line. |
| `HAITUN_AGENT_ID` | Agent ID shown in the runtime line. |
| `HAITUN_CHANNEL` | Channel name shown in the runtime line. |
| `HAITUN_TIMEZONE` | Time zone for the date/time section (default `UTC`). |
| `XFYUN_STT_APP_ID`, `XFYUN_STT_API_KEY`, `XFYUN_STT_API_SECRET` | iFLYTEK streaming STT credentials. |
| `XFYUN_TTS_APP_ID`, `XFYUN_TTS_API_KEY`, `XFYUN_TTS_API_SECRET` | iFLYTEK online TTS credentials. |
| `XFYUN_APP_ID`, `XFYUN_API_KEY`, `XFYUN_API_SECRET` | Optional shared fallback when both services use one app. |

## Tools (`tools/`)

| Tool | Notes |
|---|---|
| `bash` | Shell commands (anyio, Windows-aware bash detection). On Windows the installer bundles MSYS2 at `{app}\msys64`, added to PATH by the launcher, so bash works out-of-the-box. |
| `powershell` | Windows-native shell. |
| `read` / `write` / `edit` | Async file ops. |
| `list_dir` / `find_files` | List one directory level; recursively find files by glob (`**/*.py`), sorted newest-first. |
| `write_excel` | Build a real `.xlsx` from a 2D array (bold header, column-width fitting). |
| `write_word` | Build a real `.docx` from structured blocks (headings/paragraphs/tables); sets the East-Asian font (`w:eastAsia`) on every style so Chinese text isn't "字体不齐". |
| `skill_manage` | CRUD on `skills/<name>/SKILL.md` (agent-created skills are mutable). |
| `flow_manage` | CRUD + promote on Fusion Flow assets under `flows/`. |
| `schedule_manage` | CRUD on `schedules/<name>/TASK.md` (cron + task body); validates the cron expression. |
| `search` (`search.py` + `_mcp.py`) | Serper web search via MCP. Requires the `mcp` extra and `uvx serper-mcp-server`; tools surface as `serper_*`. |
| `x_search` (`x_search.py` + `_x_search_impl.py`) | Search recent public posts on X (Twitter) via the X API v2 recent-search endpoint (last ~7 days). `x_search(query, max_results, sort_order)` supports X search operators (`from:`, `#tag`, `"phrase"`, `lang:`, `-is:retweet`). Uses `aiohttp` (already a core dep), no extra packages. Requires `X_BEARER_TOKEN` (X API v2 App-only OAuth 2.0 bearer token). |
| `browser` (`browser.py` + `_browser_impl.py` + `_mcp.py`) | Browser automation via Playwright MCP driving the system browser (Edge). Tools surface as `browser_*` (`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_press_key`, `browser_navigate_back`, `browser_console_messages`, `browser_handle_dialog`, `browser_take_screenshot`, …). One long-lived `npx @playwright/mcp` server with `--shared-browser-context` keeps page state across calls. Requires Node.js/`npx`. |
| `browser_cdp` (`browser_cdp.py` + `_browser_cdp_impl.py`) | Send a **raw Chrome DevTools Protocol** command to a browser — the escape hatch for anything the `browser_*` tools don't wrap (any CDP domain: `Page.*`, `Network.*`, `Emulation.*`, `Runtime.*`, `Browser.*`, `Target.*`, …). `browser_cdp(method, params, target="page"/"browser", timeout_s)` where `params` is a **JSON object string** (e.g. `'{"url": "https://example.com"}'`, empty for no-arg methods); returns the raw CDP result JSON. Launches a **dedicated** debug browser (Edge, then Chrome, with `--remote-debugging-port` + isolated profile — separate from the Playwright MCP browser) on first use and reuses it, or connects to an existing browser when `CDP_ENDPOINT` is set. CDP is JSON-over-WebSocket; uses `aiohttp` (already a core dep), no extra packages. |
| `feishu_doc` (`feishu_doc.py` + `_feishu_impl.py`) | Read, **create**, and **write** Feishu/Lark documents. `feishu_doc_read(file_type, token, max_chars)` reads docx/doc/sheet. `feishu_doc_create(title, folder_token="")` creates a new standalone docx and returns its `document_id` + URL. `feishu_doc_append_content(document_id, content)` appends headings/paragraphs (plain text or light Markdown: `# `..`###### ` → h1–h6, other lines → paragraphs) to a docx body — also works on the docx behind a wiki node via its `obj_token`. Requires `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`. |
| `feishu_wiki` (`feishu_wiki.py` + `_feishu_impl.py`) | Create docs in and resolve nodes of a Feishu/Lark **wiki (knowledge base)**. `feishu_wiki_list_spaces(page_size, page_token)` lists accessible knowledge bases (to get a `space_id`). `feishu_wiki_create_doc(space_id, title, parent_node_token="")` creates a new docx node in a knowledge base and returns `node_token` + `obj_token` (the docx `document_id`) + URL. `feishu_wiki_get_node(token)` resolves a wiki node token to its `obj_token`/`obj_type` for reading. **Create-a-knowledge-base-doc flow:** `feishu_wiki_list_spaces` → `feishu_wiki_create_doc` → `feishu_doc_append_content(obj_token, content)`. Requires `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET` + edit permission on the target space/parent node. |
| `feishu_drive` (`feishu_drive.py` + `_feishu_impl.py`) | Read/post whole-document comments on a Feishu/Lark file. Tools `feishu_drive_add_comment`, `feishu_drive_list_comments`, `feishu_drive_list_comment_replies`, `feishu_drive_reply_comment`. Requires `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`. |
| `feishu_calendar` (`feishu_calendar.py` + `_feishu_impl.py`) | Read & set schedules (日程) on the bot's calendar. `feishu_calendar_create_event(summary, start, end, …, attendees)` — one shared meeting inviting several people; `feishu_calendar_list_events(start, end, calendar_id="", …)` — read the schedule (list events in a time range; blank `calendar_id` = bot's primary, reading another calendar needs reader access to it); `feishu_calendar_create_per_person(summary, start, end, attendees, …)` — give each person their **own** schedule (one independent event per open_id, each inviting only that person). Resolve open_ids via `feishu_chat_find_member` / `feishu_department_members`. Needs bot ability enabled + scope `calendar:calendar` (or `calendar:calendar.event:read` for read-only), and `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`. |
| `speech_to_text` | iFLYTEK streaming STT for WAV/PCM/MP3 files received through `[RECV:]`. |
| `text_to_speech` | iFLYTEK online TTS; creates MP3 files delivered through `[SEND:]`. |
| `computer_use` | Apple toolset. Drive the macOS desktop in the background (screenshot/click/type/scroll/drag) via the `cua-driver` CLI — no cursor/focus/Space theft. macOS only; needs `cua-driver` installed + Accessibility & Screen Recording permissions. See `skills/macos-computer-use/`. |
| `llm_wiki` (`llm_wiki.py` + `_llm_wiki_impl.py`) | Build/query an interlinked Markdown knowledge base (Karpathy's "LLM wiki" pattern): compile knowledge into durable, cross-referenced pages under `<workspace>/wiki/` instead of re-searching from scratch. Tools `wiki_write`, `wiki_read`, `wiki_search`, `wiki_list`, `wiki_links`, `wiki_delete`. Each page has YAML frontmatter (title/tags/timestamps/aliases) + a body linking others with `[[wikilink]]`; `wiki_links` reports back-links & broken links. Async `anyio` file IO + `pyyaml` frontmatter, both already core deps — no extra packages. |
| `goal` (`goal.py` + `_goal_impl.py`) | Define and track **high-level goals** for the agent — durable intent that outlives one task (e.g. "ship payments v2", "reach 90% coverage"), which neither `todo` (one session's steps) nor the `taskflow` skill (a task/project board) captures. Tools `goal_set`, `goal_progress`, `goal_get`, `goal_list`, `goal_delete`. Each goal is a Markdown file under `<workspace>/goals/` with YAML frontmatter (title/slug/status[active,paused,achieved,abandoned]/priority/progress 0-100/target_date/tags/timestamps) + an append-only progress `log`, and a body that links related/sub-goals with `[[slug]]`. `goal_progress` records a dated log entry and moves %/status (100% ⇒ achieved); `goal_list` rolls up status counts. Async `anyio` file IO + `pyyaml` frontmatter, both already core deps — no extra packages. |
| `clarify` | Ask the user a question when you need clarification, feedback, or a decision before proceeding. Two modes: multiple choice (up to 4 `options` + an auto-appended "Other" free-text) or open-ended (omit `options`). Returns a formatted question block to show the user; then **end the turn** and wait — the reply arrives as the next message (the runtime has no blocking-input primitive). Pure-Python, no extra deps. |

## Skills (`skills/`)

- `_universal` — always-relevant working discipline.
- The hermes domain skill set (cryptanalysis, image-segmentation, ml-inference, …).
- Selected curated skills (`psi-agent-help`, `code-review-checklist`, `python-async-basics`,
  `python-static-analysis`, `user-preferences-and-language`, `example-skill`).
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
- `fusion-flow` — the immutable Fusion Flow runtime skill (node-based). **Do not edit it.**

## Schedules (`schedules/`)

- Use `schedule_manage` to add / list / view / update / delete tasks instead of editing
  `schedules/<name>/TASK.md` by hand.
- `schedules/heartbeat/` uses `visibility: silent` so HEARTBEAT turns stay out of Web Console
  history and are not injected into the next chat SSE.

## Prerequisites

- **Fusion Memory**: Haitun only consumes an operator-provisioned remote MCP
  Streamable HTTP service. The bearer token defines user identity: the same
  user shares memory across sessions and workspaces, while different users are
  isolated. Workspace/session IDs are context, never user identity. The
  operator creates tokens, terminates TLS at the reverse proxy, and supervises
  MCP/model/history services with `systemd` for SSH-disconnect resilience and
  restart after failure. Do not commit or log `FUSION_MEMORY_TOKEN`; do not
  create a local memory service or use another public memory transport. Follow
  the consent policy before calling any memory tool.

- **Fusion Flow**: Node.js / `npm` / `npx`. First use: `cd skills/fusion-flow && npm install`.
- **Serper search**: install psi-agent with the `mcp` extra and have `uvx` available.
- **Browser tools**: Node.js / `npx` (first run downloads `@playwright/mcp`) and a system
  browser (Edge by default). Optional env: `BROWSER_CHANNEL` (`msedge`/`chrome`),
  `BROWSER_HEADLESS` (`1`/`0`), `BROWSER_CAPS` (default `vision,devtools`). If Node is
  missing the `browser_*` tools are skipped at load time (logged), not fatal.
- **`browser_cdp` (raw CDP)**: a Chromium-family browser (Edge/Chrome) installed, **or**
  `CDP_ENDPOINT` pointing at a browser started with `--remote-debugging-port` (e.g.
  `http://localhost:9222`). No Node needed — it launches the browser directly and speaks
  CDP over a WebSocket with `aiohttp`. Optional env: `CDP_ENDPOINT`, `CDP_BROWSER_CHANNEL`
  (`msedge`/`chrome`), `CDP_HEADLESS` (`1`/`0`, default headed), `CDP_STARTUP_TIMEOUT`,
  `CDP_COMMAND_TIMEOUT`. If no browser is found the tool returns `ok=false` (not fatal).
- **Feishu tools**: set `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET` (same app as the Feishu channel). Reuses the `lark-channel-sdk` dependency; no extra install. If unset, the tools return `ok=false` (not fatal).

## ⚠️ Intentionally-kept un-wired code (future extension)

psi-agent's session loader only ever calls `system_prompt_builder()` (and an optional
`system_prompt_rebuild_checker()`), loads `tools/*.py`, and runs `schedules/*/TASK.md`. The
following are deliberately included as **future-extension hooks** and are **NOT** invoked by
the current framework — do not "clean them up" as dead code:

- `systems/system.py`: `System.compact_history()`, `System.after_turn()`, and the
  `_run_self_evolution_review` / self-evolution helpers.
- `systems/curator.py`, `systems/background_review.py`, `systems/threat_patterns.py`,
  `systems/prompt_constants.py` — standalone modules from the hermes-style design, kept for
  when matching hooks are wired into the framework. They are not imported by `system.py`.

## Smoke test

```bash
uv run python examples/haitun-workspace/systems/system.py   # prints the assembled prompt
```
