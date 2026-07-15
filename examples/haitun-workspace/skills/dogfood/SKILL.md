---
name: dogfood
description: "Exploratory QA on a running Web app — 探索式测试 / 找 bug / 试用产品 / 边玩边挑毛病 / 冒烟 / 回归前扫一遍. Drive the real app through the browser tools like a curious user, hunt for defects (functional, UI, console/network errors, edge cases, broken flows), capture reproducible evidence (steps + screenshot + console log) for every finding, and deliver a triaged bug report. Use when asked to dogfood / QA / 探索测试 / 找问题 a live URL, NOT for writing unit tests (use test-driven-development) or reviewing source diffs (use code-review-checklist). Pure markdown, no new deps; runs on the existing browser_* tools + bash/read/write."
category: coding
---

# Dogfood（Web 应用探索式 QA）

用这个技能像一个**好奇又挑剔的真实用户**那样试用一个**跑起来的 Web 应用**，主动去**找 bug**、
为每个问题**留下可复现的证据**、最后交一份**分好优先级的缺陷报告**。目标是产品质量优化——
不是证明它能用，而是找出它哪里不行。

核心心智：**你的工作产出是「发现的问题 + 证据」，不是「点完了一遍」。** 没发现问题不代表没问题，
往往代表探得不够深。带着破坏欲走边界、走异常路径、走用户会犯错的路径。

驱动应用靠**已有的 `browser_*` 工具**（`browser_navigate` / `browser_snapshot` /
`browser_click` / `browser_type` / `browser_press_key` / `browser_navigate_back` /
`browser_take_screenshot` / `browser_console_messages` / `browser_handle_dialog` 等），
证据与报告靠 `bash` / `write` / `read`。**不装任何仓库依赖**，无专用工具。

除非用户明显用其它语言，一律用中文回复。

## 铁律（最高优先级）

- **只在被授权的目标上测。** 只测用户给的 URL / 应用（他们自己的产品或测试环境）。
  不对第三方站点做压力、爬取、绕过鉴权等操作。破坏性动作（删数据、批量提交、发真实订单/邮件）
  在**非生产环境**才做，且先和用户确认；拿不准就问。
- **每个发现必须可复现。** 一条 bug = 精确到步的重现步骤 + 期望 vs 实际 + 证据（截图 / console 报错 /
  网络失败）。复现不了的「好像有问题」不叫发现，要么再探到能复现，要么标注「偶发，未稳定复现」。
- **如实记录，不美化。** 观察到什么写什么，包括你自己操作失误导致的假象要甄别掉。不夸大严重度，
  也不为了报告好看而漏报小问题。
- **先探再报，别边探边打断。** 探索过程静默进行，不要每点一下就向用户播报。得到阶段性结论或
  遇到 blocker（应用打不开、需要登录凭据）才说话。
- **证据落盘。** 截图和日志存到 `dogfood/<session>/` 下，报告里引用相对路径，别让证据只留在对话里。
- **不改被测应用的源码。** 这是黑盒 QA，产出是报告；修 bug 是另一件事（交给用户或走正常开发流程）。

## 何时用 / 不用

- **用**：有一个**跑起来的 Web 应用 / URL**，要探索式地找 bug、做上线前冒烟、回归前扫一遍、
  验收新功能、或用户说「帮我 dogfood / QA / 探索测试 / 挑毛病」。
- **不用**：
  - 要写单元 / 集成测试代码 → [test-driven-development](../test-driven-development/SKILL.md)。
  - 要审的是源码 diff 而非运行时行为 → [code-review-checklist](../code-review-checklist/SKILL.md)。
  - 只是简单读一个页面的内容 → 直接 `fetch` / `search` 更快，不必开浏览器。
  - 应用还没跑起来 / 没有可访问 URL → 先让用户把它启动或给出地址。

## 流程

### Step 0 — 摸清目标与范围（charter）

动手前用几句话和用户对齐（缺信息就问）：

- **目标 URL** 与**环境**（生产 / 预发 / 本地？破坏性操作是否允许？）。
- **登录凭据 / 测试账号**（需要的话）。绝不硬编码真实密码进报告；引用时用「测试账号」代称。
- **重点区域**：用户最关心哪些功能 / 最近改了什么（优先探这些）。没指定就做广度优先冒烟。
- **时间 / 广度预期**：快速冒烟，还是深挖某个流程。

建立本次会话的证据目录：

```bash
SESSION="dogfood/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SESSION"
echo "evidence dir: $SESSION"
```

### Step 1 — 打开应用，建立基线

```
browser_navigate <URL>
browser_snapshot            # 拿到可交互元素的 ref，看清页面结构
browser_console_messages    # 记下「什么都没做」时是否已有报错/告警
```

先确认应用能正常加载，截一张初始状态图存进 `$SESSION`。记录初始 console/网络是否干净——
很多 bug 在你还没操作时就已经在 console 里了。

### Step 2 — 探索式测试（核心）

不要照剧本走。像真实用户那样**探索**，同时刻意去踩容易出问题的地方。每探一块，
用 `browser_snapshot` 拿 ref → `browser_click` / `browser_type` 操作 → 再 snapshot 看结果，
并随手 `browser_console_messages` 看有没有新报错。

覆盖这些**启发式**（按目标裁剪，别只走 happy path）：

- **核心用户流程**：把产品最主要的几条路径完整走通（注册 / 登录 / 下单 / 提交 / 搜索……），
  看每一步的结果是否符合预期。
- **输入边界与异常**：空值、超长文本、特殊字符 / emoji / 脚本片段（`<script>`、`'`、`"`）、
  负数 / 0 / 超大数、错误格式（非法邮箱 / 日期）。看校验是否存在、报错是否友好、有无注入迹象。
- **状态与流程破坏**：中途后退（`browser_navigate_back`）、刷新、重复提交、并发两个标签页、
  会话过期后再操作、直接访问需登录的深层 URL。
- **UI / 交互**：错位 / 溢出 / 遮挡、按钮点了没反应、loading 卡死、空状态 / 长列表、
  移动窄屏（若相关）、可访问性明显缺失（图片无 alt、无键盘焦点）。
- **反馈与错误处理**：故意触发失败（断网、填错），看应用是崩了、白屏、无声吞掉，还是给了清晰提示。
- **console / 网络**：`browser_console_messages` 抓 JS 报错与告警；留意 4xx/5xx 请求、
  慢请求、报错却对用户「看起来正常」的静默失败。

**每碰到一个疑似问题，立刻按 Step 3 固化成一条发现再继续**——别指望回头能重现。

### Step 3 — 固化每个发现（证据 = 可复现）

发现问题的当下就收集证据，别攒到最后：

```
browser_take_screenshot     # 存进 $SESSION，文件名带 bug 序号，如 bug-03-checkout-500.png
browser_console_messages    # 相关报错原文
```

用一句话确认**能重现**：把导致问题的操作序列复述一遍再走一次，确认稳定复现（或标注「偶发」）。
把这条发现追加到 `$SESSION/findings.md`，字段见报告模板的单条结构。

### Step 4 — 定优先级（triage）

对每条发现按严重度归类，便于用户先修要紧的：

- **P0 阻断**：核心流程走不通、数据丢失 / 损坏、崩溃 / 白屏、安全问题（越权、注入、凭据泄露）。
- **P1 严重**：主要功能错误但有可绕过的路径、明显错误结果、频繁 console 报错影响功能。
- **P2 一般**：次要功能问题、边界情况处理不当、体验受损但不阻断。
- **P3 轻微**：文案 / 样式瑕疵、非关键告警、改进建议。

同时给一个**覆盖说明**：你探了哪些区域、哪些没探到（时间 / 权限 / 缺账号所限），
让用户知道报告的边界，别把「没报 = 没问题」误当质量背书。

### Step 5 — 出报告

把 `findings.md` 整理成最终报告（`$SESSION/report.md`），用下方模板。按优先级排序，
每条附证据相对路径。给用户一份摘要（总数 + 按优先级计数 + 最要紧的 3 条），报告全文落盘。
若用户要求，可据此走 [github-issues](../github-issues/SKILL.md) 建 issue，或按
[document-report-authoring](../document-report-authoring/SKILL.md) 出正式文档。

## 报告模板

```markdown
# Dogfood 报告：<应用 / URL>

- **环境**：<生产 / 预发 / 本地> ｜ **时间**：<YYYY-MM-DD> ｜ **证据目录**：<dogfood/…>
- **概要**：共 N 个发现（P0×_ P1×_ P2×_ P3×_）。最要紧：<一句话>

## 覆盖范围
- 已测：<区域 / 流程列表>
- 未测：<没覆盖到的部分 + 原因>

## 发现清单（按优先级）

### [P0] <一句话标题>
- **重现步骤**：1) … 2) … 3) …
- **期望**：<应该发生什么>
- **实际**：<实际发生了什么>
- **证据**：![](bug-01-xxx.png) ｜ console: `<报错原文>` ｜ 请求: `<方法 URL 状态码>`
- **复现性**：稳定 / 偶发（<频次>）
- **影响**：<对用户/业务的影响>

### [P1] …
（同上结构，逐条）
```

## 反模式

| 错误 | 正确 |
|------|------|
| 只走 happy path，点通一遍就说"没问题" | 刻意走边界 / 异常 / 破坏性路径去找 bug |
| 报"某处好像有问题"但复现不了 | 每条发现附精确重现步骤 + 证据，或明确标"偶发未稳定复现" |
| 证据只留在对话里，事后找不到 | 截图 / 日志落盘 `dogfood/<session>/`，报告引用相对路径 |
| 攒到最后一起截图，早期状态已丢失 | 发现当下立刻固化证据 |
| 不看 console / 网络，漏掉静默失败 | 每步 `browser_console_messages`，留意 4xx/5xx |
| 在生产环境做破坏性操作没确认 | 破坏性动作限非生产 + 先问用户 |
| 一股脑列问题不分轻重 | 按 P0–P3 triage，让用户先修要紧的 |
| 把"没报问题"当质量合格证 | 给覆盖说明，讲清没探到的部分 |

## 自检

- [ ] Step 0 对齐了目标 URL / 环境 / 授权 / 凭据 / 重点区域，建了证据目录
- [ ] 走了核心流程 + 至少几类边界/异常/破坏性启发式，不止 happy path
- [ ] 每条发现有精确重现步骤 + 期望/实际 + 落盘证据（截图/console/网络）
- [ ] 每条发现确认过复现性（稳定 / 偶发）
- [ ] 全部发现按 P0–P3 定级，附覆盖范围说明
- [ ] 报告落盘 `dogfood/<session>/report.md`，给了用户摘要
- [ ] 未改被测应用源码，未在生产做未经确认的破坏性操作

## 相关

- **建 issue 跟踪**：把发现录成缺陷单 → [github-issues](../github-issues/SKILL.md)
- **正式报告文档**：出结构化质量报告 → [document-report-authoring](../document-report-authoring/SKILL.md)
- **审源码而非运行时**：对 diff 做评审 → [code-review-checklist](../code-review-checklist/SKILL.md)
- **写测试固化回归**：把发现变成自动化测试 → [test-driven-development](../test-driven-development/SKILL.md)
- **大范围并行探测**：多模块分头 dogfood → [subagent-orchestration](../subagent-orchestration/SKILL.md)
