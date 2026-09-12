# 方案 B 落地设计：/official 只读 + /enterprise 只读 + /users 可写

日期：2026-09-12 · 分支 `feat/tob-layering-s3` · 状态：设计待评审

## 结论先行

方案 B 分**四步**落地（会话 2 的三步 + 新增 B-0 只读探针）。风险不均匀：B-0/B-1 纯代码零风险，B-2 改写入路径，B-3 才碰挂载。

复核会话 2 的两处阻断**均成立**，另查出**两处新阻断**（skills 兼作飞书 API 守卫规则源、`config/` 未列入分层），并**纠正一条**（skills 已有两根合并，不是单目录）。

规模数字全部重量：顶层条目 **393**（不是 376），一天涨 17 个。

一条关键实测把 B 的风险重估：**agent/workspace 两根分离已经部署并在跑**（76 个活 session 全 `agent=/workspace`，61 个 `workspace=/workspace/<id>`），而 47 个 `ou_*` / 9 个 `chat-*` 目录里**一个 `skills/`、`tools/`、`triggers/` 都没有**。B 的个人内容层会是第一次往这些目录放东西——不是重构，是填空。

`/official` 采用**复制而非移动**：官方内容总共只有 7.2M，`/srv` 剩 13G。这是 B-3 不必停机重建的基础。

---

## 一、复核结果

### 1.1 阻断一成立，且比交接记录严重（已收敛为一条）

`skill_manage.py:14` 与 `trigger_manage.py:45` 都解析到 `resolve_agent()`，写操作在 `_atomic_write`（:48-54、:191-194）与 `unlink`（:325）。挂 ro 后确为 `OSError`。

生产上有 5 个 `created_by: agent` 的 skill 在 `/workspace/skills`。**逐字节复核（LF 归一化 md5）后收敛为 1 个**：

| skill | git vs prod | mtime | 判定 |
|---|---|---|---|
| code-review-checklist | 相同 `37942e4170cf` | 09-07 11:52:00 整秒 | 官方内容 |
| python-async-basics | 相同 `be09ac455ea8` | 09-07 11:52:00 整秒 | 官方内容 |
| python-static-analysis | 相同 `d4938cb324fe` | 09-07 11:52:00 整秒 | 官方内容 |
| skill-authoring-how | 相同 `fc0939b1eeac` | 09-07 11:52:00 整秒 | 官方内容 |
| **user-preferences-and-language** | **git 2602 / prod 4352** | **09-11 11:11:05** | **真实就地写入** |

前 4 个的 `created_by: agent` 是**随版本发的 frontmatter**（历史上由 agent 撰写后入库），整秒 mtime 是部署投放签名。不需要迁移——它们随白名单进 `/official`，与其余 158 个 skill 同路。

**归属查询结论：日志不可用**（不是"不确定"）。gateway 日志窗口起于 2026-09-12 00:46（容器重启），而写入发生在 09-11 03:11 UTC。当前窗口 1895 条 `skill_manage` 提及，无一来自重启前。

**决定：那 1 个归入 `/enterprise`。** 其内容是可复用的交付规范（交付范围最小化、PPT 规范含真知蓝 #052464 / 思源黑体、"清洁版"口径），适合全公司一份走 PR，而非固化成官方内容。

### 1.2 阻断二成立，形状已确认

`PSI_CONTENT_ROOTS` 只到 `ToolRegistry.load_content_roots`（`agent.py:385-392`）。另三样单目录：

- `TriggerRegistry.load(agent_root / "triggers")`（`trigger_registry.py:181`）
- `SystemPrompt.from_workspace(agent_root, ...)`（`system_prompt.py:82`）
- skills 经 `_build_skills_index(ws)`（`system.py:1203`，`ws = self._agent_dir`）

### 1.3 纠正：skills 已有两根合并

`system.py:514-527` 已合并 `~/.agent/skills`（`_GLOBAL_AGENT_SKILLS_DIR`）与 `agent_root/skills`，nearest-wins。**B 应扩展这个已有形状，而不是另发明一套。**

两个隐患：

- `.skills_prompt_snapshot.json` 的 manifest 是 `{name: sha256}`，**不含层身份**。同名不同内容可区分，但层序变化而字节相同不可区分。
- `_collect_skill_dirs` 用 `contextlib.suppress(OSError)` 吞异常，某层只读或缺失时返回空列表而不报错——正是"静默只看见一层"。

### 1.4 阻断三（新）：skills 兼作飞书 API 守卫规则源

`_feishu_api_impl.py:310` → `_spec.rules_for(_skills_dir(), verb, path)`。飞书 API 的放行/约束规则从 `skills/*/SKILL.md` 解析。

`_feishu_spec.py:305` 的 `@functools.lru_cache(maxsize=8)` **按目录路径字符串做键**。两个问题：

1. 需要跨根合并，而非单目录；
2. 3 根 × 多 session，8 条缓存会打不住。

**若 skills 分层只改提示词索引而漏这处**：个人层覆盖看起来生效，真实飞书 API 调用仍只受官方规则约束。失败方向最贵，且无报错。

`_card_dsl.py:432` 同为单根形状，还带 `__file__` 相对兜底（`:433`），会把缺层掩盖。

### 1.5 阻断四（新）：`config/` 从 agent 根读，但未列入分层设计

三处 `resolve_agent()/config/<name>.yaml`：`_feishu/todo_sop.py:33`、`_rookie_sop_store.py:84`、`handbook_onboarding.py:317`。生产上三个 yaml 都在。

B 的布局图把 `config/` 只放在 `/official`。**未验证**：这三处是否有回写。`_rookie_sop_store` 名字提示可能有状态持久化，B-1 动手前必须先确认。

### 1.6 顺手查到：git 里有一个损坏的 frontmatter（独立缺陷，不并入 B）

`agents/feishu/skills/user-preferences-and-language/SKILL.md` 有重复的 `---` 围栏和第二份 `name:`/`description:`。`_parse_frontmatter` 遇第一个 `\n---` 即停，第二块以下全被当正文（git body 2337 字节 vs prod 2973）。prod 那份已被 agent 的 `patch` 预先修好。

**用真实解析器全扫 170 个 skill：该缺陷只有 1 例。** 另 15 个多围栏文件（tencent-meeting-mcp 22 道、subagent-orchestration 14 道等）是合法的 markdown 水平线，非缺陷。

**决定：当独立缺陷单独修**（只搬结构、不带个人偏好内容），不摞进方案 B。

### 1.7 规模数字重量

| 目标 | 顶层条目 | 说明 |
|---|---|---|
| `workspace` | **393** | 会话 2 记的是 376，一天涨 17 |
| `workspace-luolin` | 222 | 会话 2 方案未覆盖 |
| `workspace-chengxx` | 216 | 会话 2 方案未覆盖 |

`workspace` 构成：107 目录 / 286 文件 = 47 `ou_*` + 9 `chat-*` + 17 点文件 + 236 文档 + 8 脚本 + 76 其他。

官方内容体积：skills 3.6M + tools 3.2M + triggers 36K + systems 380K + config 24K = **约 7.2M**。2.5G 是用户数据。`/srv` 剩 13G。

### 1.8 一处未验证：提示词的 9 条相对 skills 路径

`SKILLS_HEADER_TEMPLATE`（`prompt_sections.py:586-604`）里有 9 条硬编码的相对路径 `skills/<name>/SKILL.md`。而 `read` 的相对路径落 **workspace**（`resolve_user_path` → `workspace_dir()`），索引却从 **agent_dir** 建。

72 小时日志里 `read` 工具对相对 `skills/` 路径的调用 **0 次**——模型实际用 `bash` 带 `cd /workspace` 或绝对路径绕过去了。抽查 3 个用户目录，`skills/proposal-need-remind/SKILL.md` 全 MISSING，官方层 EXISTS。

**代码里错位真实存在，当前被模型行为掩盖。按"潜在"记，非"已坏"。** B-3 挂载后 `/users/<id>` 成为 workspace 根，这条会从潜在变成必然——B-1 须一并处理。

---

## 二、已定的四条决定

1. **既有写入**：先查归属；查不到则不进 `/official`——那 1 个真实写入归入 `/enterprise`（内容是可复用交付规范）。
2. **393 归类**：白名单搬迁。只把已知官方内容复制进 `/official`，其余原地不动、继续由 `/users/<主>` 承载。不逐个判、不会漏。

   白名单（9 项，全部实测）：`skills/`（162）、`tools/`（189 个 .py）、`triggers/`（4）、`systems/`（10）、`config/`（3 个 yaml）、`bin/`（6 个文件，与 git 同名同构）、`AGENTS.md`、`TOOLS.md`、`USER.md`。

   `flows/` **不进白名单**：git 里只有 `.gitkeep`，生产有 7 个子项（cache-evaluation、klm-paper-analysis、mt-semparse-survey、murder-mystery、todo-check、vector-db-article、package.json）——是运行期产物，归 `/users`。

   `bin/` 有一处差异（生产多 `auth_callback_server.py`、git 多 `compare_session_search.py`），属任务 #29 的双向漂移范畴，不在本次处理。

   其余一律留在 `/users/<主>`——含 236 个文档、47 个 `ou_*`、9 个 `chat-*`、17 个点文件、部署物（`.env`、`oauth-proxy.py`、`launch-gateway.sh`、`scripts/`）、运行期产物（`output/`、`tmp/`、`generated/`、`.psi/`、`flows/`）、历史残留（`archive/`、`rollback-20260908-before`、各 `.tar.gz`）。

   **`launch-gateway.sh` 是 B-3 最容易静默踩空的地方。** 它传的是**容器内路径**：`--feishu-workspace-root /workspace`、`--default-agent /workspace`、`--default-workspace /workspace`、`--appdata /workspace/.psi/appdata`，且脚本内 `. /workspace/.env`。挂载点从 `/workspace` 改为 `/users` 后**这 5 处全部指空**。compose 侧的 `env_file: ./workspace/.env` 与 `command: /workspace/launch-gateway.sh` 是宿主路径、不受影响——**一半受影响一半不受影响，正是最容易漏的形状**。B-3 须把这 5 处一并改，并把"改前改后逐条 diff"列为判据。
3. **群语义**：群只读 `/official` + `/enterprise`，不支持群内派生。`skill_manage`/`trigger_manage` 在群会话明确报错并指向私聊。实测 9 个 `chat-*` 现无任何 `skills/`，不减功能。
4. **拆步**：四步，新增 B-0 只读探针。

沿用设计文档已定三条规则：同名整体覆盖（不逐字段 merge）、改官方 = 派生、删除用墓碑。会话层不做。

---

## 三、四步拆法

风险递增。前两步纯代码、本地可验完；B-2 改写入路径；B-3 才碰挂载。

### B-0 层来源可观测（新增，纯只读）

**为什么单独一步**：B 最危险的失败模式是"静默只看见一层"——不报错、看起来跑通了。`_collect_skill_dirs` 吞 `OSError`、`read_manifest` 缺失时返回 `None` 不打日志（`tool_exposure`，会话 2 已记），都是这个形状。若不先建立观测手段，B-3 上线后只能靠功能表现反推分层对不对。

**做什么**：给四类内容各加一行"层来源"日志，形状对齐 `agent.py:822` 那行 `tools_exposed=NN of NN tier=layered`。只读，不改任何解析/加载行为。

- skills：`skills_index: N skills from M roots [official=a enterprise=b users=c]`
- triggers：`triggers: N from M roots`
- systems：`system module from <root-name>`
- config：`config/<name>.yaml from <root-name>`

**判据**：

- 单根（`PSI_CONTENT_ROOTS` 未设）时新日志照打，且 `M=1`——证明探针在当前生产形态下就能用，不必等 B-3。
- 本地起 2 根，故意让上层缺 `skills/`：日志须显示 `M=2` 且 `users=0`，**不是** `M=1`。这条是探针存在的理由——它要能把"某层是空的"和"某层不存在"区分开。
- 变异复核：把合并逻辑改成只读一层，日志须转红（`M` 变 1）。用 `cp` 备份，不用 `git checkout`。

**回滚**：纯新增日志，删掉即回滚。零行为变更。

**面积**：4 个文件，只加 `logger.info`。

### B-1 内核 + agent 包支持多根

**做什么**：把 skills / triggers / systems / config 四类的单根读取改为跨根合并，nearest-wins（与 skills 已有的 `~/.agent/skills` 合并同向）。`PSI_CONTENT_ROOTS` 未设时行为**逐字节不变**——与 PR #901 同一模式。

必改的落点（按 1.2-1.5 复核结果）：

| 落点 | 现状 | 改法 |
|---|---|---|
| `system.py:514` `_build_skills_index` | 已合 2 根（global + agent） | 扩为吃根列表 |
| `system.py:164` snapshot manifest | `{name: sha256}` 无层身份 | manifest 加层名，避免层序变化不可区分 |
| `system.py:497` `_collect_skill_dirs` | 吞 `OSError` 返回空 | 区分"缺失"与"读不了"，后者告警 |
| `trigger_registry.py:181` `load` | 单目录 | 吃根列表 |
| `system_prompt.py:82` `from_workspace` | 单 agent_root | 按根列表逐层找 system 模块 |
| `_feishu_api_impl.py:310` `rules_for` | 单目录 + `lru_cache(8)` 按路径键 | 跨根合并；缓存键改层身份、`maxsize` 上调 |
| `_card_dsl.py:432` 模板查找 | 单根 + `__file__` 兜底 | 逐层查；兜底命中要打日志（否则掩盖缺层） |
| `todo_sop.py:33` / `_rookie_sop_store.py:84` / `handbook_onboarding.py:317` | 单根读 `config/` | 逐层查；**先确认有无回写** |
| `prompt_sections.py:586` 9 条相对 `skills/` 路径 | 相对路径落 workspace，索引建自 agent_dir | 见下 |

**关于那 9 条相对路径**（1.8）：当前被模型行为掩盖，B-3 后必然暴露。两种改法——(a) 提示词改绝对路径，(b) 让 `read` 对 `skills/` 前缀走逐层查找。倾向 (a)：改动局限在提示词，不给 `read` 加特例；代价是提示词里出现容器路径。**这条留待评审定。**

**判据**：

- `PSI_CONTENT_ROOTS` 未设时，本地全量 pytest 与基线一致（Windows 基线：session 子树 13 failed、全量 57-62 浮动，非回归）。跑法：`PYTHONPATH=src .venv/Scripts/python.exe -m pytest -o testpaths= <路径>`，`-o testpaths=` 必须写在路径**之前**。
- 设 2 根、上层放一个同名 skill：索引里须是上层那份（nearest-wins），且 B-0 日志显示 `M=2`。
- **飞书 API 守卫必须单独立判据**：上层放一条更严的规则，须真正约束住 API 调用——不能只验索引。这条是 1.4 的直接对应，判据必须落在它声称的那一层（不是调 `_build_skills_index` 就算测了守卫）。
- `config/` 三处各一条：上层放一份 yaml，须读到上层那份。
- 每条判据做变异复核（`cp` 备份，非 `git checkout`）。
- `ty check --python .venv/Scripts/python.exe`（worktree 里必须带 `--python`，不带是 768 个诊断而基线只有 4 个）。

**回滚**：不设 `PSI_CONTENT_ROOTS` 即回到单根。代码可留在库里不上线。

### B-2 实现"改官方 = 派生" + 删除用墓碑

**前提**：B-1 已上线并跑过一段（`PSI_CONTENT_ROOTS` 仍未设，纯代码在位）。

**做什么**：`skill_manage` / `trigger_manage` 的写操作从 `resolve_agent()` 改为落**最上层可写根**。

- `create`：直接写 `/users/<id>/skills/<name>/`。
- `patch` 命中只读层的文件：复制到可写层改副本，原件不动，frontmatter 记 `derived_from` + 来源层名 + 来源版本。
- 删除：可写层写墓碑（停用标记），不删只读层文件。
- 群会话（决定 3）：明确报错"群内不支持改 skill，请在私聊里改"，不静默落到别处。

**判据**：

- 只读层挂 ro，`create` / `patch` / 删除三条各一条判据，均**不得** `OSError`。
- `patch` 官方 skill 后：可写层出现副本、只读层原件 md5 不变、索引里生效的是副本。三条都要验（少一条就可能是"写了但没生效"或"生效了但原件被改"）。
- 墓碑：只读层文件仍在，但索引里不出现该 skill。
- 群会话写操作返回明确错误文案，且**未产生任何文件**（`find` 判据，不只看返回值）。
- 变异复核：把派生逻辑改回直写 agent 根，ro 下须转红。

**回滚**：代码级回滚（改回 `resolve_agent()`）。因为此时挂载还是 rw 单根，回滚无数据风险。

**注意**：这一步做完 B-3 才不会把能用的功能改成报错。顺序不可换。

### B-3 动挂载

**前提**：B-0/B-1/B-2 已上线跑过一段；白名单已定；那 1 个 `/enterprise` 条目已就位。

**关键取舍：复制而非移动。** 官方内容 7.2M，`/srv` 剩 13G。做法：

```
/srv/haitun/psi-agent/official/        <- 新建，从 workspace 复制官方白名单
/srv/haitun/psi-agent/enterprise/      <- 新建，含那 1 个 skill
/srv/haitun/psi-agent/workspace/       <- 原地不动，仍是 393 项
```

挂载——**容器内挂载点仍叫 `/workspace`，不改名为 `/users`**：

```
- ./official:/official:ro
- ./enterprise:/enterprise:ro
- ./workspace:/workspace:rw       # 不动。ou_*/chat-* 即是各人的可写层
PSI_CONTENT_ROOTS=official=/official:enterprise=/enterprise:users=/workspace
```

不改名是刻意的，理由是上面 `launch-gateway.sh` 那 5 处容器内路径：改名要同步改 5 处启动参数 + 脚本内 `. /workspace/.env`，而**保留 `/workspace` 这个挂载点名，B-3 就退化为"加两个只读挂载 + 加一个 env"**——启动参数一行都不用动。`/users` 只是 `PSI_CONTENT_ROOTS` 里的**层名**（层名与路径解耦，正是 `content_roots.py` 模块 docstring 里 `layer_id` 取自 name 而非 path 的设计意图），不必是挂载点名。

`/workspace` 根不再兼任官方包——双重身份由"官方内容另有 `/official` 一份且 Session 按层查找"消除，而**一个文件都没搬、一个启动参数都没改**。这是回滚从"恢复 2.5G 数据"降级为"注掉一行 env"的关键。

代价（须评审确认）：`/workspace` 根下仍**物理存在**一份 `skills/`、`tools/` 等官方目录副本。它们不再被当作官方层读取（官方层指向 `/official`），但 `/users` 层的查找会**在根目录命中它们**——等于给主 workspace 保留了一个"全量个人层"。对 47 个 `ou_*` 子目录无影响（它们各自为层根），但 15 个 workspace 指向根本身的错状态 session 会看到官方内容出现在个人层。**这 15 个的行为必须单独验**（见下判据），也可以选择在 B-3 时把根目录那份官方副本删掉——但那就不再是"零文件搬动"，回滚成本上升。

**内存约束**：机器 7.1GB、swap 0、dmesg 已 8 次 OOM，gateway 那次 502 根因是 memcg OOM 撞 3g 上限。当前 `free -g` 显示 available 仅 1G。所以**不同时重建 7 个容器**：

1. 先只改 gateway 一个（`mem_limit` 已是 4500m）。
2. 观察一段，确认 B-0 日志显示 `M=3` 且三层都非零。
3. 再逐个改 luolin / chengxx（各 1g，是别人的容器，爆炸半径考虑见 `_patch_compose.py` 注释）。

**改挂载必须 `docker compose up -d`**（不是 `docker cp`）——这是换挂载配置，改动本来就在 compose 里。但 `/app/src` 在镜像里不是挂载，所以**不能借这次 up -d 顺手改容器内文件**。

**判据**：

- B-0 日志：`M=3`，且 `official`/`enterprise`/`users` 三个计数都对得上磁盘实际。
- 只读层写入被拒（挂载层面），而 `skill_manage(create)` 仍成功（落 `/users`）——两条一起验，才证明 B-2 真在吃劲。
- 61 个 `workspace=/workspace/<id>` 的 session 恢复后仍能读到官方 skill。
- 15 个错状态 session：其 workspace 是 `/workspace` 根本身，B-3 后成为 `/users` 根。**须单独确认这 15 个的行为**——`is_strictly_under` 的 docstring 记着它们，别让 B-3 把一个已知错状态变成新的静默错。
- 三层 md5 核验（LF 归一化 `tr -d '\r' | md5sum`）：build 机 src / 镜像内产物 / 容器内实际加载的。第三层是 8-18 事故缺的那层。
- 入口活性单独验：内部全绿不证明入口活着。定时任务与发卡片走出站，OAuth 回调走入站，两条都要打。重启 gateway 后紧跟 `docker compose restart oauth-proxy`（共享 netns，gateway 重建会让它挂在死 netns 上）。

**回滚**：注掉 `PSI_CONTENT_ROOTS` 一行 + `up -d`。因为采用复制而非移动，`/workspace` 全程未改，回滚不涉及数据恢复。

---

## 四、还需定的三条

1. **`/workspace` 根下那份官方副本删不删**（B-3）：不删则零文件搬动、回滚只需注掉一行 env，代价是 15 个错状态 session 会在个人层看到官方内容；删则语义更干净，但回滚要恢复文件。倾向不删——先上线、观察那 15 个的实际表现，再决定是否清理。
2. **那 9 条相对 `skills/` 路径**（1.8）：提示词改绝对路径，还是给 `read` 的 `skills/` 前缀加逐层查找。倾向前者。注意若采纳第 1 条的"不删"，这条的紧迫性下降——相对路径在 `/workspace` 根仍能命中。
3. **`config/` 是否有回写**（1.5）：B-1 动手前必须先确认 `_rookie_sop_store` 等三处。若有回写，`config/` 也要进派生逻辑，B-2 面积扩大。

## 五、不在本次范围

- 会话层（设计文档已定不做）。
- 任务 #29 内容层双向漂移（涉及真人姓名与线上群，独立一条）。
- 卡 62e1f 测试 session 残留（需设计判断，可能动 `src/`）。
- git 里那个损坏 frontmatter（1.6，当独立缺陷单独修）。
- ToC 侧：两根本来就分开，`agents/desktop/` 无 `EXPOSED.txt`，不受本次影响。
- `schedules` 仍挂 `workspace_path`，刻意不跟分层走（`agent.py:395-397`：日程是"谁的提醒"，属挂载侧）。

## 六、本文档的实测边界

已实测：1.1 全部 md5 与 mtime、1.2-1.5 代码落点、1.6 全扫 170 个 skill、1.7 三个 workspace 计数与体积、76 个 session 的 agent/workspace 分布、47+9 个用户目录无 `skills/`、磁盘与内存余量。

未验证：`config/` 三处是否回写（1.5）；那 9 条相对路径在 B-3 后的实际表现（1.8，当前被模型行为掩盖）；luolin / chengxx 两个容器的任何分层行为（`_patch_compose.py` 注释已声明从未为它们验证过）；`bin/` 的双向漂移（生产多 `auth_callback_server.py`、git 多 `compare_session_search.py`，属任务 #29）。

本文档全程未改生产、未写 `src/`。所有服务器操作均为只读探针（`docker ps`/`inspect`/`logs`、`cat`、`ls`、`stat`、`md5sum`、`df`、`free`、容器内 `curl /sessions`）。
