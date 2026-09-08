# 会话 2 · 内容分层 + 工具架构演进 · 执行交付

2026-09-08 · zsd+Claude · 分支 `feat/tob-content-layering`(基于 `origin/main` 14 commits 之后)

## 结论

拆五张卡并行执行。**三张已收活并合入**,两张在跑。三处新实测把方案的预期又改了两条:

| 卡 | 内容 | 状态 | 关键实测 |
|---|---|---|---|
| `4724a` | F1 同名工具覆盖方向对齐 | **已合** `953918a9` | 独立变异复核: 2 条用例红,断言正指向被改行 |
| `4a014` | F4 元工具一致性判据 | **已合** `06ecbc6a` | 独立变异复核: 精确报出文件名 + 第 67 行 |
| `4bd21` | 阶段 B 跨层隔离探针 | **已合** `4dfb9ed5` | **P1 也红**,推翻方案"P1 应绿"的预期 |
| `3905b` | F2 跨会话模块复用 | 在跑 | — |
| `22710` | `_feishu` 循环导入 | 在跑 | — |

**最重要的新发现:阶段 B 的风险比方案估的大。** 详见 §二。

三条决定(已定,据此拆卡):

1. **F1 修法**:`get()` 与装表期对齐(最小改动),不动 `_files` 数据结构语义。
2. **元工具**:不提共享层,改加一致性判据。据此**阶段 E 的硬前置解除**。
3. **F2 跨会话复用**:纳入本次范围(按你的指示)。

---

## 一、分支与基线

### 1.1 合并 `origin/main`

开工前同步远程,`origin/main` 领先 14 个 commit,且**动过我方案依赖的全部四个文件**:

```
agents/feishu/tools/_feishu_impl.py           | 109 +++++++-
src/psi_agent/session/tool_defs.py            |  17 ++
src/psi_agent/session/tool_registry.py        |   3 +
tests/psi_agent/session/test_tool_registry.py |  25 ++
```

所以**在合并后的状态上把四条核心结论逐条重验了一遍**,没有沿用旧结论:

| 结论 | 合并后复测 |
|---|---|
| F1 `tools` 后胜 / `get()` 先胜 | **仍在**(desc=PERSONAL / get()=OFFICIAL) |
| F3 dotted 包跨层污染 | **仍在**(layer B 看到 LAYER-A) |
| F2 相同 `session_id` 仍全量重编 | **仍在**(复用 0 个文件) |
| F4 五个元工具 byte-identical | **仍是** |

上游那 3 行改动是给 `_exec_tool_files` 加了 `__module__` 过滤(import 进来的 async 辅助函数不算工具),
与本次四项均无冲突,卡里已明确交代"不要动它"。

### 1.2 全量测试基线

合并后跑全量:**88 failed / 5549 passed / 8 skipped**(32分46秒)。

**这个数字与既有认知的 57-62 基线不符,尚未归因完成。** 已看到的失败集中在
`test_channel_adapter.py`(6 条)与 `test_server.py`(5 条),形态是 asyncio 子进程相关
—— 与 Windows 上那 5 条恒失败同族,但数量对不上。

正在跑一次带 `-rf` 的完整失败清单以做归因。**在归因完成前,不把 88 当基线、也不当回归**
—— 88 是在"已合入 14 个上游 commit"的状态上量的,上游自己可能带进了失败;
而 57-62 那个区间是几周前在旧状态上量的。两个数字不可直接相减。

判据纪律:所有卡的回归判断都**只用子树用例**(每张卡改动范围明确),不依赖全量数字。
全量归因作为独立事项收尾,见 §五 待办。

---

## 二、阶段 B 实测:风险比方案估的大(最重要的新发现)

`4bd21` 的探针在**两层同时打开**的场景下跑出 **3 passed + 5 xfailed(strict)**。

### 2.1 预期与实测的差

方案(和我上一版文档)预测:**P1/P3 绿、P2/P4 红** —— 即裸名 `_helper.py` 隔离没问题,
只有 dotted 包漏。

实测:

| 用例 | 形状 | 方案预测 | 实测 |
|---|---|---|---|
| P1 | 裸名 `_helper.py` 两层同名 | 绿 | **红** |
| P2 | dotted `_pkg/sub.py` 两层同名 | 红 | 红 |
| P3 | 个人层 import 官方层裸名 | 绿 | 绿 |
| P4 | 个人层 import 官方层 dotted | — | 绿 |

**P1 红是新信息。** 探针的失败原因写得很清楚:两层同时在 scope 里,
裸名 helper 解析到**谁在 `sys.path` 上更靠前**,而 stash/restore 那一对**在两层之间根本不运行**
—— 它的触发点是"scope 进出",同时打开就没有"出"。

### 2.2 含义:修法范围变大

原判断是"候选 A 只给裸名槽位加层 id 不够,还要处理 dotted"。实测把它推到更前面一步:

**stash/restore 机制在"多层同时打开"下整体不适用,不是补一个 dotted 分支就行。**
它是个"一次一个目录"的机制,分层要的是"同时多个目录",这是机制的适用前提被推翻,
而不是覆盖面不全。

所以候选 A 的实现必然要动 import 解析本身(方案 §3.3 已预感到:
"要改 import 解析,`_stash_private_modules` 的 `resolve()` 比对逻辑要重写",
但按 P1 也红来看,重写比对逻辑不够,得换机制)。
§三 那三条退路里,**退路 A1(给每层装 meta path finder)从备选升为主选候选**。

### 2.3 P3/P4 现在绿,但这是"绿而不吃劲"

派生场景(个人层 import 官方层私有模块)现在两种形状都绿。**这不是功能可用的证据。**

它绿的原因恰恰是**零隔离**:两层共享同一个命名空间,所以当然能互相看见。
等候选 A 把隔离做起来,P3/P4 才会变成真正吃劲的判据 —— 那时它们要防的是
"隔离做得太狠、把派生也挡掉"。

探针文件里已经把这层含义写进 docstring。**这两条用例现在的绿不能当验收凭据**,
这一点必须在阶段 C 开工时重申,否则会有人拿"P3/P4 一直是绿的"来证明派生没问题。

### 2.4 探针本身的质量

两处做得对,值得留作后续模板:

- **两个层序各跑一次**(`official-first` / `personal-first`),所以结论不可能是
  "谁恰好在 `sys.path` 前面"的偶然产物。
- **判定靠回读模块内容标记**(`MARKER`),不看 `sys.modules` 内部状态 ——
  所以修法换了用例不用改,判据不会绑死在某个实现上。
- 5 个 xfail 全部 `strict=True`:修好之后会变 XPASS 而 strict 让它**变红**,
  强迫那时的人回来删标记。缺陷被锁住,不会静默遗忘。

---

## 三、已合入的两项改动

### 3.1 F1 · 同名工具覆盖方向对齐(`953918a9`)

改动 14 行,核心是一处:

```python
# src/psi_agent/session/tool_registry.py:439
for entry in reversed(self._files.values()):   # 原为 for entry in self._files.values()
```

`tools` property 用 `update()` 逐文件覆盖 → 后胜;`get()` 原来命中即返回 → 先胜。
两者方向相反,同名时模型看到个人层的描述和 schema、执行官方层的函数体。
参数不兼容时报错,兼容时**静默给出错误结果**。

docstring 把理由写在了改动处,并明确"只有冲突的赢家变了,早先文件里独有的名字仍然找得到"
—— 全集可达没被破坏(红线二)。

**独立变异复核**(我自己做的,不只看卡的自述):把 `reversed` 去掉后

```
FAILED test_get_last_file_wins
FAILED test_duplicate_name_metadata_and_callable_come_from_same_file
assert 'PERSONAL' == 'OFFICIAL'
2 failed, 90 passed
```

断言内容(`'PERSONAL' == 'OFFICIAL'`)**正指向被改的那件事**,不是撞到别的兜底分支。
复核后已还原,工作树干净。

顺带把 `test_get_last_file_wins` 那条假绿用例重写了:原来函数名说 last、docstring 说 first、
断言 `in ("a","b")` 恒真,三者互相矛盾。

### 3.2 F4 · 元工具一致性判据(`06ecbc6a`)

97 行新测试,10 条判据盯 5 个文件(`_tool_index.py`、`tool_describe.py`、`tool_search.py`、
`tool_search_code.py`、`skill_manage.py`),换行归一化后比对内容。

**独立变异复核**:给 `agents/desktop/tools/tool_search.py` 追加一行注释,报错是

```
tool_search.py differs between the two packs: the first 66 lines match,
but desktop has 2 extra line(s) from line 67 while feishu ends at line 66.
```

精确到文件名与行号,不是甩一个几千行 diff。复核后已还原,工作树干净。

**这一项让阶段 E 的硬前置解除**:分层暴露只跟到一侧的风险由 CI 覆盖,
不必等"元工具是否提共享层"这个决定。

---

## 四、实测到的 vs 没验到的

### 已实测(本轮)

| 事实 | 方法 |
|---|---|
| F1/F2/F3/F4 四条结论在合并 14 个上游 commit 后**全部仍然成立** | 逐条重跑探针脚本 |
| F1 修法的变异能让 2 条用例红,断言指向被改行 | 我自己去掉 `reversed` 跑一次 |
| F4 判据的变异能精确报出文件名与行号 | 我自己追加一行跑一次 |
| 阶段 B: 两层同时打开时 **P1 也红**(方案预测绿) | `4bd21` 探针,两个层序各跑 |
| P3/P4 绿是零隔离的副产物,不是派生可用的证据 | 读探针实现 + 失败原因 |
| 三卡合并后子树用例全绿(102 passed + 3 passed/5 xfailed) | 合并后在分支上重跑 |
| 上游那 3 行是 `__module__` 过滤,与本次四项不冲突 | 读 diff |

### 没验到(明确交代)

| 未验 | 为什么 | 打算怎么办 |
|---|---|---|
| **全量 88 failed 的归因** | 与既有 57-62 认知不符,清单还在跑 | §五 待办第 1 条。**在归因完成前不声称"无回归"** |
| F2 跨会话复用的实际效果 | 卡 `3905b` 还在跑 | 收活时按"编译次数前后对比"验,并独立做变异复核 |
| `22710` 循环导入的修法是否结构性消环 | 卡 `22710` 还在跑 | 收活时验"干净解释器直接 import `_feishu.mentor_ledger`"能过 |
| 候选 A 能否同时满足判据 7/8 | 探针只做了基线,修法未实现 | 阶段 C。按 §2.2,退路 A1 升为主选候选 |
| `refuse_agent_write` 分层后会挡住哪些工具 | 需逐个核对写 agent 包的工具 | 阶段 C 末尾产出清单。方案说这是最可能造成用户可见回归的一处 |
| 阶段 E 三档字符数比值 | 新机制不存在,本地无可比输入 | 阶段 E,固定输入量相对值 |
| 阶段 A 收债的 49 个文件 | 本轮未做 | 阶段 A。判据见上一版文档 |

### 本轮完全没碰

`gateway/`、`desktop/`(除 F4 变异复核时临时追加一行、已还原)、ToC 侧代码、
`deploy/` 下的部署配置、生产环境。

---

## 五、待办

1. **全量 88 failed 归因**(阻塞"无回归"结论)。要做的控制实验:在 `3b704ac6`
   (合并上游、未加任何卡的产出)上跑一次全量,与 88 对比 —— 这才能分清是上游带进来的
   还是我们改的。**不要拿 88 和几周前的 57-62 直接相减。**
2. 收 `3905b` 与 `22710`,各自独立做变异复核后合入。
3. 按 §2.2 更新方案文档里"候选 A 只需处理 dotted"的表述,以及
   `2026-09-08-session2-content-layering-design-review.md` 里"预期 P1/P3 绿"的预测。
4. 提 PR:标题带数字,正文分问题/改法/验证三段,没验到的如实交代(尤其第 1 条)。

## 附:分支与提交

```
feat/tob-content-layering  (基于 origin/main @ e0e0d02b)
├── 3b704ac6  Merge origin/main (14 commits)
├── 953918a9  fix(session): 同名工具的元数据与函数体统一解析到后加载那份
├── 06ecbc6a  test(agents): 元工具两份一致性成为 CI 门, 10 条判据盯 5 个文件
└── 4dfb9ed5  test(session): 跨层私有模块隔离探针——4 用例矩阵实测 5 个 xfail
```

改动统计(相对 `origin/main`,不含文档):

```
src/psi_agent/session/tool_registry.py             |  14 +-
tests/agents/test_meta_tools_in_sync.py            |  97 +++
tests/psi_agent/session/test_layer_isolation_probe.py | 319 +++++
tests/psi_agent/session/test_tool_registry.py      |  97 ++-
```
