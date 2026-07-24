---
name: test-driven-development
description: "强制 TDD 红-绿-重构循环：动手实现任何行为前先写一个会失败的测试。用于质量优化、修 bug、加功能、或用户说「TDD / 先写测试 / 红绿重构 / test-first」时。铁律是每一步都由测试驱动——红（写失败测试并亲眼看它失败）→ 绿（写最小实现让它通过）→ 重构（测试保持绿的前提下清理）。无专用工具，全靠已有的 read / edit / write / bash + 项目自带的 pytest / pytest-asyncio / ruff。无额外依赖。"
category: coding
---

# Test-Driven Development（红-绿-重构）

用这个技能在**写任何生产代码之前先写测试**：修 bug、加功能、做质量优化，或用户明确说
「TDD / 先写测试 / 红绿重构 / test-first」时。核心是三段式循环 **红 → 绿 → 重构**，
每一轮只推进一个小行为，且**先看到测试失败**才允许写实现。

没有专门的工具——全靠已有的 `read` / `edit` / `write` / `find_files` / `search_content` / `bash`，
以及项目自带的测试栈（psi-agent 用 `pytest` + `pytest-asyncio`，`asyncio_mode = "auto"`，配 `ruff`）。
**无额外依赖**：测试基建已经就绪，不需要动 `pyproject.toml`，因此也不需要改 nuitka / pyinstaller。

除非用户明显用其它语言，一律用中文回复。

## 铁律（最高优先级）

- **先写测试，再写实现。** 每一轮循环，第一步永远是写一个新的、会失败的测试。
- **必须亲眼看到红。** 写完测试**先跑一次，确认它因为「功能还没实现」而失败**（不是因为语法错、import 错、
  测试本身写错）。没见过红的测试不算数——可能它永远为真、根本没测到东西。
- **只写让当前测试变绿的最小代码。** 不要顺手实现测试没覆盖的分支、不要提前抽象、不要"预留扩展点"。
  想到的额外行为 → 记下来，下一轮用新测试驱动。
- **重构只在绿的时候做。** 且重构前后测试必须都绿，不改任何测试期望。改期望 = 新需求 = 回到红。
- **一次一个行为。** 一轮循环对应一个可观察行为的小增量。别在一个测试里塞多个不相关断言。
- **不 commit、不 push**（除非用户明确要求）。产出是工作区里的测试 + 实现；提交是用户发起的另一步，
  遵循 [git-workflow](../git-workflow/SKILL.md) 的安全规则。

## 何时用 / 不用

- **用**：新功能、修 bug（用一个复现 bug 的失败测试开场）、给缺测试的既有逻辑补网、
  行为可以用测试明确表达的质量优化。
- **不用**：纯探索/spike（先摸清方向，摸完再用 TDD 正式做）；改动无法被自动化测试观察（纯视觉/CSS 布局
  ——这类以 `build` 通过 + 明确人工核对为验收）；纯行为不变的清理去重（那不是 TDD 场景，行为不变的重构应在既有测试绿灯下进行）。

## 循环：红 → 绿 → 重构

### 🔴 红 — 写一个失败的测试

1. 把「下一个最小行为」想成一句话（例："空输入时返回空列表"）。
2. 定位/新建测试文件。psi-agent 的 workspace 测试在 `examples/<workspace>/tests/test_<name>.py`，
   源码级在 `tests/psi_agent/...`；命名、fixture、风格**照抄同目录既有测试**。
3. 写测试：明确的输入 → 断言期望输出/副作用。异步代码直接写 `async def test_...`（`asyncio_mode="auto"` 会接管，
   无需 `@pytest.mark.asyncio`）。外部 IO（网络/子进程/文件）用 mock/fake 或 `tmp_path`，别打真实服务。
4. **跑它，确认红**，且红的原因正确（缺实现，而非笔误）：

```bash
# 单文件/单用例，绕开根 pyproject 的 --cov，跑得快、输出干净
pytest examples/<workspace>/tests/test_<name>.py::test_<case> -o addopts="" -p no:cov -p no:cacheprovider -q
```

看到 `FAILED`（`AssertionError` 或 `NameError`/`AttributeError` 指向未实现的目标）才算合格的红。
若是 `SyntaxError`/import 错/fixture 缺失 → 先修测试本身，再重新确认红。

### 🟢 绿 — 写最小实现让它通过

1. 写**刚好够让这个测试过**的实现代码。丑一点没关系，先绿。
2. 重新跑同一条用例，确认变绿。
3. 再跑该文件/该模块的**全部**相关测试，确认没有碰红别的用例（防回归）。

### 🔧 重构 — 绿灯下清理

1. 现在（且仅现在）可以去重、改名、抽函数、去嵌套、补类型标注。
2. **不动任何测试期望。** 每改一小步就重跑测试保持绿。
3. lint/format 收口：

```bash
ruff check <改动的文件> && ruff format --check <改动的文件>
```

> 注意：psi-agent CI 同时跑 `ruff check` 和 `ruff format --check`，两个都要过（引号规范化差异会挂 format）。

### 循环

回到 🔴，用一个新的失败测试驱动下一个行为。重复直到该功能/修复的所有行为都被测试覆盖且实现完整。

## 收尾验证（关键）

功能做完后，跑一次相关范围的**全量**测试作为总验收：

```bash
pytest examples/<workspace>/tests/test_<name>.py -o addopts="" -p no:cov -p no:cacheprovider -q
# 若改到多个模块，扩大到相关目录；根级全量 pytest 可能因 --cov 很慢，按需缩范围
```

- 全绿 + `ruff check` / `ruff format --check` 通过才算完成。
- 清掉任何临时/探针文件。
- **不 commit**——问用户是否提交，要则走 [git-workflow](../git-workflow/SKILL.md)。

## 反模式

| 错误 | 正确 |
|------|------|
| 先写实现，事后补测试 | 永远测试先行，先看到红 |
| 测试一写完直接假设它对，没跑 | 每个新测试先跑一次确认红且红得对 |
| 测试因 import/语法错失败就当红通过了 | 那是坏测试，先修测试再看真红 |
| 一口气实现测试没覆盖的一堆分支 | 只写让当前测试绿的最小代码，其余下一轮 |
| 为了让测试过去改测试期望 | 改期望=新需求，回到红另起一轮 |
| 红灯状态下重构 | 重构只在绿灯做，前后都绿 |
| 一个测试塞进多个不相关行为 | 一轮一个行为，断言聚焦 |
| 为跑测试临时加依赖/改 pyproject | 用已就绪的 pytest 栈；真要加依赖先跟用户对齐并同步 nuitka/pyinstaller |
| 擅自 commit/push | 产出留工作区，提交由用户发起 |

## 自检

- [ ] 每个行为都由一个**先失败**的测试驱动，且亲眼确认过红（红的原因是缺实现）
- [ ] 实现是让当前测试变绿的**最小**代码，没提前实现未覆盖分支
- [ ] 重构只在绿灯下做，未改动任何测试期望
- [ ] 异步用例遵循 `asyncio_mode="auto"`，外部 IO 已 mock / 用 tmp_path
- [ ] 收尾全量测试全绿 + `ruff check` / `ruff format --check` 通过
- [ ] 未新增依赖（沿用现有 pytest 栈）；未擅自 commit / push
