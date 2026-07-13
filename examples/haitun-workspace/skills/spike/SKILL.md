---
name: spike
description: "Run a time-boxed, throwaway experiment to answer ONE concrete technical unknown before real development — 技术预研 / 摸底 / 验证可行性 / 选型对比 / 第三方 API 或库能不能做到 X / 性能够不够. Use when there's a real technical risk to de-risk first, NOT when the requirement is already clear (then just build it) or a doc lookup would answer it. The output is the CONCLUSION; the code is disposable and never ships. Isolate work in spikes/<topic>/, try libraries in a throwaway env (uv run --with) without touching pyproject, no tests, no quality bar. Pure markdown, no new deps; runs on existing bash/read/write/edit/find_files."
category: coding
---

# Spike（一次性抛弃式技术预研）

用这个技能在**正式开发前**做一次**时间盒内的抛弃式实验**，只为回答**一个**具体的技术未知问题：
「这个库/API/方案能不能做到 X？」「性能够不够？」「这两种选型哪个可行？」

核心心智：**代码是垃圾，结论才是产出。** spike 写完，你带走的是「行不行 + 为什么 + 建议」，
代码本身丢弃。它和「正式实现」对立——正式开发追求质量、写测试、进主干；
spike **不写测试、不做错误处理、不追质量**，够回答问题就停。

没有专门的工具，也**不装任何仓库依赖**——全靠已有的 `bash` / `read` / `write` / `edit` /
`find_files` / `search_content`。要试第三方库时用**临时隔离环境**（见 Step 2），不碰 `pyproject.toml`。

除非用户明显用其它语言，一律用中文回复。

## 铁律（最高优先级）

- **单一明确问题 + 可判定的判据。** 动手前用一句话写下要回答什么，以及「怎样算成功 / 失败」。
  问题太宽泛（「试试这个框架」）就先缩窄成能判定的（「用它 20 行内能不能读到 X 字段」）。
- **时间盒。** 定一个上限（用户指定则用之，否则默认 30–60 min）。**到点必须停下做决策**，
  不允许「再改一点就好了」无限投入。超时本身就是一个结论（这条路比预期难）。
- **隔离，绝不污染生产。** 所有 spike 代码放 `spikes/<topic>/`，不改任何生产源码、配置、依赖清单。
- **试库用临时环境，不进 pyproject。** 例如 `uv run --with <pkg> python spikes/<topic>/probe.py`
  或临时 venv。**绝不**把实验依赖写进 `pyproject.toml` / lock / 打包配置——那是正式开发的事。
- **不追质量。** 不写测试、不做异常处理、不抽象、不管命名、不管覆盖率。硬编码、print 调试都行。
- **结束必产出结论。** 收尾一定要有「结论文档」（Step 5 模板）：可行性 + 发现 + 对正式实现的建议。
- **spike 代码永不直接进正式实现。** 要落地就在正式流程（tdd / 正常开发）里**重写**，spike 只喂结论。

## 何时用 / 不用

- **用**：有**真实技术风险**要先排除——没用过的库/API、不确定的性能、拿不准的集成方式、
  两三个方案要快速对比可行性。目的是**降风险**，不是交付功能。
- **不用**：
  - 需求已经清楚、无技术未知 → 直接走正式实现（别用 spike 拖延）。
  - 查文档 / 读源码 / 一次 `WebSearch` 就能答 → 直接查，别写代码。
  - 要交付能用的功能 → 那是正式开发，用 tdd / 正常流程，不是 spike。
  - 大范围预研需要并行铺开多个独立探针 → 用 [subagent-orchestration](../subagent-orchestration/SKILL.md) 分派，每路各跑一个 spike。

## 流程

### Step 0 — 定义问题与判据

写下（可以就写在 `spikes/<topic>/QUESTION.md` 或直接告诉用户）：

- **问题**：一句话，具体、封闭。例：「httpx 能否在流式响应下边收边解析 SSE，且断线自动重连？」
- **判据**：怎样算答完。例：「能拿到分块事件 + 主动断网后 5s 内重连成功 → 可行；否则不可行/需再探。」

判据不可判定就别开始——先缩窄问题。

### Step 1 — 定时间盒

明确上限（默认 30–60 min，或用户指定）。告诉用户这是时间盒实验，到点给结论。

### Step 2 — 隔离环境搭最小原型

```bash
mkdir -p spikes/<topic>
# 试第三方库：临时环境，不写进 pyproject
uv run --with <pkg1> --with <pkg2> python spikes/<topic>/probe.py
# 或临时 venv：python -m venv .spike-venv && ... （用完删）
```

只写**回答问题所需的最少代码**。硬编码、跳过错误处理、print 一切——这是特性不是缺陷。

### Step 3 — 跑实验、如实记录

跑起来，观察真实行为，把**发现原样记下来**（成功的、失败的、意外的、报错、耗时），不要美化，
不要为了让结论好看而调参数掩盖问题。

### Step 4 — 到点决策

时间盒到（或更早得到确定答案）就停，从三选一：

- **可行**：判据满足 → 给正式实现的建议（用哪个库、注意哪些坑）。
- **不可行**：判据不满足 → 说清楚卡在哪、为什么，是否有替代方向。
- **需再探**：问题比预想复杂 → 缩窄成更小的问题，再跑一个短时间盒（而不是无限延长当前这个）。

### Step 5 — 产出结论 + 处理代码

写结论文档（下方模板），然后处理 spike 代码：**丢弃**（`rm -rf spikes/<topic>` 或不提交），
或**归档**到 `spikes/` 并在文档里明确标注「实验代码，不可复用，正式实现需重写」。
删掉临时环境（`.spike-venv` 等）。**不 commit spike 代码进生产路径。**

## 结论文档模板

```markdown
# Spike 结论：<问题一句话>

- **判据**：<怎样算成功/失败>
- **时间盒**：<上限 / 实际用时>

## 做了什么
<最小原型试了什么、用了哪些临时依赖及版本>

## 发现
- <观察 1：真实行为 / 报错 / 耗时，如实写>
- <观察 2>

## 结论
可行 / 不可行 / 需再探 —— <一句话判定>

## 对正式实现的建议
- <选型 / 架构 / 要避开的坑；spike 代码不可直接复用，需重写>
```

## 反模式

| 错误 | 正确 |
|------|------|
| spike 代码偷偷合进主干当正式实现 | 只喂结论，正式流程里重写 |
| 无时间盒，「再改一点」无限投入 | 定上限，到点必停并决策（超时也是结论） |
| 把实验依赖写进 pyproject / lock / 打包配置 | `uv run --with` 临时环境，用完即弃 |
| 给 spike 写测试、做抽象、追覆盖率 | 够回答问题就停，不追质量 |
| 问题太宽泛（「试试 X 框架」）无法判定 | 缩窄成封闭、可判定的一句话问题 |
| 验证完只留代码不留结论 | 必产出结论文档（可行性 + 发现 + 建议） |
| 需求本已明确却用 spike 拖延 | 无技术未知就直接走正式实现 |

## 自检

- [ ] Step 0 写下了单一封闭问题 + 可判定判据
- [ ] 定了时间盒，且到点做了三选一决策
- [ ] 代码隔离在 `spikes/<topic>/`，未碰任何生产源码/依赖
- [ ] 试库用临时环境，`pyproject.toml` / 打包配置零改动
- [ ] 产出了结论文档（可行性 + 发现 + 建议）
- [ ] spike 代码已丢弃或明确标注不可复用，临时环境已清理
- [ ] 未把 spike 代码当正式实现 commit 进生产路径

## 相关

- **执行清单**：验证可行后正式开发的多步拆解 → [task-planning](../task-planning/SKILL.md)
- **并行铺探针**：大范围预研分多路独立 spike → [subagent-orchestration](../subagent-orchestration/SKILL.md)
- **落地后清理**：正式实现完成后的行为不变清理 → [simplify-code](../simplify-code/SKILL.md)
