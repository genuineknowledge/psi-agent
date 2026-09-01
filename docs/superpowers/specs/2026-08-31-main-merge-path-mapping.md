# origin/main 收线合并 · 路径映射表

## 结论

`origin/main` (`ecd85519`) 已全量并入 `refactor/gateway-workspace-evolution`，**100 条 main 侧改动路径逐条有交代，零丢失**。
结构以新架构为准，内容以同事改动为准。已死目录 `examples/haitun-workspace/` 在索引与磁盘上均为 0 文件。

| 项 | 数字 |
|---|---|
| merge-base | `64b6273b` |
| origin/main（落地前二次核验，未漂移） | `ecd85519` |
| main 侧改动路径 | 100（48 新增 / 52 修改） |
| 其中被搬到新路径 | 58 |
| 其中原地不动 | 42 |
| git 报告的冲突 | 26（1 内容 + 25 file location） |
| git 建议的落点错误 | 14 / 25 |
| git 完全没报、需自查才发现的静默复活 | 10 文件 |
| main 新增文件逐字节一致 | 45 / 48（另 3 处为本次有意改路径） |
| main 新增行存活率 | 52 个修改文件中仅 3 行被有意改写 |
| 有意改写的行合计 | 5 |
| 全量测试 | 57 failed / **1624** passed / 7 skipped（合并前 57 / 1571 / 7） |
| 失败名单与基线逐条 diff | 空（FAILURE-SET-IDENTICAL） |
| gateway 子树 | **318** passed / 2 skipped（合并前 308 / 2） |

失败数 57 是 Windows 平台基线，不是本次回归；passed 涨 53 是 main 带进来的新用例真的在跑。

## 五处有意改写的行

结构上必须跟新架构走，所以 main 的这 5 行原文不能照抄。除这 5 行外，main 的新增内容全部原样落地。

| 文件 | main 原文 | 改为 | 依据 |
|---|---|---|---|
| `.github/macos/build-dmg.sh:25` | `WORKSPACE_SRC="$REPO_ROOT/examples/haitun-workspace"` | `"$REPO_ROOT/agents/feishu"` | main 新增文件，与我方从未重叠，**零冲突静默带进死路径** |
| `.github/workflows/pyinstaller.yml:257` | `printf ... > examples/haitun-workspace/.env` | `> agents/feishu/.env` | 同文件 Windows 侧 `:204` 已是 `agents\feishu\.env` |
| `.github/workflows/pyinstaller.yml:259` | `: > examples/haitun-workspace/.env` | `: > agents/feishu/.env` | 同上 |
| `tests/psi_agent/gateway/test_auth_manager.py:22` | `from psi_agent.gateway._auth_manager import AuthManager, classify_failure` | `from psi_agent.gateway.desktop._auth_manager import ...` | 唯一的内容冲突；取 main 的内容 + 我方的新路径 |
| `src/psi_agent/session/live_agent.py:6` | docstring 引 `examples/haitun-workspace/tools/...` | `agents/feishu/tools/...` | 引用的文件确实在 feishu pack 内 |

`agents/feishu/tests/test_python_run.py:26` 的 docstring 同属此类，是随目录搬迁一并改的（计入 58 条搬迁，不单列）。

## 三类 git 不报的丢失，及处置

冲突清单只覆盖第一类。另两类是本次真正的风险面。

### 一、静默复活死目录（10 文件，git 零冲突）

main 在我方已废弃的路径下**新增**文件时，合并是干净的 —— 目录就这么活回来了。全部搬到 `agents/feishu/skills/`：

- `card-dsl/`：`SKILL.md`、`card.xsd`、`templates/review-card.xml`、`templates/todo-card.xml`
- `company-todo-audit/`、`company-todo-fill-check/`、`company-todo-review/`、`company-todo-sync/`
- `todo-completion-standard/`、`todo-writing-standard/`

其中 `.xsd` / `.xml` 三个模板不被任何 import 或 pytest 覆盖，只能靠清单核对发现。

### 二、git 建议的落点错了 14 / 25

`CONFLICT (file location)` 的建议落点是**按目录统计猜的**。`agents/desktop/tools/` 里恰好一个飞书工具都没有，于是 git 把飞书专属工具全推给了 desktop。

12 个飞书专属工具 `agents/desktop/tools/` → `agents/feishu/tools/`：
`_card_dsl.py`、`_review_card_impl.py`、`_todo_card_impl.py`、`feishu_card_render.py`、`feishu_leave_query.py`、`feishu_mentor_ledger_cycle_table.py`、`feishu_mentor_ledger_ensure.py`、`feishu_review_card_select.py`、`feishu_review_card_send.py`、`feishu_review_input.py`、`feishu_review_reject.py`、`feishu_todo_card_untick.py`

判断依据是文件自身内容，不是名字猜的：docstring 里写着 `GET /open-apis/approval/v4/instances`、飞书卡片 2.0 JSON、导师台账 bitable；同名 skills 全在 `agents/feishu/skills/` 下。

2 个通用工具**两个 pack 都放**：`python_run.py`、`_proc_run.py`（内容只讲 shell 引号层级和子进程输出捕获，零飞书痕迹）。

落点规则是量出来的，不是拍的：`agents/desktop/tools`(85) 是 `agents/feishu/tools`(142) 的**真子集**；85 个同名文件里 84 个逐字节相同，唯一有意的例外是 `_runtime_paths.py`；`agents/desktop/tests/` 不存在。

### 三、双 pack 漂移（1→2 拆分的后遗症）

一个目录拆成两个 pack 时，git 只在其中一侧记 rename，另一侧留成 `A`（新增）。**于是 main 的修改只跟到了 desktop 一侧。**

坐实的硬故障：main 新版 `bash.py` 里 `import _proc_run`，而 `_proc_run.py` 只落到了 desktop；`test_proc_run.py` 却落在 `agents/feishu/tests/`，靠 `parents[1]/tools` 定位被测对象 —— 直接 import 失败。

已 desktop→feishu 同步 4 个文件：`bash.py`(35 行差异)、`_background_process_registry.py`(175)、`background_start.py`(6)、`background_stop.py`(38)。

同步后不变量复核：**87 个同名文件，86 个逐字节相同，仅 `_runtime_paths.py` 有意不同，desktop-only 为 0。**

## 保留的前置条件

- `POST /feishu/auth/login` 仍然**忽略 body 里的 open_id**（`src/psi_agent/gateway/feishu/_routes.py:128`），身份只能由 code 换回来 —— 安全前提未被合并动摇。
- `via_dev_bypass` 保留。
- `scripts/feishu_web_paths.py --check`：清单与源码一致（19 条）。
- `uv.lock` / lockfile 未改动。

## 遗留的死路径引用（有意保留）

`examples[/\\]haitun-workspace` 的剩余命中全部在 `docs/superpowers/` 的历史叙述里（重构报告、改名计划、架构演进与人工验收 spec），加上 `src/psi_agent/gateway/desktop/spa-v2/src/App.tsx:14` 的前后对照注释。**活代码与 CI 里已无死路径引用。**

## 没验到的部分

- **macOS dmg 打包 CI 跑不了** —— 本地没有 macOS，`.github/macos/build-dmg.sh` 的路径修正只做了静态核对（比对同仓库 Windows 侧与 `inno-setup/haitun.iss` 的既有约定），未实跑。
- Windows 平台 57 条失败为既有基线（asyncio 子进程 `NotImplementedError` 等），本次未逐条排查。
- 前端未重建：本次未改 `spa-v2/src/`（`App.tsx` 只动了一行注释），故未跑 `npm run build`。

## 两个坑（供后续复用）

1. **别假定冻结真的封住了。** 首次测量基于 `62bc298c`，量完发现 main 已走到 `ecd85519`（+5 commit），冲突面从 1+7 变成 1+25、静默复活从 2 变成 10 —— 第一次测量整份作废。落地前必须再 `rev-parse` 核对一次。
2. **比对内容必须 `diff --strip-trailing-cr`。** checkout 写出 CRLF，裸 `diff` 会把整文件报成不同，看起来像内容丢了。`feishu_sheet_read_grid.py` 就这么假报过一次，实际 main 的 55 行新增完好。

## 附录: 100 条 main 侧路径逐条落点

`A` = main 新增, `M` = main 修改。`(原地)` = 新架构下路径未变。

```
  M  .github/inno-setup/haitun.iss  ->  (原地)
  M  .github/inno-setup/oss-publish.md  ->  (原地)
  M  .github/inno-setup/upload_to_oss.py  ->  (原地)
  A  .github/macos/.gitattributes  ->  (原地)
  A  .github/macos/Info.plist.in  ->  (原地)
  A  .github/macos/build-dmg.sh  ->  (原地)
  A  .github/macos/entitlements.plist  ->  (原地)
  A  .github/macos/launcher.sh  ->  (原地)
  A  .github/macos/macos-release.md  ->  (原地)
  A  .github/macos/rollback.sh  ->  (原地)
  A  .github/macos/updater.sh  ->  (原地)
  M  .github/workflows/ci.yml  ->  (原地)
  M  .github/workflows/oss-publish.yml  ->  (原地)
  M  .github/workflows/pyinstaller.yml  ->  (原地)
  M  .gitignore  ->  (原地)
  M  AGENTS.md  ->  (原地)
  M  docs/superpowers/specs/2026-08-25-targeted-debug-logging-design.md  ->  (原地)
  M  examples/haitun-workspace/AGENTS.md  ->  agents/feishu/AGENTS.md
  M  examples/haitun-workspace/TOOLS.md  ->  agents/feishu/TOOLS.md
  A  examples/haitun-workspace/skills/card-dsl/SKILL.md  ->  agents/feishu/skills/card-dsl/SKILL.md
  A  examples/haitun-workspace/skills/card-dsl/card.xsd  ->  agents/feishu/skills/card-dsl/card.xsd
  A  examples/haitun-workspace/skills/card-dsl/templates/review-card.xml  ->  agents/feishu/skills/card-dsl/templates/review-card.xml
  A  examples/haitun-workspace/skills/card-dsl/templates/todo-card.xml  ->  agents/feishu/skills/card-dsl/templates/todo-card.xml
  A  examples/haitun-workspace/skills/company-todo-audit/SKILL.md  ->  agents/feishu/skills/company-todo-audit/SKILL.md
  A  examples/haitun-workspace/skills/company-todo-fill-check/SKILL.md  ->  agents/feishu/skills/company-todo-fill-check/SKILL.md
  A  examples/haitun-workspace/skills/company-todo-review/SKILL.md  ->  agents/feishu/skills/company-todo-review/SKILL.md
  A  examples/haitun-workspace/skills/company-todo-sync/SKILL.md  ->  agents/feishu/skills/company-todo-sync/SKILL.md
  M  examples/haitun-workspace/skills/feishu-sheet/SKILL.md  ->  agents/feishu/skills/feishu-sheet/SKILL.md
  M  examples/haitun-workspace/skills/feishu-task/SKILL.md  ->  agents/feishu/skills/feishu-task/SKILL.md
  M  examples/haitun-workspace/skills/feishu-todo-board-sync/SKILL.md  ->  agents/feishu/skills/feishu-todo-board-sync/SKILL.md
  M  examples/haitun-workspace/skills/feishu-todo-card/SKILL.md  ->  agents/feishu/skills/feishu-todo-card/SKILL.md
  A  examples/haitun-workspace/skills/todo-completion-standard/SKILL.md  ->  agents/feishu/skills/todo-completion-standard/SKILL.md
  A  examples/haitun-workspace/skills/todo-writing-standard/SKILL.md  ->  agents/feishu/skills/todo-writing-standard/SKILL.md
  A  examples/haitun-workspace/tests/test_background_output.py  ->  agents/feishu/tests/test_background_output.py
  M  examples/haitun-workspace/tests/test_feishu.py  ->  agents/feishu/tests/test_feishu.py
  A  examples/haitun-workspace/tests/test_feishu_leave_query.py  ->  agents/feishu/tests/test_feishu_leave_query.py
  M  examples/haitun-workspace/tests/test_feishu_sheet_as_data.py  ->  agents/feishu/tests/test_feishu_sheet_as_data.py
  A  examples/haitun-workspace/tests/test_feishu_sheet_find_columns.py  ->  agents/feishu/tests/test_feishu_sheet_find_columns.py
  A  examples/haitun-workspace/tests/test_feishu_sheet_grid_range.py  ->  agents/feishu/tests/test_feishu_sheet_grid_range.py
  A  examples/haitun-workspace/tests/test_feishu_sheet_truncation.py  ->  agents/feishu/tests/test_feishu_sheet_truncation.py
  A  examples/haitun-workspace/tests/test_proc_run.py  ->  agents/feishu/tests/test_proc_run.py
  A  examples/haitun-workspace/tests/test_python_run.py  ->  agents/feishu/tests/test_python_run.py
  A  examples/haitun-workspace/tests/test_todo_completion_standard.py  ->  agents/feishu/tests/test_todo_completion_standard.py
  A  examples/haitun-workspace/tests/test_todo_writing_standard.py  ->  agents/feishu/tests/test_todo_writing_standard.py
  M  examples/haitun-workspace/tools/_background_process_registry.py  ->  agents/feishu/tools/_background_process_registry.py
  A  examples/haitun-workspace/tools/_card_dsl.py  ->  agents/feishu/tools/_card_dsl.py
  M  examples/haitun-workspace/tools/_feishu/auth.py  ->  agents/feishu/tools/_feishu/auth.py
  M  examples/haitun-workspace/tools/_feishu/bitable.py  ->  agents/feishu/tools/_feishu/bitable.py
  A  examples/haitun-workspace/tools/_feishu/leave.py  ->  agents/feishu/tools/_feishu/leave.py
  A  examples/haitun-workspace/tools/_feishu/mentor_ledger.py  ->  agents/feishu/tools/_feishu/mentor_ledger.py
  M  examples/haitun-workspace/tools/_feishu/sheet.py  ->  agents/feishu/tools/_feishu/sheet.py
  M  examples/haitun-workspace/tools/_feishu_impl.py  ->  agents/feishu/tools/_feishu_impl.py
  A  examples/haitun-workspace/tools/_proc_run.py  ->  agents/feishu/tools/_proc_run.py
  A  examples/haitun-workspace/tools/_review_card_impl.py  ->  agents/feishu/tools/_review_card_impl.py
  A  examples/haitun-workspace/tools/_todo_card_impl.py  ->  agents/feishu/tools/_todo_card_impl.py
  M  examples/haitun-workspace/tools/background_start.py  ->  agents/feishu/tools/background_start.py
  M  examples/haitun-workspace/tools/background_stop.py  ->  agents/feishu/tools/background_stop.py
  M  examples/haitun-workspace/tools/bash.py  ->  agents/feishu/tools/bash.py
  M  examples/haitun-workspace/tools/feishu_auth.py  ->  agents/feishu/tools/feishu_auth.py
  M  examples/haitun-workspace/tools/feishu_bitable.py  ->  agents/feishu/tools/feishu_bitable.py
  A  examples/haitun-workspace/tools/feishu_card_render.py  ->  agents/feishu/tools/feishu_card_render.py
  A  examples/haitun-workspace/tools/feishu_leave_query.py  ->  agents/feishu/tools/feishu_leave_query.py
  A  examples/haitun-workspace/tools/feishu_mentor_ledger_cycle_table.py  ->  agents/feishu/tools/feishu_mentor_ledger_cycle_table.py
  A  examples/haitun-workspace/tools/feishu_mentor_ledger_ensure.py  ->  agents/feishu/tools/feishu_mentor_ledger_ensure.py
  A  examples/haitun-workspace/tools/feishu_review_card_select.py  ->  agents/feishu/tools/feishu_review_card_select.py
  A  examples/haitun-workspace/tools/feishu_review_card_send.py  ->  agents/feishu/tools/feishu_review_card_send.py
  A  examples/haitun-workspace/tools/feishu_review_input.py  ->  agents/feishu/tools/feishu_review_input.py
  A  examples/haitun-workspace/tools/feishu_review_reject.py  ->  agents/feishu/tools/feishu_review_reject.py
  M  examples/haitun-workspace/tools/feishu_sheet.py  ->  agents/feishu/tools/feishu_sheet.py
  M  examples/haitun-workspace/tools/feishu_sheet_find_columns.py  ->  agents/feishu/tools/feishu_sheet_find_columns.py
  M  examples/haitun-workspace/tools/feishu_sheet_read_grid.py  ->  agents/feishu/tools/feishu_sheet_read_grid.py
  M  examples/haitun-workspace/tools/feishu_todo_card_tick.py  ->  agents/feishu/tools/feishu_todo_card_tick.py
  A  examples/haitun-workspace/tools/feishu_todo_card_untick.py  ->  agents/feishu/tools/feishu_todo_card_untick.py
  A  examples/haitun-workspace/tools/python_run.py  ->  agents/feishu/tools/python_run.py
  M  pyproject.toml  ->  (原地)
  A  src/psi_agent/_card_markers.py  ->  (原地)
  M  src/psi_agent/_tls.py  ->  (原地)
  M  src/psi_agent/ai/AGENTS.md  ->  (原地)
  M  src/psi_agent/ai/server.py  ->  (原地)
  M  src/psi_agent/channel/AGENTS.md  ->  (原地)
  M  src/psi_agent/channel/_core.py  ->  (原地)
  M  src/psi_agent/channel/_stream.py  ->  (原地)
  M  src/psi_agent/channel/feishu/__init__.py  ->  (原地)
  M  src/psi_agent/channel/feishu/_card_action.py  ->  (原地)
  M  src/psi_agent/channel/feishu/client.py  ->  (原地)
  M  src/psi_agent/channel/telegram/__init__.py  ->  (原地)
  M  src/psi_agent/channel/telegram/client.py  ->  (原地)
  M  src/psi_agent/gateway/_auth_manager.py  ->  src/psi_agent/gateway/desktop/_auth_manager.py
  M  src/psi_agent/session/AGENTS.md  ->  (原地)
  M  src/psi_agent/session/agent.py  ->  (原地)
  A  src/psi_agent/session/live_agent.py  ->  (原地)
  M  src/psi_agent/session/server.py  ->  (原地)
  M  tests/psi_agent/ai/test_server.py  ->  (原地)
  M  tests/psi_agent/channel/test__core.py  ->  (原地)
  M  tests/psi_agent/channel/test__stream.py  ->  (原地)
  M  tests/psi_agent/gateway/test_auth_manager.py  ->  (原地)
  A  tests/psi_agent/session/test_agent_card_direct_dispatch.py  ->  (原地)
  A  tests/psi_agent/session/test_live_agent.py  ->  (原地)
  A  tests/psi_agent/test_tls.py  ->  (原地)
  M  uv.lock  ->  (原地)
```
