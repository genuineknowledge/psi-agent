# haitun-workspace (海豚 / Haitun agent 🐬)

A consolidated psi-agent workspace. Its persona is fixed: a **Haitun agent** (always stated
in the system prompt). It merges the most useful parts of the other example workspaces:

- **Prompt engine** — a layered builder (system prompt + per-turn context block, skills
  index, bootstrap context files), with **all configuration kept inside this
  workspace** (there is no global config directory). The prompt is built once per Session
  and reused byte-for-byte; the clock and the runtime line are re-rendered **every turn** by
  `turn_context_builder()` and delivered at the *tail* of the request, on the turn's own user
  message, so staying current leaves the prompt and every earlier turn untouched. `USER.md` and the dynamic
  context files stay in the prompt and trigger a rebuild only when their **content** changes.
- **Fusion Flow** — `fusion-flow-next` is the preferred bounded G4/Agent runtime
  (`run_flow`); the existing Node/Fuclaw `fusion-flow` + `flow_run` path remains an
  explicit `.flow.ts` fallback. `flow_manage` supports both and prefers Next assets.
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

## Remote Fusion Memory configuration

These process-start settings connect Haitun to an operator-provisioned remote Fusion Memory MCP
Streamable HTTP service. For multi-user Feishu routing, the starter manually configures one token
map outside the workspace. The bearer token is the only source of server-side user identity: the
same token shares memory across Sessions, while different tokens are isolated. Workspace and
Session IDs are provenance only. Keep tokens in deployment-managed secrets; never commit or log
them. Haitun consumes this MCP service only and must not use legacy REST routes.

| Variable | Purpose |
|---|---|
| `FUSION_MEMORY_MCP_URL` | Remote Fusion Memory MCP Streamable HTTP endpoint; TLS is terminated by its reverse proxy. |
| `FUSION_MEMORY_TOKEN_MAP_FILE` | Absolute path to the operator-owned JSON map keyed by Feishu `open_id`; each entry requires `token`, while empty or omitted `workspace_id` defaults to `haitun`. |
| `FUSION_MEMORY_AUTO_REGISTER_FEISHU` | Optional first-use Feishu private-chat registration. When true and a `feishu-<open_id>` Session is missing from the token map, the workspace signs a short-lived registration assertion with the Feishu app secret, asks Memory to register that `open_id`, and writes the returned bearer token into the token map. |
| `FUSION_MEMORY_ORGANIZATION_ID` | Required with automatic registration; identifies the organization selected for this deployment. |
| `FUSION_MEMORY_TOKEN` | Legacy single-user bearer token, used only when no token-map path is configured. |
| `FUSION_MEMORY_WORKSPACE_ID` | Legacy single-user workspace provenance (defaults to `haitun`). |
| `FUSION_MEMORY_SESSION_ID` | Optional legacy single-user Session provenance. |

Token-map membership enables automatic durable memory. On each mapped user's first message after
process startup, `system_prompt_builder()` or `system_prompt_rebuild_checker()` idempotently starts
authenticated MCP health checking and that Session's passive JSONL writer. Unknown users can chat
but receive no bearer token, connector, writer, checkpoint, or durable memory unless Feishu auto-registration
is explicitly enabled and the Memory service accepts the signed assertion. Auto-registration uses the
runtime-observed private Feishu `open_id`; it does not let model-visible text choose an identity.
Map mode never falls back to the legacy shared token. Duplicate token assignments reject the map. Removing an entry
stops that Session's watcher and closes its cached client. Passive persistence accepts only completed
ordinary chat turns, excludes schedule/heartbeat/compaction rows, and skips unchanged history files.
Validated maps are cached by file signature. Each active turn renews a five-minute watcher lease;
idle watcher/client resources are reclaimed and restart on the next message. Server provisioning
and token creation remain operator actions.

This is a trusted-runtime boundary: the Feishu Channel, Gateway, Session runtime and management
tools, host shell, and token-map file must be trusted. `feishu-<open_id>` is not a cryptographic
principal. Strong isolation from forged Session IDs or workspace code that can read the complete
map requires core authorization and a privileged credential broker outside this workspace.

## Runtime display and service credentials

The following optional variables either change runtime display metadata or enable their named
service tools:

| Variable | Purpose |
|---|---|
| `HAITUN_MODEL` | Override the model name shown in the runtime line. |
| `HAITUN_AGENT_ID` | Agent ID shown in the runtime line. |
| `HAITUN_CHANNEL` | Channel name shown in the runtime line. |
| `TZ` | Standard IANA time zone for the date/time section, e.g. `Asia/Shanghai` (when unset, follows the system's local time zone). |
| `XFYUN_STT_APP_ID`, `XFYUN_STT_API_KEY`, `XFYUN_STT_API_SECRET` | iFLYTEK streaming STT credentials. |
| `XFYUN_TTS_APP_ID`, `XFYUN_TTS_API_KEY`, `XFYUN_TTS_API_SECRET` | iFLYTEK online TTS credentials. |
| `XFYUN_APP_ID`, `XFYUN_API_KEY`, `XFYUN_API_SECRET` | Optional shared fallback when both services use one app. |
| `PSI_OAUTH_CALLBACK_BASE` | Gateway base URL the user's browser can reach; makes OAuth authorization copy-paste-free via the `/oauth/callback` relay (works for phone approval / multi-user). Register `<base>/oauth/callback` in the Feishu console. |
| `PSI_OAUTH_LOOPBACK_PORT` | Port for the one-shot `127.0.0.1` OAuth callback listener (default `17860`); same-machine deployments only. |

## Channel events (`channel_events/`)

**定事信号源（交付对接）**：有「每次 xx 就…」类需求且 xx 可观测时，在 **`channel_events/<channel>/`** 按需注册（官方：`map.py`；自定义：`produce.py`），≈ 加 tool；**不要**改 Session catalog，也**不要**为每个事件改 Channel 源码（Feishu 已统一接线）。必读：`docs/superpowers/specs/2026-07-29-channel-events-developer-guide.md`。挂钩仍用 `triggers/` + `trigger_manage`。

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
| `skill_manage` | CRUD on **agent** `skills/<name>/SKILL.md`（经 `get_agent()`；agent-created skills are mutable）. |
| `flow_manage` | CRUD + promote on Fusion Flow assets under **workspace** `flows/`; prefers `.workflow` / `.g4` over `.flow.ts`. |
| `run_flow` | Execute bounded Agent-only Fusion Flow Next G4 workflows synchronously; requires exact instruction bodies and gives inner Steps no tools. |
| `flow_run` | Legacy Node/Fuclaw `.flow.ts` runner retained for explicit fallback use. |
| `schedule_manage` | CRUD on **workspace** `schedules/<name>/TASK.md`. **Recurring**: `action=create` + `cron`. **One-shot**: `action=create` + `once_at` (`YYYY-MM-DD HH:MM` local) → writes cron + `run_once: true` (Session deletes TASK.md after first successful fire). **`fire=tool`**: Session calls `tool(**tool_args)` at fire time with no LLM (required for Feishu IM reminders via `feishu_message_send`). `fire=prompt` (default) injects TASK body for an agent turn. Also `visibility` (`display`/`silent`), list/view/patch/delete. |
| `trigger_manage` | CRUD on **agent** `triggers/<name>/TRIGGER.md`。`event` 名应对齐 agent ``channel_events/`` 已接通能力；Session 不再用 catalog 硬拒。`fire=tool` 命中后直调工具。见 `skills/feishu-event-remind`；事件定义见 ``channel_events/README.md``。 |
| `memory_add` / `memory_search` / `memory_answer_context` / `memory_health` / `assignment_upsert` / `assignment_get` / `assignment_list` / `assignment_transition` | Per-Session routed Fusion Memory MCP tools. Authentication comes only from the trusted runtime Session and operator token map. Assignment tools carry generic work-arrangement facts and state transitions; they do not expose organization_id in tool arguments. |
| `assignment_send_card` | Haitun workspace wrapper for sending a deterministic Feishu work-assignment card with `view_assignment_detail` and `confirm_assignment_receipt` actions wired to assignment handlers. |
| `haibao_list_datasets` / `haibao_ask` | Bundled Haibao MCP Adapter tools for real business-data queries. They require an operator-provisioned private MCP server; no private server or database onboarding is bundled. |
| `search` (`search.py` + `_mcp.py`) | Serper web search via MCP. Requires the `mcp` extra and `uvx serper-mcp-server`; tools surface as `serper_*`. |
| `x_search` (`x_search.py` + `_x_search_impl.py`) | Search recent public posts on X (Twitter) via the X API v2 recent-search endpoint (last ~7 days). `x_search(query, max_results, sort_order)` supports X search operators (`from:`, `#tag`, `"phrase"`, `lang:`, `-is:retweet`). Uses `aiohttp` (already a core dep), no extra packages. Requires `X_BEARER_TOKEN` (X API v2 App-only OAuth 2.0 bearer token). |
| `browser` (`browser.py` + `_browser_impl.py` + `_mcp.py`) | Browser automation via Playwright MCP driving the system browser (Edge). Tools surface as `browser_*` (`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_press_key`, `browser_navigate_back`, `browser_console_messages`, `browser_handle_dialog`, `browser_take_screenshot`, …). One long-lived `npx @playwright/mcp` server with `--shared-browser-context` keeps page state across calls. Requires Node.js/`npx`. |
| `browser_cdp` (`browser_cdp.py` + `_browser_cdp_impl.py`) | Send a **raw Chrome DevTools Protocol** command to a browser — the escape hatch for anything the `browser_*` tools don't wrap (any CDP domain: `Page.*`, `Network.*`, `Emulation.*`, `Runtime.*`, `Browser.*`, `Target.*`, …). `browser_cdp(method, params, target="page"/"browser", timeout_s)` where `params` is a **JSON object string** (e.g. `'{"url": "https://example.com"}'`, empty for no-arg methods); returns the raw CDP result JSON. Launches a **dedicated** debug browser (Edge, then Chrome, with `--remote-debugging-port` + isolated profile — separate from the Playwright MCP browser) on first use and reuses it, or connects to an existing browser when `CDP_ENDPOINT` is set. CDP is JSON-over-WebSocket; uses `aiohttp` (already a core dep), no extra packages. |
| `feishu_doc` (`feishu_doc.py` + `_feishu_impl.py`) | Read, **create**, and **write** Feishu/Lark documents. `feishu_doc_read(file_type, token, max_chars)` reads docx/doc/sheet. `feishu_doc_create(title, folder_token="")` creates a new standalone docx and returns its `document_id` + URL. `feishu_doc_append_content(document_id, content)` appends headings/paragraphs (plain text or light Markdown: `# `..`###### ` → h1–h6, other lines → paragraphs) to a docx body — also works on the docx behind a wiki node via its `obj_token`. **Revise in place** (the append tools only ever add, so correcting a wrong paragraph needs these three): `feishu_doc_list_blocks(document_id, max_blocks=200, user_key)` lists the body's blocks as `{block_id, block_type, type_name, parent_id, text, editable_text}` — the **only** way to learn the `block_id`s the other two address content by (`text` is a 200-char preview; `editable_text` is false for image/table/divider blocks that carry no text runs); `feishu_doc_update_block(document_id, block_id, text)` replaces one block's text while keeping its id and type (a heading stays a heading), via `PATCH …/blocks/:block_id` with `update_text_elements` — the text is *replaced*, not merged, and the doc's root block (whose id **is** the `document_id`) is refused since it holds no text; `feishu_doc_delete_blocks(document_id, block_ids_json, parent_block_id="")` deletes whole blocks by id. Deletion is the sharp one: Feishu deletes by **child-index range under a parent**, not by id, so each id is resolved to its current index at delete time and the batch runs **highest-index-first** (deleting low-to-high would shift each later sibling down and take the wrong block); ids that can't be located are returned as `not_found` rather than guessed at, and `parent_block_id` is needed for *nested* blocks (inside a table cell / callout — see `parent_id` in the list result), empty meaning the document root. Requires `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`; the block writes need scope `docx:document` and take the usual `identity` (`"user"` / `"bot"`) argument, while `list_blocks` is a read and takes none. |
| `share_brief_guard` (`share_brief_guard.py`) | Deterministic guard for `feishu-share-brief`: validates exact source substrings, pagination/time coverage, action fields, revisions, whole-message confirmation, and frozen draft content. State is isolated by Session under AppData. It never reads Feishu, changes permissions, sends messages, or publishes; semantic omission discovery remains the Agent's responsibility. |
| `feishu_wiki` (`feishu_wiki.py` + `_feishu_impl.py`) | Create docs in and resolve nodes of a Feishu/Lark **wiki (knowledge base)**. `feishu_wiki_list_spaces(page_size, page_token)` lists accessible knowledge bases (to get a `space_id`). `feishu_wiki_create_doc(space_id, title, parent_node_token="")` creates a new docx node in a knowledge base and returns `node_token` + `obj_token` (the docx `document_id`) + URL. `feishu_wiki_get_node(token)` resolves a wiki node token to its `obj_token`/`obj_type` for reading. **Create-a-knowledge-base-doc flow:** `feishu_wiki_list_spaces` → `feishu_wiki_create_doc` → `feishu_doc_append_content(obj_token, content)`. Requires `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET` + edit permission on the target space/parent node. |
| `feishu_drive` (`feishu_drive.py` + `_feishu_impl.py`) | Read/post whole-document comments on a Feishu/Lark file. Tools `feishu_drive_add_comment`, `feishu_drive_list_comments`, `feishu_drive_list_comment_replies`, `feishu_drive_reply_comment`. Requires `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`. |
| `feishu_sheet` (`feishu_sheet.py` + `_feishu_impl.py`) | Range-level read/write on a Feishu/Lark **spreadsheet** (`feishu_doc_read(file_type="sheet")` only dumps the whole workbook). `feishu_sheet_tabs(token, user_key)` lists worksheets — a `SHEET_ID` is **not** in the sheet URL but every range is `"SHEET_ID!A1:B2"`, so call this first. `feishu_sheet_read(token, range, max_chars, user_key)` reads one range as plain-text rows, **flattening mention cells (`@somebody`) and styled rich text to visible text** (a name column reads `"@张三"`, not raw JSON — strip the leading `@` when matching); use it to locate a person's row and to check whether a target cell is already filled before overwriting. `feishu_sheet_write` / `feishu_sheet_append` / `feishu_sheet_format` write values/formulas (a cell string starting with `=` becomes a formula) and cell style. Reads are tenant-first with `user_key` as a permission fallback. Writes take an `identity` argument (`"user"` / `"bot"`) deciding who owns the result; omitted, it uses the choice remembered for that `user_key`, and returns `need_identity_choice` if they have never been asked. Requires `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`. |
| `feishu_bitable` (`feishu_bitable.py` + `_feishu_impl.py`) | Build and drive a Feishu/Lark **bitable (多维表格)**. **Create from nothing**: `feishu_bitable_create_app(name, folder_token="", time_zone="", user_key)` makes the base itself (returns `app_token` + `url` + `default_table_id` — every other bitable tool needs an `app_token`, and this is the only thing that produces one); `feishu_bitable_create_table(app_token, table_name, fields_json, default_view_name="")` makes a data table **with its columns** (`fields_json` is a JSON array of `{field_name, type, property?, ui_type?}`; `type` ints 1 文本/2 数字/3 单选/4 多选/5 日期/7 复选框/11 人员/13 电话/15 超链接/17 附件/20 公式/22 地理位置/1001 创建时间/1005 自动编号, 19 查找引用 not creatable — the **first** field is the index column and must be 1/2/5/13/15/20/22, else Feishu answers 1254012; `default_view_name` is only accepted together with `fields_json`); `feishu_bitable_create_field(app_token, table_id, field_name, field_type, property_json="", ui_type="")` adds one column to an existing table. `feishu_bitable_create_tables(app_token, table_names)` adds several **empty** tables at once (names only, max 50) and `feishu_bitable_delete_tables(app_token, table_ids)` removes whole tables with all their data (max 50; a base must keep one table — 1254034). **Base itself**: `feishu_bitable_get_app(app_token)` reads its name / time zone / `is_advanced` (check this before creating a role — advanced permission must be on), `feishu_bitable_update_app(app_token, name, is_advanced)` renames it and toggles 高级权限 (omitted settings stay; not atomic, so `changed` reports what was sent; refused on wiki-hosted/embedded bases — 1254301), `feishu_bitable_copy_app(app_token, name, folder_token, without_content)` duplicates a whole base — **the template move**: keep one well-built ledger and copy it (with `without_content=True` for structure only) instead of rebuilding columns each time. **Read/write**: `feishu_bitable_list_tables` → `table_id`, `feishu_bitable_list_records(app_token, table_id, …, filter, sort, field_names)` pages the whole table, `feishu_bitable_search_records(app_token, table_id, filter_json, sort_json, field_names, view_id, …)` finds *particular* rows by structured conditions (`{"conjunction":"and","conditions":[{"field_name":"状态","operator":"is","value":["进行中"]}]}`; operators `is`/`isNot`/`contains`/`doesNotContain`/`isEmpty`/`isNotEmpty`/`isGreater`/`isGreaterEqual`/`isLess`/`isLessEqual`, `value` always an array of strings; page_size max 500) — **this is how you get a `record_id`** for an update or delete, and `view_id` can't be combined with filter/sort (Feishu would ignore it, so the tool refuses), `feishu_bitable_get_record(app_token, table_id, record_id)` reads one row plus its direct url, `feishu_bitable_create_record(app_token, table_id, fields_json)`, `feishu_bitable_create_records(app_token, table_id, records_json)` writes **many rows in one call** (batches of 500 — use this instead of looping the singular form; a table caps at 20000 rows), `feishu_bitable_update_record(app_token, table_id, record_id, fields_json)` / `feishu_bitable_update_records(app_token, table_id, records_json)` (**change cell values in existing rows** — the update is incremental, so only the columns passed are written and the rest of the row is untouched; `null` clears a cell; `record_id` comes from `feishu_bitable_list_records`, whose `filter` locates the row; use the plural form for multi-row sweeps, batches of 1000; both default to `validate_fields=True`, which checks the column names against the table first because Feishu **silently drops unknown column names and still answers code:0**, and warns with `dropped_fields` when the response shows a value didn't land; 公式/查找引用/创建时间/自动编号 columns are computed and not writable), `feishu_bitable_delete_records` / `feishu_bitable_clear_table` (wipe Feishu's default empty rows), `feishu_bitable_list_fields` / `feishu_bitable_delete_fields` (drop placeholder columns; the primary one can't be deleted — 1254046) / `feishu_bitable_update_field(app_token, table_id, field_id, field_name, field_type, property_json)` (**fix a column without losing its data** — rename it, retype it, add a 单选 option; deleting and re-creating the column would throw away every value. Feishu replaces the whole definition, so whatever you omit is read back off the table and carried over, including the existing `property` when the type is unchanged; a `property_json` you *do* pass replaces the old settings wholesale. Type 19 and an index column outside 1/2/5/13/15/20/22 are refused locally). **Role-based visibility**: `feishu_bitable_create_role` / `feishu_bitable_list_roles` / `feishu_bitable_add_role_member` (needs 高级权限 on the base). Build-a-tracker flow: `create_app` → `create_table` (real columns) → `create_record` per row. Writes take an `identity` argument (`"user"` / `"bot"`) deciding whether the base is owned by the person who asked or by the bot; omitted, it uses the choice remembered for that `user_key`, and returns `need_identity_choice` if they have never been asked. Requires `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET` + scope `bitable:app` (`base:app:create` / `base:table:create` / `base:field:create` also work), and — for a base the bot didn't create — the app added as a collaborator (editor). |
| `feishu_auth` (`feishu_auth.py` + `_feishu_impl.py` + `_oauth_receiver.py`) | One-off **user** authorization (OAuth authorization-code flow + PKCE S256) for the `user_access_token` that some Feishu APIs require and the bot's own credentials can't provide. The happy path asks the user for **no copy-pasting**: `feishu_auth_start(user_key)` returns an `authorize_url` to send them, and when it reports `auto_receive=True`, `feishu_auth_wait(user_key, timeout_seconds)` receives the code by itself and finishes the exchange. The code returns either through the Gateway's `/oauth/callback` relay (`PSI_OAUTH_CALLBACK_BASE` — works when the user approves **on a phone**, the only viable channel for multi-user deployments) or through a one-shot `127.0.0.1:17860` listener (`PSI_OAUTH_LOOPBACK_PORT`, same machine only). `feishu_auth_complete(code, user_key)` is the fallback for when neither channel exists (`auto_receive=False` / `manual_required=True`) — then, and only then, the user copies `code=` out of the address bar. **The redirect URI must be registered in the Feishu console's redirect-URL list** or Feishu refuses before redirecting (20071). Authorization asks only for the **capabilities** a task needs: `feishu_auth_start(user_key, capabilities)` takes capability keys (`docs_read`/`drive_read`/`drive_write` (incl. spreadsheets)/`docx_write`/`wiki_write`/`bitable_write`/`task_write`/`calendar_write`/`contact_read`/`contact_phone_email_read`), never raw scope strings — an invalid scope makes Feishu reject the whole authorize page (error 20043), so unknown keys are refused locally. Each grant is the **union** of the request and everything already granted (Feishu's token carries only the latest grant's scopes, so asking for just the new one would revoke the rest); what a user has is recorded in `granted_scopes.json`, so a later task needing the same capabilities never re-prompts. Separately, `feishu_identity_set(user_key, identity)` / `feishu_identity_get(user_key)` record and report whether that person's **writes run as themselves** (output owned by them) or **as the bot** (output owned by the bot), kept in `identity.json`; write tools return `need_identity_choice` and send nothing until it is answered. Tokens are cached per `user_key` (sender's open_id) in `<workspace>/.psi/feishu/uat.json` (plaintext, dev-only) and auto-refreshed. |
| `feishu_calendar` (`feishu_calendar.py` + `_feishu_impl.py`) | Read & set schedules (日程) on the bot's calendar. `feishu_calendar_create_event(summary, start, end, …, attendees)` — one shared meeting inviting several people; `feishu_calendar_list_events(start, end, calendar_id="", …)` — read the schedule (list events in a time range; blank `calendar_id` = bot's primary, reading another calendar needs reader access to it); `feishu_calendar_create_per_person(summary, start, end, attendees, …)` — give each person their **own** schedule (one independent event per open_id, each inviting only that person). Resolve open_ids via `feishu_chat_find_member` / `feishu_department_members`. Needs bot ability enabled + scope `calendar:calendar` (or `calendar:calendar.event:read` for read-only), and `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`. |
| `speech_to_text` | iFLYTEK streaming STT for WAV/PCM/MP3 files received through `[RECV:]`. |
| `text_to_speech` | iFLYTEK online TTS; creates MP3 files delivered through `[SEND:]`. |
| `computer_use` | Apple toolset. Drive the macOS desktop in the background (screenshot/click/type/scroll/drag) via the `cua-driver` CLI — no cursor/focus/Space theft. macOS only; needs `cua-driver` installed + Accessibility & Screen Recording permissions. See `skills/macos-computer-use/`. |
| `llm_wiki` (`llm_wiki.py` + `_llm_wiki_impl.py`) | Build/query an interlinked Markdown knowledge base (Karpathy's "LLM wiki" pattern): compile knowledge into durable, cross-referenced pages under `<workspace>/wiki/` instead of re-searching from scratch. Tools `wiki_write`, `wiki_read`, `wiki_search`, `wiki_list`, `wiki_links`, `wiki_delete`. Each page has YAML frontmatter (title/tags/timestamps/aliases) + a body linking others with `[[wikilink]]`; `wiki_links` reports back-links & broken links. Async `anyio` file IO + `pyyaml` frontmatter, both already core deps — no extra packages. |
| `todo` (`todo.py` + `_todo_store.py`) | **本 Session 执行步骤清单**（非跨会话 goal、非飞书个人看板）。权威约定：`skills/task-planning/SKILL.md` — 有分拆价值才写；**写即承诺随进程维护**（禁止建表复读后空过不勾就当结束；仅计划则须声明且 status 诚实）。禁止为 UI 进度凑装饰清单。`todo()` 读；`todo(todos='[...]')` 写（`content` 必须是字符串）；`merge=true` 按 id 更新。落盘 AppData `todos/{session_id}.json`（legacy `.psi/todos` 双读）。Gateway `GET …/todos` / spa-v2 `N/M` **只消费**已有清单。 |
| `goal` (`goal.py` + `_goal_impl.py`) | Define and track **high-level goals** for the agent — durable intent that outlives one task (e.g. "ship payments v2", "reach 90% coverage"), which neither `todo` (one session's steps) nor the `taskflow` skill (a task/project board) captures. Tools `goal_set`, `goal_progress`, `goal_get`, `goal_list`, `goal_delete`. Each goal is a Markdown file under `<workspace>/goals/` with YAML frontmatter (title/slug/status[active,paused,achieved,abandoned]/priority/progress 0-100/target_date/tags/timestamps) + an append-only progress `log`, and a body that links related/sub-goals with `[[slug]]`. `goal_progress` records a dated log entry and moves %/status (100% ⇒ achieved); `goal_list` rolls up status counts. Async `anyio` file IO + `pyyaml` frontmatter, both already core deps — no extra packages. |
| `clarify` | Ask the user a question when you need clarification, feedback, or a decision before proceeding. Two modes: multiple choice (up to 4 `options` + an auto-appended "Other" free-text) or open-ended (omit `options`). Returns a formatted question block to show the user; then **end the turn** and wait — the reply arrives as the next message (the runtime has no blocking-input primitive). Pure-Python, no extra deps. |
| `c_drive_cleanup` (`c_drive_cleanup.py` + `_c_drive_cleanup_impl.py`) | Windows C-drive `scan` / `status` / `clean` tool. The first scan in a Session requires confirmation; cleanup requires the user's affirmation and deletes only unchanged candidates from allowlisted temporary/cache locations. Large files, exact duplicates, and stale Downloads are report-only. See `skills/windows-c-drive-cleanup/SKILL.md` for the agent workflow. |

## Skills (`skills/`)

- `_universal` — always-relevant working discipline.
- The hermes domain skill set (cryptanalysis, image-segmentation, ml-inference, …).
- Selected curated skills (`psi-agent-help`, `code-review-checklist`, `python-async-basics`,
  `python-static-analysis`, `user-preferences-and-language`, `example-skill`).
- `task-planning` — **何时必须 / 禁止**用 `todo` 拆步；**建表即承诺维护**（推进配方 + 禁止空表收工）；spa/Gateway 进度 UI 只消费结果，不定义策略。
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
- **行政财务技能组** (drive the existing `feishu_*` tools; no dedicated tool, no extra deps):
  - `admin-finance-governance` — the tiered-autonomy rulebook (小事不问 / 中事少问 / 大事必问): default 假勤/报销 thresholds, per-tier action boundaries, and the audit-trail rule. `knowledge-base`; the other three reference it. Load first for any admin-finance work.
  - `feishu-leave-audit-board` — auto-audit 假勤 approvals by tier (小事 auto-approve via `feishu_approval_decide`, 中事 recommend, 大事 ask), log to a bitable, build a 看板 doc, and push it via `feishu_message_send`/`feishu_topic_start`. `productivity`.
  - `feishu-reimbursement-audit-report` — auto-audit 报销 by tier, download verified attachments per-claim, roll up a bitable, and produce a 财务报告/分析单 doc pushed to finance. Extends `feishu-reimbursement-archive`. `productivity`.
  - `feishu-admin-finance-assistant` — 真知小助手: answer 行政/财务 policy questions from Feishu docs synced into the local `llm-wiki` (`wiki_*`), always citing sources, routing any implied action through `admin-finance-governance`. `knowledge-base`.
- `feishu-todo-board-sync` — 搬运一篇飞书 docx 个人 ToDoList 进团队看板电子表格（列=日期、行=人）: slice the doc's newest date section, split its `ToDo` items **by the `@name` mentioned in each item** (items mentioning nobody go to the doc's owner), assemble each person's cell text, and write it to the **caller-specified** column. Three hard rules: attribution follows `@name`; the target column is always given by the caller (**never** inferred from the source doc's date, whose format differs from the header's); a non-empty target cell is reported for confirmation instead of being silently overwritten. Board structure (header row / name column / `SHEET_ID`) is discovered per run, never hardcoded. Drives the existing `feishu_wiki_get_node` → `feishu_sheet_tabs` → `feishu_doc_read` → `feishu_sheet_read` → `feishu_sheet_write` tools; `productivity`, no dedicated tool, no extra deps. Never adds rows for people absent from the board — it reports them as skipped.
- `feishu-share-brief` — collect explicitly scoped Feishu messages/documents/comments for a person or task and an offset-qualified time range, then create an evidence-backed text preview or, after exact user confirmation, an unshared draft document. Ordinary group messages are not automatically copied into Session history; retrieval uses existing `feishu_*` tools, while `share_brief_guard` enforces evidence, coverage, revision, and confirmation boundaries.
- `feishu-schedule-message` — Feishu timed reminders via **`schedule_manage` `fire=tool`**: Session **directly** calls `feishu_message_send(**tool_args)` at fire time (no LLM). Pass `tool_args` JSON with real `chat_id`/`open_id` from `<feishu_context>` (**not** Gateway `session_id`). Prefer `visibility=silent`. One-shot (`once_at`) **rejects** `fire=prompt` / content-embedded calls — create must include `fire`+`tool`+`tool_args` in one shot; Session `run_once` deletes TASK after fire.
- `feishu-event-remind` — Feishu **event** reminders（定事）via **`trigger_manage` `fire=tool`**: map NL → catalog `event`（如 `feishu.chat.member_added`）+ dual-write `raw_event`（如 `im.chat.member.user.added_v1`）；Session 先规范匹配再 raw 回退。禁止手写 `TRIGGER.md`；未接通事件勿 invent catalog 名。
- **Feishu tool credentials on Gateway（踩坑）**：`feishu_message_send` 等 workspace 工具跑在 **Session / Gateway 进程**里，读的是该进程的 `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`。只给 Feishu **channel** 进程设环境变量不够——定时触发时会报 `Feishu app not configured`，飞书收不到推送。启动 Gateway 时也要带上同一组凭证。
- **Feishu interactive-card callback contract**：发送给其他人的卡片必须同时传
  `business_context_json`（业务类型、稳定业务 ID、发起人、当前状态等收件方 agent 独立处理所需事实）和
  `action_handlers_json`（按钮 `value.action` 到 handler 标识符的完整映射）。工具会把原卡片、发送来源、
  业务上下文和映射保存到 v2 snapshot；按钮/表单操作由 Feishu Channel 按操作者 `open_id` 路由回点击者
  agent，以 `<feishu_card_action>` 包裹的结构化 JSON 作为下一条 user 消息，并在原卡片所在聊天流式回复。
  信封中的 `source` 是发卡方 Session / open_id 与接收目标，`card` 是原始完整卡片，
  `business_context` 是发卡时提供的业务事实，`dispatch` 是确定性选择结果，`action` 是飞书原始操作。
  Channel **只选择 handler，不直接执行 handler，也不绕过 LLM**。映射键和 handler 必须是无首尾空白的
  canonical 字符串；配置非空映射后，未知 action 必须得到
  `dispatch.matched=false` 和 `handler=null`；点击者 agent 不得臆造或执行未匹配 handler。只有未配置映射的
  v1/v2 snapshot 才回退到把 `action.value.action` / `action_id` 本身作为 handler；snapshot 缺失或损坏时
  必须 fail closed，不能假定它是旧卡片。首个回调留下持久 `.consumed` tombstone，后续进程/重启后的重复点击
  直接忽略。原卡片的只读“已选择”已经确认点击，回调 agent 不得再生成“你点击了…”或“我来处理/通知…”等过程文本；
  应先按匹配 handler 完成必要工具调用。成功且无额外必要信息时以零 assistant 文本结束，不得输出 `NO_REPLY`
  或成功确认；只有警告、部分失败、权限问题、未匹配 handler 或必要后续步骤才回复，且不得把失败说成成功。
  Feishu Channel 会防御性吞掉卡片回调中整段独立的 `NO_REPLY`，其他文本保持不变。自定义 AppData 时 Channel 和
  Gateway/workspace tool 必须使用同一根，推荐统一设置 `PSI_APPDATA`。
  按钮组/表单优先用旧版卡片；
  Card 2.0 不支持旧版 `action` 标签。按钮 `value` 必须包含明确动作名和稳定业务 ID（如 `request_id`），
  且不同按钮使用不同值；选择器/日期输入放进 `form` 后提交，让结果进入 `form_value`，不要依赖 SDK 1.2.0
  无法完整区分选项变化的 `standalone` 回调。每张卡片按 `message_id` 只接受首个有效操作，随后保留原卡片
  标题和正文，并把交互区替换为“已选择: <选项>”只读提示；再次收集输入必须发新卡片。有后果的操作执行前
  重新校验权限和当前状态，底层写操作保持 **idempotent**，以覆盖飞书重投、卡片更新失败和多实例并发。
  工具成功后卡片已经对用户可见：若卡片已承载全部必要信息，本轮以零 assistant 文本结束，不得输出
  `NO_REPLY`、发送确认或重复卡片内容/按钮；若仍有卡片未承载的必要信息（风险、部分失败、必要后续步骤），
  则必须只回复这些信息。若卡片已发送但 snapshot 保存失败，工具返回
  `ok=false, sent=true, callback_context_saved=false`；必须告知这项必要的部分失败，且不要重发卡片造成重复。
- `fusion-flow-next` — preferred immutable G4/Agent workflow skill.
- `flow` (`skills/fusion-flow/`) — immutable legacy Node/Fuclaw `.flow.ts` fallback.

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
  would fire once per online session (Feishu spawns one Session per `open_id`).
  Use `'*'` plus a blacklist rather than an enumerated whitelist when a session should own
  "everything except these": a whitelist cannot cover `TASK.md` files created after startup.
  Consequence: when this package is used as a **separate agent root** (`--agent` ≠
  `--workspace`), the `schedules/heartbeat/` shipped here is **not** loaded. Put schedules
  under the workspace if you need them to run. Single-root usage (`agent` ≡ `workspace`) is
  unaffected.
- `visibility: display` results are stashed to pending, but the scheduler session has no
  channel attached, so under Gateway they do not reach any user. Use `fire=tool` (e.g.
  `feishu_message_send`) for anything that must actually be delivered.

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

- **Fusion Memory**: Haitun only consumes an operator-provisioned remote MCP
  Streamable HTTP service. The process starter supplies the token-map path; the
  bearer token defines user identity, while workspace/Session values are only
  provenance. The operator creates tokens, terminates TLS at the reverse proxy,
  and supervises MCP/model services with `systemd` for SSH-disconnect resilience
  and restart after failure. The workspace itself starts one passive writer per
  mapped Session and retries outages without blocking chat. Never commit or log
  a token or token map; do not authenticate from `<feishu_context>`, create a
  local memory service, or use another public memory transport.

- **Fusion Flow Next**: bundled Python runtime; no separate setup.
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
- **Feishu tools**: set `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET` on the **Gateway/Session process** (same app as the Feishu channel). Channel-only env is not enough for scheduled `feishu_message_send`. Reuses the `lark-channel-sdk` dependency; no extra install. If unset, the tools return `ok=false` (not fatal).
- **Feishu user authorization (`feishu_auth`)**: to make authorization **copy-paste-free**, give the
  callback somewhere to land — either set `PSI_OAUTH_CALLBACK_BASE` to a Gateway base URL the user's
  browser can reach (e.g. `https://haitun.example.com`; required for multi-user/phone approval), or
  leave it unset on a single-machine deployment and let the tool listen on the loopback
  callback path `/oauth/callback` at port `17860` (`PSI_OAUTH_LOOPBACK_PORT` to change it).
  **Register the resulting redirect URI in the Feishu console** either way. Setting
  `PSI_FEISHU_REDIRECT_URI` to a non-loopback address forces the manual copy-paste path.
  Feishu China has **no device flow** (`authen/v2/oauth/device_authorization` 404s on every
  Feishu domain), so loopback/relay redirect is the only automatic option.

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

The following remain deliberately included as **future-extension hooks** and are **NOT**
invoked by the current framework — do not "clean them up" as dead code:

- `systems/system.py`: `System.compact_history()`, `System.after_turn()`, and the
  `_run_self_evolution_review` / self-evolution helpers. (The **module-level**
  `compact_history()` is a separate, self-contained implementation and *is* invoked on a
  compaction signal; the identically-named `System` method is not reached from it.)
- `systems/curator.py`, `systems/background_review.py`, `systems/threat_patterns.py`,
  `systems/prompt_constants.py` — standalone modules from the hermes-style design, kept for
  when matching hooks are wired into the framework. They are not imported by `system.py`.

## Smoke test

```bash
uv run python examples/haitun-workspace/systems/system.py   # prints the assembled prompt
```
