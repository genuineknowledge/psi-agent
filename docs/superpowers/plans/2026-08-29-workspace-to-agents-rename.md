# workspace/ → agents/ 改名实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把顶层 `workspace/` 改名为 `agents/`，其下 `tob`/`toc` 改名为 `feishu`/`desktop`，与 `gateway/` 已有的 `feishu/`/`desktop/` 子包命名对齐。

**Architecture:** 纯改名，不动结构。用 `git mv` 分两步（先顶层再子目录）落地目录移动，再逐处修引用。**禁止全局 sed** —— `toc` 在前端是 table-of-contents，`ToC`/`ToB` 是产品线称谓，天真替换会破坏 600+ 处无关内容。

**Tech Stack:** git mv、Python 3.14、pytest、uv build、ruff、ty、Inno Setup、GitHub Actions

## Global Constraints

- **判据统一用分隔符无关的正则**：`git grep -nE 'workspace[/\\](tob|toc)'`。规格原文给的 `workspace/tob` 漏掉 16 处反斜杠写法（已实测）。
- **改名前基线（本计划已实测，直接用）**：非 docs 共 **89 处 / 36 文件**（不是规格里的 73/31）；`docs/` 另有 **255 处 / 17 文件**。
- **必须一字不动**（改错会静默坏）：
  - 9 处 REST 路由字面量 `"/workspace/`（`/workspace/cwd` `/workspace/places` `/workspace/browse` `/workspace/file` `/workspace/reveal`）—— HTTP 接口，改了前端全 404。
  - `default_workspace` 参数名：`src` 22 处、`tests` 5 处（全库非 docs 27 处）。
  - `workspace_root` 字段名：`src` 14 处、`tests` 28 处、`agents` 11 处、`examples` 42 处（全库非 docs 95 处）。
  - `--default-workspace` CLI flag。
  - `SessionManager` 等处 `workspace=` 形参名。
  - `ToC`/`ToB` 产品线称谓：`src` 110 处、`agents` 23 处、`tests` 4 处。
  - `.toc` CSS 类：`gateway/desktop/spa-v2/public/legal.css` 6 处、`privacy.html` 1 处（`privacy.html:22`）。
  - `README.md` / `README_en.md` / `examples/**` 里泛指"能力包"概念的 `workspace/`（如 `workspace/histories/`、`workspace/tools/`）—— 指任意能力包目录，不是顶层目录名。
- **docs/ 处理规则（负责人已定：沿用 a9099a25 先例）**：描述当前代码位置的**活引用**全改；讲述改名历史的**叙事**保持原样。判据 1 的零命中例外是一份显式白名单（见 Task 6）。
- **边界**：不动 `.haitun` AppData（已推后）、不动启动参数（另一任务）、不合并 main（另一任务）。

---

## File Structure

改名涉及三类改动，按类切任务：

| 类别 | 文件 | 说明 |
|---|---|---|
| 目录移动 | `workspace/tob` → `agents/feishu`，`workspace/toc` → `agents/desktop` | 752 个 tracked 文件，`git mv` 两步 |
| 内核与测试引用 | `gateway/_defaults.py`(4) `gateway/__init__.py`(1) `gateway/_openapi_core.py`(1) `gateway/AGENTS.md`(9) `runtime/AGENTS.md`(1) `gateway/feishu/_oauth_manager.py`(1) `_openapi.py`(1) `_routes.py`(1) `desktop/spa-v2/AGENTS.md`(1) `desktop/spa-v2/src/App.tsx`(1) + 4 个测试文件 | 含 3 处 glob 参数化（最易静默失败） |
| 打包与 CI | `pyproject.toml`(5) `.github/inno-setup/haitun.iss`(4) `haitun.c`(1) `build-haitun-launcher.ps1`(1) `oss-publish.md`(4) `.github/workflows/pyinstaller.yml`(6) `nuitka.yml`(3) `workflow.yml`(1) `.github/CODEOWNERS`(1) `scripts/dev-feishu.ps1`(1) | 反斜杠写法集中在这里 |
| 包内文档 | `agents/feishu/**`(10) `agents/desktop/**`(23) `AGENTS.md`(2) `examples/haitun-supervisor-workspace/README.md`(3) | 移动后路径变了，内容里的自引用还得改 |

---

### Task 1: 建立基线快照

改名前把所有判据的基线数字量出来存盘。**改名后所有验收都 diff 这些文件**，不靠记忆。

**Files:**
- Create: `/tmp/rename-baseline-nondocs.txt`（非 docs 逐文件计数）
- Create: `/tmp/rename-baseline-invariants.txt`（不变量计数）
- Create: `/tmp/rename-baseline-failures.txt`（pytest 失败集合）
- Create: `/tmp/rename-baseline-sdist.txt`（sdist 内 workspace 条目数）

**Interfaces:**
- Produces: 上述 4 个基线文件，Task 6/7/8 逐条 diff 它们。

- [ ] **Step 1: 确认工作树干净、确认在正确的 worktree**

```bash
cd F:/code/psi-agent/.kanban/worktrees/848e4/psi-agent
git status --porcelain          # 期望：空
git rev-parse --short HEAD      # 期望：abde791f（或其后代）
ls -d agents 2>/dev/null && echo "FATAL: agents/ 已存在，先查清楚" || echo "ok: 无 agents/"
```

- [ ] **Step 2: 存非 docs 逐文件计数基线**

```bash
git grep -oE 'workspace[/\\](tob|toc)' -- . ':!docs' \
  | cut -d: -f1 | sort | uniq -c | awk '{print $2" "$1}' | sort \
  > /tmp/rename-baseline-nondocs.txt
wc -l < /tmp/rename-baseline-nondocs.txt        # 期望：36
awk '{s+=$2} END {print s}' /tmp/rename-baseline-nondocs.txt   # 期望：89
```

- [ ] **Step 3: 存不变量基线**

```bash
{
  echo "rest_routes $(git grep -o '\"/workspace/' -- src | wc -l)"
  echo "default_workspace_src $(git grep -o 'default_workspace' -- src | wc -l)"
  echo "default_workspace_tests $(git grep -o 'default_workspace' -- tests | wc -l)"
  echo "workspace_root_src $(git grep -o 'workspace_root' -- src | wc -l)"
  echo "workspace_root_tests $(git grep -o 'workspace_root' -- tests | wc -l)"
  echo "toc_tob_caps_src $(git grep -o 'ToC\|ToB' -- src | wc -l)"
  echo "css_toc_class $(git grep -o '\.toc' -- 'src/psi_agent/gateway/desktop/spa-v2/public/legal.css' | wc -l)"
  echo "privacy_toc $(git grep -o 'toc' -- '*privacy.html' | wc -l)"
} > /tmp/rename-baseline-invariants.txt
cat /tmp/rename-baseline-invariants.txt
```

期望内容（本计划已实测）：

```
rest_routes 9
default_workspace_src 22
default_workspace_tests 5
workspace_root_src 14
workspace_root_tests 28
toc_tob_caps_src 110
css_toc_class 6
privacy_toc 1
```

- [ ] **Step 4: 存 pytest 失败集合基线（控制实验）**

工作树此刻已是干净的 HEAD，所以这次跑出来的就是当次基线。`PYTHONPATH=src` 必须带 —— 否则测的是主 checkout 的 src。

```bash
PYTHONPATH=src uv run --no-cache pytest -p no:randomly -q --no-cov 2>&1 \
  | grep -E '^(FAILED|ERROR)' | sort > /tmp/rename-baseline-failures.txt
wc -l < /tmp/rename-baseline-failures.txt   # 期望：57（Windows 基线 57-62 浮动）
```

- [ ] **Step 5: 存 sdist 打包基线**

```bash
rm -rf /tmp/distchk && uv build --out-dir /tmp/distchk 2>&1 | tail -2
python - <<'PY' > /tmp/rename-baseline-sdist.txt
import tarfile, glob, os
d = os.path.expandvars(r'C:\Users\Shengdi\AppData\Local\Temp\distchk')
d = d if os.path.isdir(d) else '/tmp/distchk'
for s in glob.glob(os.path.join(d, '*.tar.gz')):
    names = tarfile.open(s).getnames()
    print('sdist_total', len(names))
    print('sdist_pack_files', len([m for m in names if '/workspace/' in m or '/agents/' in m]))
PY
cat /tmp/rename-baseline-sdist.txt
```

期望：`sdist_total 1423`、`sdist_pack_files 752`。

- [ ] **Step 6: 记下 13 个 workspace 参数化基线**

```bash
PYTHONPATH=src uv run --no-cache pytest -p no:randomly -o testpaths= \
  tests/psi_agent/session/test_workspace_hook_contract.py --collect-only -q --no-cov 2>&1 \
  | grep -oE '\[[^]]*\]' | tr -d '[]' | sed 's/-asyncio//;s/asyncio-//' | sort -u \
  > /tmp/rename-baseline-params.txt
wc -l < /tmp/rename-baseline-params.txt   # 期望：13
grep -cE '^(tob|toc)$' /tmp/rename-baseline-params.txt   # 期望：2
```

- [ ] **Step 7: 无需 commit（基线文件在 /tmp，不入库）**

确认工作树仍干净：`git status --porcelain` 期望空输出。

---

### Task 2: 顶层 `workspace/` → `agents/`

**Files:**
- Move: `workspace/` → `agents/`（752 个 tracked 文件）

**Interfaces:**
- Consumes: Task 1 的基线文件。
- Produces: `agents/tob/`、`agents/toc/` 两个目录（子目录名下一任务再改）。

- [ ] **Step 1: 执行顶层改名**

`git mv` 只搬 tracked 文件。已实测有 1 个 ignored 目录（`workspace/tob/tools/__pycache__/`）不会被搬走——那是构建产物，留在原地无害，但要顺手清掉避免留个空 `workspace/`。

```bash
git mv workspace agents
git status --porcelain | head -5
git status --porcelain | wc -l    # 期望：752
```

- [ ] **Step 2: 清掉可能残留的空目录**

```bash
ls -R workspace 2>/dev/null && rm -rf workspace || echo "ok: workspace/ 已不存在"
ls -d agents/tob agents/toc      # 两者都应存在
```

- [ ] **Step 3: 确认 git 认出这是改名而非删了重加**

```bash
git diff --cached -M --stat | tail -3
git diff --cached -M --name-status | grep -c '^R'   # 期望：752
```

若 `^R` 数远小于 752，停下来报告 —— 说明 rename 检测没生效，合并 main 时会丢历史。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "refactor(agents): 顶层 workspace/ 改名 agents/, 752 文件 git mv 全部识别为 rename"
```

- [ ] **Step 5: 确认 rename 检测在 commit 后仍成立**

```bash
git show --name-status -M HEAD | grep -c '^R'   # 期望：752
```

---

### Task 3: `tob`/`toc` → `feishu`/`desktop`

**Files:**
- Move: `agents/tob` → `agents/feishu`
- Move: `agents/toc` → `agents/desktop`

**Interfaces:**
- Consumes: Task 2 产出的 `agents/tob`、`agents/toc`。
- Produces: `agents/feishu/`、`agents/desktop/` —— 后续所有引用修改的目标路径。

- [ ] **Step 1: 执行子目录改名**

```bash
git mv agents/tob agents/feishu
git mv agents/toc agents/desktop
ls -1 agents/          # 期望：desktop 与 feishu 两项
```

- [ ] **Step 2: 确认两次叠加改名后 rename 检测仍然成立**

规格已实测两次叠加不破坏检测（684 个 rename 全识别为 R100/R097）。这里对本次实际文件数复核：

```bash
git diff --cached -M --name-status | grep -c '^R'   # 期望：752
git diff --cached -M --name-status | grep -c '^R100'  # 绝大多数应是 R100
```

- [ ] **Step 3: 提交**

```bash
git add -A
git commit -m "refactor(agents): tob/toc 改名 feishu/desktop, 与 gateway 子包命名对齐"
```

- [ ] **Step 4: 确认此刻引用全断（预期状态，不是错误）**

```bash
git grep -cE 'workspace[/\\](tob|toc)' -- . ':!docs' | wc -l   # 仍有命中：引用还没改
ls agents/feishu/systems/system.py agents/desktop/systems/system.py  # 两个文件都应存在
```

---

### Task 4: 修 3 处 glob 参数化（最易静默失败）

**这是本任务最容易静默失败的地方。** 三个测试文件用 `Path("workspace").glob("*/systems/system.py")` 做参数化；改名后 glob 静默返回 0 命中，测试不报错，只是参数从 13 个变成 11 个。

**Files:**
- Modify: `tests/psi_agent/session/test_compaction_prompt_injection.py:26`
- Modify: `tests/psi_agent/session/test_compact_history_chaining.py:19`
- Modify: `tests/psi_agent/session/test_workspace_hook_contract.py:32`

**Interfaces:**
- Consumes: Task 3 产出的 `agents/feishu`、`agents/desktop`。
- Produces: 参数化仍为 13 项（11 个 examples + `feishu` + `desktop`）。

- [ ] **Step 1: 先证明它现在是坏的（静默降到 11）**

```bash
PYTHONPATH=src uv run --no-cache pytest -p no:randomly -o testpaths= \
  tests/psi_agent/session/test_workspace_hook_contract.py --collect-only -q --no-cov 2>&1 \
  | grep -oE '\[[^]]*\]' | tr -d '[]' | sed 's/-asyncio//;s/asyncio-//' | sort -u | wc -l
```

期望：**11** —— 且 pytest 退出码为 0、不报任何错。这正是"静默失败"的样子，看一眼再修。

- [ ] **Step 2: 三个文件各改一行**

三处那一行当前完全相同：

```python
WORKSPACES = sorted([*Path("examples").glob("*/systems/system.py"), *Path("workspace").glob("*/systems/system.py")])
```

改成：

```python
WORKSPACES = sorted([*Path("examples").glob("*/systems/system.py"), *Path("agents").glob("*/systems/system.py")])
```

- [ ] **Step 3: 确认三处都改到，且回到 13**

```bash
git grep -n 'Path("agents").glob' -- tests | wc -l    # 期望：3
git grep -n 'Path("workspace").glob' -- tests | wc -l # 期望：0

for f in test_workspace_hook_contract test_compaction_prompt_injection test_compact_history_chaining; do
  n=$(PYTHONPATH=src uv run --no-cache pytest -p no:randomly -o testpaths= \
    tests/psi_agent/session/$f.py --collect-only -q --no-cov 2>&1 \
    | grep -oE '\[[^]]*\]' | tr -d '[]' | sed 's/-asyncio//;s/asyncio-//' | sort -u | wc -l)
  echo "$f params=$n"
done
```

三个都必须是 **13**。

- [ ] **Step 4: 确认参数名里出现 feishu/desktop、不再有 tob/toc**

```bash
PYTHONPATH=src uv run --no-cache pytest -p no:randomly -o testpaths= \
  tests/psi_agent/session/test_workspace_hook_contract.py --collect-only -q --no-cov 2>&1 \
  | grep -oE '\[[^]]*\]' | tr -d '[]' | sed 's/-asyncio//;s/asyncio-//' | sort -u \
  > /tmp/rename-after-params.txt
diff <(sed 's/^tob$/feishu/;s/^toc$/desktop/' /tmp/rename-baseline-params.txt | sort) \
     <(sort /tmp/rename-after-params.txt) && echo "PARAMS OK"
```

- [ ] **Step 5: 跑这三个文件确认通过**

```bash
PYTHONPATH=src uv run --no-cache pytest -p no:randomly -o testpaths= \
  tests/psi_agent/session/test_workspace_hook_contract.py \
  tests/psi_agent/session/test_compaction_prompt_injection.py \
  tests/psi_agent/session/test_compact_history_chaining.py -q --no-cov 2>&1 | tail -5
```

- [ ] **Step 6: 提交**

```bash
git add tests/psi_agent/session/test_workspace_hook_contract.py \
        tests/psi_agent/session/test_compaction_prompt_injection.py \
        tests/psi_agent/session/test_compact_history_chaining.py
git commit -m "fix(tests): glob 改指 agents/, 参数化从静默的 11 回到 13"
```

---

### Task 5: 修内核、测试、打包、CI 的 89 处引用

逐处判断，**禁止全局 sed**。分 4 组做，每组做完立刻验。

**Files:**
- Modify: `src/psi_agent/gateway/_defaults.py`(4) `__init__.py`(1) `_openapi_core.py`(1) `AGENTS.md`(9) `feishu/_oauth_manager.py`(1) `feishu/_openapi.py`(1) `feishu/_routes.py`(1) `desktop/spa-v2/AGENTS.md`(1) `desktop/spa-v2/src/App.tsx`(1) `src/psi_agent/runtime/AGENTS.md`(1)
- Modify: `tests/psi_agent/gateway/test_defaults.py`(1) `test_openapi.py`(1) `tests/psi_agent/session/test_compaction_prompt_injection.py`(1) `test_workspace_hook_contract.py`(1)
- Modify: `pyproject.toml`(5) `.github/inno-setup/haitun.iss`(4) `haitun.c`(1) `build-haitun-launcher.ps1`(1) `oss-publish.md`(4) `.github/workflows/pyinstaller.yml`(6) `nuitka.yml`(3) `workflow.yml`(1) `.github/CODEOWNERS`(1) `scripts/dev-feishu.ps1`(1)
- Modify: `AGENTS.md`(2) `examples/haitun-supervisor-workspace/README.md`(3) `agents/feishu/AGENTS.md`(1) `README.md`(3) `docs/haibao-integration.md`(4) `skills/psi-agent-help/SKILL.md`(1) `tools/_runtime_paths.py`(1) `agents/desktop/AGENTS.md`(7) `README.md`(8) `TOOLS.md`(2) `docs/haibao-integration.md`(4) `tools/_runtime_paths.py`(1)

**Interfaces:**
- Consumes: Task 3 的 `agents/feishu`、`agents/desktop`。
- Produces: 非 docs 零命中（Task 6 验收）。

映射规则（全组通用）：`workspace/tob` → `agents/feishu`，`workspace/toc` → `agents/desktop`，反斜杠写法 `workspace\tob` → `agents\feishu`（**保留反斜杠**，那是 Windows/Inno Setup 路径）。

- [ ] **Step 1: 第 1 组 —— 内核 src（20 处，含唯一的硬编码候选）**

核心一处，`src/psi_agent/gateway/_defaults.py:66`：

```python
DEFAULT_AGENT_REPO_CANDIDATE = "agents/feishu"
```

同文件 14/25/103 行的 docstring 一并改。其余 src 文件逐处改。改完验：

```bash
git grep -nE 'workspace[/\\](tob|toc)' -- src   # 期望：零输出
git grep -n 'DEFAULT_AGENT_REPO_CANDIDATE' -- src/psi_agent/gateway/_defaults.py
```

**同时确认不变量没被碰**：

```bash
echo "rest=$(git grep -o '"/workspace/' -- src | wc -l) (期望 9)"
echo "dw=$(git grep -o 'default_workspace' -- src | wc -l) (期望 22)"
echo "wr=$(git grep -o 'workspace_root' -- src | wc -l) (期望 14)"
echo "caps=$(git grep -o 'ToC\|ToB' -- src | wc -l) (期望 110)"
echo "css=$(git grep -o '\.toc' -- 'src/psi_agent/gateway/desktop/spa-v2/public/legal.css' | wc -l) (期望 6)"
```

- [ ] **Step 2: 第 1 组提交**

```bash
git add src/
git commit -m "refactor(gateway): 内核 20 处引用改指 agents/feishu, 硬编码候选收在 _defaults.py:66"
```

- [ ] **Step 3: 第 2 组 —— 测试的 4 处注释/docstring**

这 4 处都是注释或字符串描述，不是可执行路径（可执行的 glob 已在 Task 4 改完）：

- `tests/psi_agent/gateway/test_defaults.py:74` docstring：`prefer workspace/tob` → `prefer agents/feishu`
- `tests/psi_agent/gateway/test_openapi.py:63` 注释：`workspace/tob/tools` → `agents/feishu/tools`
- `tests/psi_agent/session/test_compaction_prompt_injection.py:49` 注释：`Verbatim from workspace/tob/HEARTBEAT.md` → `agents/feishu/HEARTBEAT.md`
- `tests/psi_agent/session/test_workspace_hook_contract.py:87` 注释：`workspace/tob resolves 0 of 6` → `agents/feishu`

```bash
git grep -nE 'workspace[/\\](tob|toc)' -- tests   # 期望：零输出
echo "dw=$(git grep -o 'default_workspace' -- tests | wc -l) (期望 5)"
echo "wr=$(git grep -o 'workspace_root' -- tests | wc -l) (期望 28)"
git add tests/ && git commit -m "docs(tests): 4 处注释路径改指 agents/feishu"
```

- [ ] **Step 4: 第 3 组 —— 打包与 CI（27 处，反斜杠集中在这里）**

`pyproject.toml` 5 处（**是 ruff/ty 配置，不是打包配置**，但改错会静默丢 lint 排除）：

```toml
[tool.ruff]
extend-exclude = [
    "agents/feishu/skills/workflow/fusion_flow/generated",
    "agents/desktop/skills/workflow/fusion_flow/generated",
]

[tool.ty.environment]
extra-paths = ["agents/feishu/skills/workflow"]

[tool.ty.src]
exclude = [
    "agents/feishu/skills/workflow/fusion_flow/generated",
    "agents/desktop/skills/workflow/fusion_flow/generated",
]
```

反斜杠组（保留反斜杠）：
- `.github/inno-setup/haitun.iss` 64/66/72/77 行：`..\..\workspace\tob\*` → `..\..\agents\feishu\*`
- `.github/workflows/pyinstaller.yml` 98/111/114/132/155/156 行
- `.github/workflows/nuitka.yml` 189/202/205 行
- `.github/inno-setup/build-haitun-launcher.ps1:5`：`Join-Path $RepoRoot 'workspace\tob'` → `'agents\feishu'`
- `scripts/dev-feishu.ps1:13`：同上

正斜杠组：
- `.github/CODEOWNERS:13`：`workspace/tob/` → `agents/feishu/`
- `.github/workflows/workflow.yml:22`：`working-directory: workspace/tob/skills/workflow` → `agents/feishu/skills/workflow`
- `.github/inno-setup/haitun.c:649` 注释、`oss-publish.md` 4 处

- [ ] **Step 5: 验第 3 组 —— ruff 排除仍生效**

那两个 generated 目录现有 415 个 lint 错误，排除失效会立刻暴露：

```bash
uv run ruff check . 2>&1 | tail -3        # 不应冒出 fusion_flow/generated 的几百个错
uv run ruff check . 2>&1 | grep -c 'fusion_flow/generated'   # 期望：0
```

- [ ] **Step 6: 第 3 组提交**

```bash
git add pyproject.toml .github/ scripts/
git commit -m "build(agents): 打包与 CI 27 处改指 agents/feishu, 含 16 处反斜杠写法"
```

- [ ] **Step 7: 第 4 组 —— 包内与根文档（38 处）**

`agents/feishu/**`、`agents/desktop/**`、根 `AGENTS.md`(2)、`examples/haitun-supervisor-workspace/README.md`(3)。

注意 `agents/desktop/tools/_runtime_paths.py:31` 的 docstring 现在写的是 `workspace/tob`（**desktop 包里写着 tob，是上次抽包时抄漏的**）—— 这里应改成 `agents/desktop`，不是 `agents/feishu`。`agents/feishu/tools/_runtime_paths.py:31` 改成 `agents/feishu`。

```bash
git grep -nE 'workspace[/\\](tob|toc)' -- agents AGENTS.md examples   # 期望：零输出
git grep -n '_runtime_paths' -- agents/*/tools/_runtime_paths.py >/dev/null
git grep -n 'agents/desktop' -- agents/desktop/tools/_runtime_paths.py   # 应命中
echo "caps_agents=$(git grep -o 'ToC\|ToB' -- agents | wc -l) (期望 23)"
echo "wr_agents=$(git grep -o 'workspace_root' -- agents | wc -l) (期望 11)"
git add agents/ AGENTS.md examples/
git commit -m "docs(agents): 包内与根文档 38 处自引用改指 agents/feishu 与 agents/desktop"
```

---

### Task 6: docs/ 活引用改名 + 叙事白名单

负责人已定：**改活引用，留叙事**（沿用 a9099a25 先例 —— 上次重写 15 个 docs 文件、故意留 6 处叙事性旧路径）。

**Files:**
- Modify: `docs/superpowers/plans/*.md`（7 个文件，156 处）
- Modify: `docs/superpowers/specs/*.md` 与 `.html`（10 个文件，99 处）
- Create: `docs/superpowers/plans/2026-08-29-workspace-to-agents-rename-whitelist.md`（叙事白名单）

**Interfaces:**
- Consumes: Task 5 完成后的非 docs 零命中状态。
- Produces: 白名单文件 —— 判据 1 的唯一例外清单。

分类规则：
- **活引用（改）**：句子在说"某文件现在在哪"。例：`- \`workspace/tob/tools/_feishu_impl.py\` — 共享实现层` → 改。
- **叙事（留）**：句子在讲上一次改名的前后对照，或引用了历史 commit message。改了句子会自相矛盾。

- [ ] **Step 1: 先固定叙事白名单（本计划已实测出候选）**

已确认的叙事处（含 `examples/haitun-workspace` 前后对照或 commit 引用）：

- `docs/superpowers/specs/2026-08-28-gateway-workspace-refactor-report.md:74`（目录树前后对照，`examples\haitun-workspace\*` ↔ `workspace\tob\*`）
- 同文件 `:83`（表格"ToB 能力包文件数 | `examples/haitun-workspace` 485 | `workspace/tob` **486**"）
- 同文件 `:151`（表格"B1/B2 | `examples/haitun-workspace` → `workspace/tob`"）
- 同文件 `:501`（引用 commit message 原文 `a9099a25 refactor(workspace): haitun-workspace 迁出 examples 为 workspace/tob`）
- `docs/superpowers/specs/2026-08-28-gateway-workspace-refactor-report.html:249`、`:261`、`:385`（同上三类的 HTML 对应处）

另需逐条判断的：报告里"抽出 `workspace/toc` —— 已做，`80b54129`"这类**引用历史 commit 的完成记录**（`report.md:346` 等）也算叙事。

把最终清单写进白名单文件，每处附文件:行号与保留理由。

- [ ] **Step 2: 写白名单文件**

```markdown
# workspace/ → agents/ 改名：docs/ 叙事保留白名单

判据 1（`git grep -nE 'workspace[/\\](tob|toc)'` 零命中）的唯一例外。
保留理由统一为：句子本身在讲述**上一次**改名（`examples/haitun-workspace` → `workspace/tob`）
或引用历史 commit message 原文，改了会让记录自相矛盾、历史失真。
沿用 a9099a25 的先例（那次同样保留了 6 处叙事性旧路径）。

| 文件:行 | 内容性质 | 保留理由 |
|---|---|---|
| `specs/2026-08-28-...report.md:74` | 目录树前后对照 | 左列旧路径右列新路径，改右列则两列相同 |
| `specs/2026-08-28-...report.md:83` | 文件数对照表 | 同上 |
| `specs/2026-08-28-...report.md:151` | B1/B2 变更表 | 记录的是那一轮做了什么 |
| `specs/2026-08-28-...report.md:501` | commit message 引文 | 引文须与 git 历史逐字一致 |
| `specs/2026-08-28-...report.html:249` | 叙述段 | md 对应处的 HTML 同步 |
| `specs/2026-08-28-...report.html:261` | 目录树前后对照 | 同 md:74 |
| `specs/2026-08-28-...report.html:385` | B1/B2 变更表 | 同 md:151 |
```

（执行时按 Step 1 的实际判断补齐剩余行。）

- [ ] **Step 3: 改 docs 活引用**

逐文件处理，7 个 plans + 10 个 specs。plans 里绝大多数是文件清单式的活引用（如 `2026-07-10-feishu-tools.md` 的 47 处基本全是活引用），全改。

- [ ] **Step 4: 验 —— 剩余命中恰好等于白名单**

```bash
git grep -nE 'workspace[/\\](tob|toc)' -- docs | cut -d: -f1,2 | sort > /tmp/docs-remaining.txt
cat /tmp/docs-remaining.txt
wc -l < /tmp/docs-remaining.txt
```

逐行核对：每一行都必须在白名单表里。有任何一行不在 → 那是漏改的活引用，回 Step 3。

- [ ] **Step 5: 提交**

```bash
git add docs/
git commit -m "docs: 活引用改指 agents/, 叙事性旧路径按白名单保留"
```

---

### Task 7: 全量验收

逐条对 Task 1 的基线做 diff。**判据是集合一致，不是全绿。**

**Files:**
- 无修改（纯验证）；如发现问题回对应任务修。

**Interfaces:**
- Consumes: Task 1 的 4 个基线文件 + Task 6 的白名单。

- [ ] **Step 1: 判据 1 —— 零命中（docs 例外按白名单）**

```bash
echo "--- 非 docs（必须零输出）"
git grep -nE 'workspace[/\\](tob|toc)' -- . ':!docs'
echo "--- docs 剩余（必须全在白名单）"
git grep -nE 'workspace[/\\](tob|toc)' -- docs | cut -d: -f1,2
echo "--- 顶层 workspace/ 目录不复存在"
ls -d workspace 2>/dev/null && echo FAIL || echo ok
```

- [ ] **Step 2: 判据 2 —— 新路径处数逐文件对齐基线**

```bash
git grep -oE 'agents[/\\](feishu|desktop)' -- . ':!docs' \
  | cut -d: -f1 | sort | uniq -c | awk '{print $2" "$1}' | sort > /tmp/rename-after-nondocs.txt

# 基线里的旧文件名要先映射到新文件名再比
sed 's|^workspace/tob|agents/feishu|; s|^workspace/toc|agents/desktop|' \
  /tmp/rename-baseline-nondocs.txt | sort > /tmp/rename-baseline-mapped.txt

diff /tmp/rename-baseline-mapped.txt /tmp/rename-after-nondocs.txt \
  && echo "判据2 PASS：36 文件逐文件处数一致，共 89 处"
```

差异必须能逐条解释（例如 `agents/desktop/tools/_runtime_paths.py` 那处原本错写成 `tob`，修正后归属未变、处数未变）。

- [ ] **Step 3: 判据 3/4/5 —— 不变量一字未动**

```bash
{
  echo "rest_routes $(git grep -o '\"/workspace/' -- src | wc -l)"
  echo "default_workspace_src $(git grep -o 'default_workspace' -- src | wc -l)"
  echo "default_workspace_tests $(git grep -o 'default_workspace' -- tests | wc -l)"
  echo "workspace_root_src $(git grep -o 'workspace_root' -- src | wc -l)"
  echo "workspace_root_tests $(git grep -o 'workspace_root' -- tests | wc -l)"
  echo "toc_tob_caps_src $(git grep -o 'ToC\|ToB' -- src | wc -l)"
  echo "css_toc_class $(git grep -o '\.toc' -- 'src/psi_agent/gateway/desktop/spa-v2/public/legal.css' | wc -l)"
  echo "privacy_toc $(git grep -o 'toc' -- '*privacy.html' | wc -l)"
} > /tmp/rename-after-invariants.txt
diff /tmp/rename-baseline-invariants.txt /tmp/rename-after-invariants.txt \
  && echo "判据3/4/5 PASS"
echo "--- CLI flag 仍在"
git grep -c '\-\-default-workspace' -- src/psi_agent/gateway/_defaults.py src/psi_agent/runtime/_session_manager.py
```

- [ ] **Step 4: 判据 6 —— 参数化仍是 13**

```bash
for f in test_workspace_hook_contract test_compaction_prompt_injection test_compact_history_chaining; do
  n=$(PYTHONPATH=src uv run --no-cache pytest -p no:randomly -o testpaths= \
    tests/psi_agent/session/$f.py --collect-only -q --no-cov 2>&1 \
    | grep -oE '\[[^]]*\]' | tr -d '[]' | sed 's/-asyncio//;s/asyncio-//' | sort -u | wc -l)
  echo "$f params=$n （必须 13）"
done
```

- [ ] **Step 5: 判据 7 —— pytest 失败集合与基线逐条相同**

```bash
PYTHONPATH=src uv run --no-cache pytest -p no:randomly -q --no-cov 2>&1 \
  | grep -E '^(FAILED|ERROR)' | sort > /tmp/rename-after-failures.txt
diff /tmp/rename-baseline-failures.txt /tmp/rename-after-failures.txt \
  && echo "判据7 PASS：失败集合逐条一致"
wc -l < /tmp/rename-after-failures.txt
```

已知 flaky：硬编码命名管道被残留进程占用会让失败数在 57-62 间浮动。若 diff 只在管道类用例上有出入，重跑一次确认；**其他任何新增失败都算回归**，必须定位。

- [ ] **Step 6: 判据 8 —— gateway --help 与 sdist 打包**

```bash
PYTHONPATH=src uv run --no-cache psi-agent gateway --help 2>&1 | head -5

rm -rf /tmp/distchk && uv build --out-dir /tmp/distchk 2>&1 | tail -2
python - <<'PY' > /tmp/rename-after-sdist.txt
import tarfile, glob, os
d = os.path.expandvars(r'C:\Users\Shengdi\AppData\Local\Temp\distchk')
d = d if os.path.isdir(d) else '/tmp/distchk'
for s in glob.glob(os.path.join(d, '*.tar.gz')):
    names = tarfile.open(s).getnames()
    print('sdist_total', len(names))
    print('sdist_pack_files', len([m for m in names if '/workspace/' in m or '/agents/' in m]))
PY
diff /tmp/rename-baseline-sdist.txt /tmp/rename-after-sdist.txt \
  && echo "判据8 PASS：sdist 内能力包文件仍 752 个，一个没漏"
```

补充确认产物里路径确实叫 `agents/`：

```bash
python - <<'PY'
import tarfile, glob, os
d = os.path.expandvars(r'C:\Users\Shengdi\AppData\Local\Temp\distchk')
d = d if os.path.isdir(d) else '/tmp/distchk'
for s in glob.glob(os.path.join(d, '*.tar.gz')):
    n = tarfile.open(s).getnames()
    print('agents/feishu 条目', len([m for m in n if '/agents/feishu/' in m]))
    print('agents/desktop 条目', len([m for m in n if '/agents/desktop/' in m]))
    print('残留 workspace/ 条目（须 0）', len([m for m in n if '/workspace/' in m]))
PY
rm -rf /tmp/distchk "C:/Users/Shengdi/AppData/Local/Temp/distchk"
```

- [ ] **Step 7: ruff / ty 仍覆盖到能力包**

```bash
uv run ruff check . 2>&1 | grep -c 'fusion_flow/generated'   # 期望：0（排除仍生效）
uv run ruff check agents/ 2>&1 | tail -3
```

- [ ] **Step 8: rename 历史完好（合并 main 前的关键确认）**

```bash
git log --oneline -6
git diff -M --name-status main...HEAD 2>/dev/null | grep -c '^R' || \
  git diff -M --name-status HEAD~5..HEAD | grep -c '^R'
```

`^R` 应在 752 量级。若变成成百上千的 A/D 对，停下报告 —— 合并 main 会丢文件历史。

- [ ] **Step 9: 工作树干净、无遗漏文件**

```bash
git status --porcelain          # 期望：空
git status --porcelain --ignored agents | head
```

---

### Task 8: 通知负责人

负责人明确要求：改完立刻通知，因为合并 main 的任务排在这之后。

- [ ] **Step 1: 汇总一份结论先行的验收报告**

包含（结论放最前）：
- 改名已落地：`workspace/` → `agents/`，`tob`/`toc` → `feishu`/`desktop`，752 文件全部识别为 git rename。
- 8 条判据逐条实测结果（PASS/异常）。
- **两处规格修正**（必须点名说明）：
  1. 规格给的 grep `workspace/tob` 漏掉 **16 处反斜杠写法**，含安装器 `haitun.iss`(4) 与两个 Windows 打包 workflow(9)。真实基线是 **89 处 / 36 文件**，不是 73/31。已按负责人决定一并改，判据换成 `workspace[/\\](tob|toc)`。
  2. 规格说 `pyproject.toml` 5 处是打包配置、"改错会打包漏文件"—— 实测那 5 处是 **ruff/ty 的 lint 与类型检查配置**。真正的打包事实是：wheel 只打 `src/psi_agent`（0 个能力包文件），sdist 靠默认全量收录打进全部 **752** 个能力包文件。打包判据已按此改为"sdist 内能力包文件仍 752 个 + ruff/ty 仍覆盖"。
- docs/ 处置：活引用已改，叙事按白名单保留（白名单文件路径）。
- 未做的部分：`.haitun` AppData、启动参数、合并 main —— 均在边界外。

- [ ] **Step 2: 明确交回下一个任务**

告知合并 main 的任务现在可以开始，并提示 rename 检测已验证（`^R` 752），合并不会丢历史。

---

## Self-Review

**1. 判据覆盖**：规格 8 条判据 → Task 7 Step 1-6 逐条对应；判据 6（glob 静默失败）额外拿 Task 4 单独处理并先证明它是坏的；判据 8 按负责人决定改为 sdist 752 + ruff/ty 覆盖。

**2. 陷阱覆盖**：`.toc` CSS（Task 1 Step 3 存基线 / Task 7 Step 3 验）、`ToC`/`ToB` 称谓（同上）、禁止全局 sed（Global Constraints + Task 5 分 4 组逐处改）、9 处 REST 路由与 `default_workspace`/`workspace_root`（Global Constraints 列出实测分项数 + Task 5 每组验 + Task 7 Step 3 总验）。

**3. 规格数字修正已在计划内显式标注**：89/36 取代 73/31；反斜杠 16 处纳入范围；pyproject 5 处的真实性质。基线以 Task 1 实测为准，不用记忆数字。

**4. 与实测不符处**：规格称 `default_workspace` 19 处、`workspace_root` 11 处 —— 实测全库非 docs 分别为 27 和 95（`workspace_root` 的 11 处只是 `agents/` 子树那一部分）。计划改为按子树分项记数，避免用一个对不上的总数当判据。

---

## 附录：一条命令的完整验收

```bash
cd F:/code/psi-agent/.kanban/worktrees/848e4/psi-agent
git grep -nE 'workspace[/\\](tob|toc)' -- . ':!docs'   # 须空
diff /tmp/rename-baseline-mapped.txt /tmp/rename-after-nondocs.txt
diff /tmp/rename-baseline-invariants.txt /tmp/rename-after-invariants.txt
diff /tmp/rename-baseline-failures.txt /tmp/rename-after-failures.txt
diff /tmp/rename-baseline-sdist.txt /tmp/rename-after-sdist.txt
```
