# Web Search & Recency 增强设计

日期: 2026-07-07
分支: `enhance/web-search-recency`(基于 `fix/system-prompt14`)
工作区: `examples/haitun-workspace`

## 背景与问题

`fix/system-prompt14` 已经引入 `WEB_SEARCH_RECENCY_SECTION`(见
[prompt_sections.py](../../../examples/haitun-workspace/systems/prompt_sections.py)),
并已接线进系统提示词的 stable prefix
([system.py](../../../examples/haitun-workspace/systems/system.py) import + assembly)。
现状已覆盖:时效性事实先搜、区分已验证/凭记忆、引用来源、冲突交叉验证、关注时钟。

实测仍存在一个失效模式(旧分支复现):用户问电竞战队 (BLG / HLE) 当前阵容时,
agent 把"选手当前所属/队伍名单"当成**记忆里的稳定知识**直接输出,阵容严重过时,
直到用户明说"用联网功能确认"才切换联网。

根因:这类"命名实体的当前归属/成员关系"看起来像稳定知识,实则高频变动(转会期)。
现状规则点名了 rankings / "latest·current·newest",但**没有直白点名"某人现在属于哪个
队 / 某团队当前名单 / 赛事阵容"**这一类,模型容易归错类。

## 目标

在**不推翻**现有 `WEB_SEARCH_RECENCY_SECTION` 的前提下,就地增强(取向 1),补三个盲区:

- **A. 知识截止锚点** — 让模型有明确时间基准判断"这事在截止后可能变过吗"。
- **B. 扩类别 + 强默认(重点)** — 点名"命名实体当前状态/归属/成员"高危类,对该类
  从"倾向搜"提升为"默认必搜,不得凭记忆答"。直接对症失败案例。
- **C. 反向边界** — 声明稳定事实无需搜,灰色地带倒向"快速搜一下"而非凭记忆猜,防过度联网。

非目标:不改 `search`/`fetch` 工具实现;不动 `TASK_SELF_CHECK_SECTION`(取向 3 已排除);
不硬编码模型截止日期(违背 workspace 模型无关设计)。

## 改动

### 改动 1 — `prompt_sections.py`: 增强 `WEB_SEARCH_RECENCY_SECTION`

在现有第 1 条 "Search first" 中补入高危类别与强默认措辞(B),并在段末新增一条反向
边界(C)。措辞要点(最终英文文案在实现时定稿,保持与该段现有风格一致):

1. 第 1 条扩充,显式加入一类:**"命名实体的当前状态 / 归属 / 成员:某人当前的职位或
   所属组织、某个团队 / 队伍 / 组织的当前成员或名单、赛事阵容、赛果与赛程。"** 并追加强
   措辞:*对"现在谁在哪 / 当前名单 / 当前冠军 / 最新阵容"这类问题,默认必须联网核实,
   不得凭记忆作答 —— 即使你记得一个看似合理的答案。*
2. 段末新增反向边界条目:*稳定、不随时间变化的事实(基础数学、已确立的定义、通用做法、
   可本地推理的代码)无需联网。当拿不准某事是否时效敏感时,倒向快速搜一下,而不是凭
   记忆猜。*

该 section 位于 stable prefix,保持纯规则文本,不含任何易变值(日期不放这里)。

### 改动 2 — `system.py`: 知识截止锚点注入(A)

复用现有 `_build_datetime_section()`(dynamic suffix,已输出当前日期)。在其中追加一行
知识截止时间,使"当前日期"与"知识截止"并排呈现,模型可直接对比:

- 读环境变量 `HAITUN_KNOWLEDGE_CUTOFF`(格式如 `2026-01`),类比现有 `HAITUN_TIMEZONE`。
- **已设**: 追加一行,如
  `Knowledge cutoff: 2026-01 (facts that may have changed after this date are not reliable from memory — verify online).`
- **未设**: 追加中性兜底行,不给假日期,如
  `Knowledge cutoff: unknown — treat any fact that may have changed recently as possibly stale and verify online.`

只改 `_build_datetime_section` 内部,拼装点(`dynamic_parts += [_build_datetime_section(), ""]`)
不变。截止值作为易变数据留在 dynamic suffix,stable prefix 缓存边界不受影响。

## 缓存边界

- 规则文本(改动 1)→ stable prefix,不含易变值,缓存友好。
- 截止日期(改动 2)→ dynamic suffix,与当前日期同处,天然易变、本就在缓存边界外。

## 测试

- **单元**: 为 `_build_datetime_section` 加/扩测试,覆盖 (a) `HAITUN_KNOWLEDGE_CUTOFF`
  已设时输出含该值的截止行;(b) 未设时输出中性兜底行且不含假日期。参照现有
  `systems` 测试风格与 `HAITUN_TIMEZONE` 的既有用法。
- **文本校验**: 断言 `WEB_SEARCH_RECENCY_SECTION` 包含新增高危类别关键词与反向边界关键词
  (防止后续误删)。
- **构建校验**: 按 workspace 惯例跑 `ruff check` + `ruff format --check`(CI 两个都跑)。
- **人工冒烟**(可选,依赖模型服务): 重放"BLG/HLE 当前阵容"类问题,确认 agent 未经提醒
  即主动联网。此项依赖上游模型,不作为合入硬门槛。

## 风险与回滚

- 过度联网风险: 由改动 1 的反向边界(C)约束;若实测过搜,收紧灰色地带措辞。
- 改动集中在两个文件的局部文本 + 一个函数,易 diff 易回滚。
- 分支策略: 按既有约定,`enhance/web-search-recency` 只 push 到功能分支,是否合入
  `fix/system-prompt14` 或 main 由用户决定。
