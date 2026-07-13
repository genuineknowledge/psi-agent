---
name: simplify-code
description: "Clean up and simplify recent code changes by fanning out 3 parallel subagents over the changed files. Use after a burst of edits / a feature landing / before a PR, when asked to 精简/清理/simplify/refactor/去重 recent changes or reduce complexity without changing behavior. Splits the git diff into 3 disjoint buckets, delegates each to a background subagent (via the subagent-orchestration recipe), then merges their edits and verifies. Behavior-preserving only; no new deps; runs through git/bash/read/edit + subagent_* tools."
category: coding
---

# Simplify code (3-agent parallel cleanup)

用这个技能在一批改动落地后（feature 完成、合并前、或用户说「精简 / 清理 / simplify / 去重 / 降复杂度」）
对**最近改动**做**行为不变**的清理：去重、拆长函数、删死代码、统一命名、收敛重复分支、
补/修类型标注。核心手法是把改动的文件分成 **3 组**，用 [subagent-orchestration](../subagent-orchestration/SKILL.md)
配方**并行**派 3 个后台子 Agent 各清一组，最后合并各自的编辑并统一验证。

没有专门的工具——全靠已有的 `bash` / `read` / `edit` / `find_files` / `search_content` 和
`subagent_plan` / `background_start` / `subagent_wait` / `subagent_chat` / `background_stop`。无额外依赖。

除非用户明显用其它语言，一律用中文回复。

## 铁律（最高优先级）

- **只做行为等价的清理。** 不改公开 API、不改可观察行为、不改测试期望。任何可能改行为的重构先问用户。
- **只碰最近改动的文件。** 范围 = 本次改动集（见下）。不要顺手重排/重命名无关文件。
- **不 commit、不 push**（除非用户明确要求）。清理产出的是工作区里的编辑；提交是用户发起的另一步。
  遵循 [git-workflow](../git-workflow/SKILL.md) 的安全规则。
- **先跑基线测试再动手。** 拿到「改前」的绿灯，改完再跑一次对比；没有测试就至少 `ruff check` + 编译/import 冒烟。
- **3 组必须互不相交。** 同一个文件只能分到一个子 Agent，避免并行写冲突。

## 何时用 / 不用

- **用**：一批 PR 级改动需要收口清理、多个文件都能独立清、想省墙上时间 → 并行 3 路。
- **不用**：改动只落在 1～2 个文件（主 Session 直接清更快，别为并行而并行）；
  或清理会牵扯跨文件的行为变更（先和用户对齐设计）。固定多步流水线用 `fusion-flow`。

## Step 0 — 圈定「最近改动」范围

先确定改动集，默认按以下顺序探测（用 `bash`）：

```bash
# 1) 有未提交改动就以工作区为准（最常见：feature 刚写完还没提交）
git status --porcelain
git diff --name-only            # 已跟踪的改动
git diff --name-only --cached   # 已 stage 的

# 2) 否则看相对主干的领先提交（合并前清理）
git fetch origin main:refs/remotes/origin/main 2>/dev/null || true
git diff --name-only origin/main...HEAD

# 3) 或最近 N 个提交（用户指定「最近 3 个 commit」时）
git diff --name-only HEAD~3..HEAD
```

只保留**源码文件**：过滤掉锁文件、生成物、`node_modules/`、二进制、以及非目标语言文件。
把最终清单读一遍（`read` / `git diff <file>`）确认确实是本次改动，别把陈旧文件也拉进来。

**范围为空或只有 1～2 个文件** → 不派子 Agent，直接在主 Session 清完（仍守铁律），跳到 Step 5。

## Step 1 — 记录基线

```bash
# 按项目实际命令来；psi-agent 本身用 uv + ruff + pytest
ruff check . && ruff format --check .
# 测试可能因根 pyproject 的 --cov 而挂住；单包/单文件用：
# pytest <targets> -o addopts="" -p no:cov -p no:cacheprovider -q
```

存下「改前」结果（通过/失败、失败项）。清理**不允许**让绿的变红。

## Step 2 — 分 3 组（互不相交）

把 Step 0 的文件清单切成 3 个尽量均衡、且**内聚**的桶。分组优先级：

1. **按目录/模块**分（同模块的重复更可能一起清）；
2. 再按**文件数 / 改动行数**平衡三桶；
3. 强约束：**任何文件只属于一个桶**。若某文件被多桶依赖，把它连同其主改动整体归一桶。

文件很少（3～5 个）时可以每桶 1～2 个；桶可以少于 3 个（比如只有 2 个内聚簇就派 2 路）。
「3」是上限/默认，不是硬性凑数。

## Step 3 — 并行派 3 个子 Agent（subagent-orchestration 配方）

对每个桶各走一遍 [subagent-orchestration](../subagent-orchestration/SKILL.md) 的
Step 1–6（**不同 `session_id`**，Gateway 模式下 `reuse_parent_ai: true` 会跳过起 AI）：

1. `subagent_plan(session_id="simplify-1"/-2/-3)` ×N
2. 各自 `background_start`（session；`shell` 用 plan 返回值）→ `subagent_wait`（channel_socket）
3. 各自 `subagent_chat`，`message` 用下面的**自包含任务模板**填该桶

**静默执行**：不要逐步向用户播报 spawn 调试过程，开头一句「派 3 路并行清理」即可，结束给合并摘要。

### 每桶任务模板

```markdown
## Objective
对下列文件做**行为不变**的清理/精简，只改这些文件，不碰其它文件。

## Files (你的专属桶，勿越界)
- <path-a>
- <path-b>

## Do
- 去重复代码 / 提取共用逻辑（仅限本桶文件内）
- 拆过长函数、去深层嵌套、合并重复分支
- 删死代码 / 未用 import / 未用变量
- 统一命名与风格，补明显缺失的类型标注
- 用更清晰的等价写法（列表/字典推导、早返回等）

## Don't
- 不改公开 API、函数签名对外契约、可观察行为、测试期望
- 不动本桶以外的文件
- 不 commit / 不 push
- 不启动 Gateway 或其它 psi-agent 进程

## Verify (改完自己先跑)
- <项目的 lint/format 命令>
- 相关测试（若能定位到）
- 报告：改了哪些文件、每处清理的一句话理由、验证结果（通过/失败）

## Deliverable
一个简表：文件 | 清理点 | 理由 | 验证。**编辑直接落到工作区文件里**（用 edit/write），不要只贴 diff 文本。
```

（子 Agent 与主 Agent 共享同一个 workspace/工作区文件，`edit`/`write` 的改动主 Session 可见。）

## Step 4 — 收结果 + 收尾

- 三路 `subagent_chat` 返回后，把各自的简表**合并成一张**（用 `structured-output-tables` 的风格）。
- 逐路 `background_stop(session_process_id)`（`reuse_parent_ai: false` 时再 stop `ai_process_id`；
  **不要** stop 主 Gateway 的 AI）。

## Step 5 — 统一验证（关键）

并行编辑合并后，在主 Session 跑**一次全量**基线对比：

```bash
ruff check . && ruff format --check .
# 测试同 Step 1 的调用方式
```

- 和 Step 1 的基线比：**绝不允许**新增失败。有回归 → 定位到具体桶的改动，回退或修正那一处，再验一次。
- 若三路碰巧改了相邻/交叉区域产生语义冲突（理论上桶不相交应避免），以基线测试为准裁决。
- 清理完删掉任何临时文件。

## Step 6 — 汇报

给用户一张合并表：文件 | 清理点 | 理由，加一行验证结论（改前/改后测试对比）。
**不 commit**——问用户是否要提交，要则走 `git-workflow`。

## 反模式

| 错误 | 正确 |
|------|------|
| 为「3 路」硬凑，2 个文件也派 3 个子 Agent | 少量文件主 Session 直接清 |
| 同一文件分进两个桶 → 并行写冲突 | 桶严格不相交 |
| 顺手重命名/重排无关文件 | 只碰最近改动集 |
| 清理中改了行为却不声明 | 只做等价清理，行为变更先问 |
| 边 spawn 边向用户念内部步骤 | 静默执行，只报结果/blocker |
| 改完不 stop 子 Agent | Step 4 `background_stop` |
| 让绿的测试变红还提交 | Step 5 基线对比，回归必修 |

## 自检

- [ ] Step 0 圈定的文件都是本次改动、且已过滤生成物/无关文件
- [ ] Step 1 基线已记录
- [ ] 3 桶互不相交、尽量均衡；文件少时按需减少路数
- [ ] 每个子 Agent 只碰自己的桶、不 commit、不起 Gateway
- [ ] Step 5 全量验证 vs 基线无回归
- [ ] 子 Agent 已 `background_stop`
- [ ] 未擅自 commit/push
