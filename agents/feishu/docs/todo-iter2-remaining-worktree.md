# Todo list 第二期（静态二层可先行 + 动态一层补全）剩余实现 — 工作树

> PR：`feat/todo-iter2-remaining`
> 依据：《Todo list迭代方案第二期-静态二层与动态一层.docx》（9.03）+ 上下文里已核实的代码现状
> 原则：**现在能做的一定做；做不了的（缺数据层）不硬做，写明缺什么；「可先行」是迭代期概念，不写进代码层。**

## 一、本次已做（2 项）

| 任务 | 内容 | 文件 |
|---|---|---|
| A2 防复制相似度工具 | 新增确定性工具 `feishu_text_similarity`：最长公共子串长度归一（`2*lcs/(len_a+len_b)`，difflib.SequenceMatcher），纯函数 + async 壳，`threshold` 默认 0.85（评审可调），返回 `{similarity, similar, matched_fragment}`；只产证据不做定性。truthfulness 接入「防复制（静态抓应付）」节：**同周期跨人 ≥ 阈值 → 只判 `存疑／待跟进` + matched_fragment 证据，不判失实**（红线定性留人工确认）；动态抓滑坡注为后续出口。AGENTS.md 工具表登记 | `tools/feishu_text_similarity.py`、`skills/todo-truthfulness-check/SKILL.md`、`AGENTS.md` |
| B3 audit E2 摄入 | audit 取证节加「E2 证据摄入」：对「推断已完成/待确认」与 E1 取不到的条目自动走 `feishu_message_search`（**user-token-only，必带 user_key**）/ `feishu_thread_read` 检索验收确认/交付评论进 audit 依据；**只补证据、不替代闭环五要素要件**；失败照 unavailable、取证对称性不变 | `skills/company-todo-audit/SKILL.md` |

测试：`tests/test_feishu_text_similarity.py`（新增）、`test_todo_truthfulness_check.py`（+1 防复制）、`test_todo_dynamic_layer1.py`（+1 E2 摄入）。

## 二、未做与原因（写清楚缺什么）

1. **A1 维度分档标注「可先行/待数据层」——按口径不进代码层**：
   - 缺：无（技术上可写）——但「可先行」指代的是**本迭代现在要做**的范围，不是判定标准的永久属性；
   - 决定：skill 保留 D1-D6 **全量判定标准**与既有兜底（`无法跟进` / `存疑／待跟进` / 查询失败明说），不把分档写进规则；
   - 执行范围（当前跑哪些维度、哪些待数据层就绪再点亮）由**调度/部署侧**按数据层就绪度决定——用户在对话里建 `todo-check` 的 schedule content 时自定。
2. **待数据层维度真正点亮**（D1 可跟进性自动判、D3 进度真实性用任务 `completed_at` 对声明、D5 验收状态机落地）：
   - 缺：任务系统接入（`completed_at` 权威源 + `feishu_task` 调用权限）、过程文档与交付物记录、相应授权；
   - 现状：数据层改造不在本期范围；skill 全量口径下数据取不到按 `unavailable`/`无法跟进` 兜底，不会误判成违规。
3. **A2 动态防滑坡消费侧**（本人本期 vs 上期相似度跳升 → 关注名单/趋势）：
   - 缺：`.todo-eval` 多期连续积累（判定落库刚有，需跨多期样本）+ 独立的消费迭代（趋势检测 + 干预分级）；
   - 现状：只把「动态抓滑坡」注为后续出口，不展开实现。
4. **A3 schedule 仓库实体**：
   - 缺：无——按系列约定定时任务由用户在与 agent 对话中 `schedule_manage` 自建，仓库不下发实体；
   - 合入后需重建 `todo-check` 的 schedule content：写明**当前跑哪些维度（按数据层就绪度）**并引用 `feishu_text_similarity` 防复制。
5. **B1 判定落库工具化**（文档原设计 `feishu_todo_eval_put` 确定性工具）：
   - 缺：无——按仓库哲学「规则是文字不是代码」，判定落库已由 `company-todo-audit` skill 指令写 `.todo-eval/YYYY-MM-DD.json` + 飞书评测记录表（幂等），目标（判定可回放）已达成，不另做工具。

## 三、验证

`ruff check` / `ruff format --check` 全绿；`pytest`（相似度 + truthfulness + dynamic_layer1 + 既有 todo 测试）全绿（用例数见提交说明）。
