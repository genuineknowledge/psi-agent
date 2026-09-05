# 飞书卡片工具进度状态行 · 设计

分支：`feat/feishu-tool-progress-status-line-20260905`（基于 `main` = `c76a2962`）

## 结论

1. **信号早就在流上，缺的只是飞书侧最后一段渲染。** `session/agent.py` 两处 yield、
   `channel/_core.py` 分桶还原都已就位；`channel/feishu/client.py` 收到
   `ReasoningChunk` 只把 `tool_result` 当 NO_REPLY 抑制的时钟信号用，从不渲染。
2. **卡片只有一个 markdown 格子**，状态行与正文没有两个槽位，只能拼在同一个字符串里。
   分工：正文走 `append`（保住 100ms/50 字节流），状态行走
   `set_content(状态行 + "\n\n" + 正文)`，只在工具边界调用。
3. **`append` 与 `set_content` 混用的吞字风险是真的，靠时序消除而不是靠祈祷。**
   `merge_streaming_text(prev, chunk)` 会丢掉「prev 的后缀 == chunk 的前缀」那一段，
   实测 `merge('abc','cdef') == 'abcdef'`。修法：正文 `append` 之前先
   `set_content(正文)` 抹掉状态行，于是 `prev` 永远只是正文，与本改动前逐字节相同。
4. **工具名走结构化字段 `tool_name`，不从 `reasoning` 文本里正则抠。** 那段文本是
   `[Tool Call: name({...})]`，参数里带嵌套括号和引号，解析在最要紧的场合最脆。
5. **卡片上一个字都不来自 `reasoning` 文本。** 只显示白名单里的中文别名；表外工具走通用
   兜底，**不** fallback 到工具名本身。
6. **抹除状态行不设抑制门，显示才设 —— 这个不对称是修出来的。** 第一版两边都门控，撞上
   「真回复 → 工具调用 → `tool_result` 重新武装抑制」的序列时，状态行被永久钉在卡片上，
   由判据 `test_tool_result_still_rearms_the_silent_check` 抓出。

---

## 一、设计目标

一次工具调用可能跑 50 秒，这期间卡片停在 SDK 硬编码的 `Thinking...`
（`lark_channel/channel/outbound/streaming/markdown_stream.py:23`）一动不动，用户以为
机器人死了就重发。

目标形态是卡片顶部**一行**状态，随工具推进原地改写：

    ⏳ 正在检索代码库…

正文开始出字时把状态行抹掉，只留正文：

    根据你提供的三份文档,我整理出以下要点…

**不做多行步骤清单。** 流里只有「刚调了 X」，没有未来 —— 模型不预先声明要调几个工具，
所以「待办」那一档做不出来；而累加式清单会随回合越滑越长，把正文挤出屏幕。

## 二、实现方案

### 2.1 结构化工具名（新增字段，五处）

| 文件 | 改动 |
| --- | --- |
| `session/protocol.py` | `AgentChunk.tool_name` |
| `session/agent.py` | 两处 yield 各带上 `tool_name=func_name` |
| `session/channel_adapter.py` | `_to_sse` 把它接进 `DeltaMessage` |
| `protocol.py` | `DeltaMessage.tool_name` + `to_dict` 按「未设不出现」序列化 |
| `channel/_types.py` | `ReasoningChunk.tool_name` |
| `channel/_core.py` | `_buffer_key()` / `_to_chunk()` 一对，编进 key 再还原 |

**为什么加字段而不是解析文本**：见结论 4。代价是要动五处、且都得对没有该字段的旧流兼容
（`to_dict` 未设即不出现，`_to_chunk` 拿不到就是 `None`）——
判据 `test_to_chunk_tolerates_stream_without_tool_name` 与
`test_delta_message_serializes_tool_name` 各锁一头。

**为什么工具名要进 `StreamBuffer` 的桶 key**：缓冲区按 key 合并连续同类文本。工具是并发
执行的，两条 `tool_call` 会连着来；只按 `reasoning:tool_call` 分桶会把它们融成一条，而
一条只能带一个 `tool_name`。key 形如 `reasoning:tool_call\x1f<tool_name>`，用 `\x1f`
（ASCII 单元分隔符）而不是冒号，避免与已有的 `reasoning:<provenance>` 切分打架。

### 2.2 状态行（新文件 `channel/feishu/_tool_status.py`）

- `TOOL_ALIASES`：46 条工具名 → 中文别名，覆盖 M2 高频集，由
  `test_alias_table_covers_the_m2_core_set` 锁死（缺一个就在生产里显示成兜底）。
- `GENERIC_TOOL_LABEL = "正在调用工具"`：表外工具的兜底，刻意不含工具名。
- `status_line_for(running)`：渲染一行；`None` 表示「这一行该消失」。
- `ToolStatusTracker`：按工具名计数。计数不为负 —— 结果先到或名字对不上时直接忽略，
  否则一次错位会让状态行在整个回合里永久偏移。

**并发怎么表示**：报「其中一个 + 还有几个」，如
`⏳ 正在读取文件…(另有 2 个工具在跑)`。铺开列名会让这行长度随并发度变化；报个数长度有界，
信息量也够（用户要的是「还在动」，不是精确工具清单）。按到达顺序取第一个，与用户看到的
顺序一致。计数只认名字不认 id：流上没有配对标识，所以同名并发两次算两个，先回来的结果
只减一 —— 这正好是「还有一个在跑」。

### 2.3 渲染（`channel/feishu/client.py:_stream_reply`）

`_produce` 里新增三个闭包，共用 `body`（本函数自己维护的「正文那一半」）：

- `render_status(line)`：`line is None` 抹掉，否则 `set_content(line + "\n\n" + body)`。
- `append_body(text)`：先 `render_status(None)`，再 `body += text`、`stream.append(text)`。
  正文的唯一入口，`flush_silent_candidate` 也改走它。
- 分派：`tool_call` → `tracker.on_tool_call`，`tool_result` → `on_tool_result`。

### 2.4 与 NO_REPLY 抑制正交

`tool_result` 这个 chunk 已被征用为「上一次卡片动作办完了、下一段重新开始攒」的时钟信号。
新逻辑**读同一个 chunk，各走各的**：抑制那半段（`checking_silent_reply` /
`silent_candidate`）一个字没动，状态行只是另外读一遍。

**静默回合不许冒卡**（独立于上一条的第二条回归路径）：`_ensure_started` 在首次
`append`/`set_content` 时才建卡，静默回合正文全攒在 `silent_candidate` 里没进 `_content`，
所以今天卡片压根不出现 —— 用户点个按钮界面什么都不动，这是正确行为。一旦无条件渲染状态行，
`_ensure_started` 被触发，就会跳出一张写着「正在整理待办…」的卡。

**判断：抑制期不渲染状态行，且事后不补。** 等抑制解除时工具早已跑完（它们的结果正是重新
武装抑制的那个信号），补上去就是在报一件已经做完的事。这个回合退化成只渲染正文，与本改动
前行为一致。

**但抹除不设门。** 状态行可见意味着卡片已存在、`body` 里是已发出的文本，写 `body` 既不建卡
也不泄漏未嗅探的候选。第一版把抹除也门控了，于是「真回复 → 工具 → `tool_result` 重新武装」
这条序列里状态行永久留在卡上（判据实测抓出，非推演）。

## 三、状态行映射表

文案与颗粒度属产品侧，这是可用的一版而非终版。几个工具共用一句别名是刻意的
（`feishu_sheet_read` / `feishu_sheet_read_grid` 对用户是同一件事）。

| 工具 | 别名 | 工具 | 别名 |
| --- | --- | --- | --- |
| `bash` | 正在执行命令 | `feishu_doc_read` | 正在读飞书文档 |
| `read` | 正在读取文件 | `feishu_doc_create` | 正在新建飞书文档 |
| `edit` | 正在修改文件 | `feishu_doc_update_block` | 正在修改飞书文档 |
| `write` | 正在写入文件 | `feishu_doc_append_content` | 正在追加飞书文档内容 |
| `list_dir` | 正在浏览目录 | `feishu_doc_list_blocks` | 正在梳理文档结构 |
| `find_files` | 正在查找文件 | `feishu_docs_search` | 正在搜索云文档 |
| `search_content` | 正在检索代码库 | `feishu_sheet_read` | 正在读表格 |
| `fetch` | 正在抓取网页 | `feishu_sheet_read_grid` | 正在读表格 |
| `serper_google_search` | 正在搜索网络 | `feishu_sheet_find_columns` | 正在定位表格列 |
| `wiki_search` | 正在查维基百科 | `feishu_sheet_write` | 正在写表格 |
| `todo` | 正在整理待办 | `feishu_wiki_list_nodes` | 正在浏览知识库 |
| `clarify` | 正在确认需求 | `feishu_wiki_list_spaces` | 正在浏览知识库 |
| `tool_search` | 正在挑选工具 | `feishu_api` | 正在调用飞书接口 |
| `tool_describe` | 正在查看工具说明 | `feishu_message_list` | 正在翻阅聊天记录 |
| `tool_search_code` | 正在检索工具实现 | `feishu_message_send` | 正在发送消息 |
| `trigger_manage` | 正在设置定时任务 | `feishu_image_get` | 正在取图片 |
| `describe_image` | 正在看图 | `feishu_identity_get` | 正在查成员信息 |
| `read_document` | 正在读文档 | `feishu_department_members` | 正在查部门成员 |
| `read_pdf` | 正在读 PDF | `feishu_permission_list_members` | 正在查文档权限 |
| `write_word` | 正在生成 Word 文档 | `feishu_attendance_query` | 正在查考勤 |
| `write_word_from_markdown` | 正在生成 Word 文档 | `session_keyword_search` | 正在检索历史会话 |
| `memory_search` | 正在检索记忆 | `sessions_history` | 正在翻阅历史会话 |
| `memory_answer_context` | 正在回忆相关内容 | `session_status` | 正在查看会话状态 |

表外一律 `⏳ 正在调用工具…`。

## 四、判据

25 条新判据，三层各测各的、不互相替代：

| 层 | 文件 | 条数 |
| --- | --- | --- |
| 纯函数（别名 / 兜底 / 状态机） | `tests/psi_agent/channel/feishu/test_tool_status.py` | 12 |
| 飞书渲染（走 `_stream_reply` + 真控制器） | `tests/psi_agent/channel/feishu/test_feishu_tool_progress.py` | 13 |
| 线上字段 | `test__types.py` / `test_protocol.py` / `test__core.py` / `test_agent.py` / `test_channel_adapter.py` | 各 1-3 |

渲染层判据装的是**真的** `MarkdownStreamController`，只把底下四个 cardkit HTTP 调用换成
记录器 —— 换成 `AsyncMock` 会让 `merge_streaming_text` 与 `_ensure_started` 懒建卡这两件
风险全部消失，判据会假绿。断言落在「发给飞书的卡片内容」上，与用户看到的东西同层。

`test__core.py` 里的三条新判据刻意写成纯函数：同文件其余用例走 unix socket，在 Windows 上
恒失败（asyncio 子进程），判据落在那里等于没有判据。

### 变异复核

13 条变异，逐条改坏实现再跑判据，**13/13 变红**。其中一条第一次没抓住：

- **M4「正文开始后不抹状态行」初次为 GREEN。** 原判据
  `test_body_text_erases_the_status_line` 的序列里 `tool_result` 已先把状态行抹掉，于是
  `append_body` 里那次抹除根本没执行 —— 结论被前一个分支提前吃掉，正是本仓库
  「一个分支撞多次兜底会让用例看着绿其实没吃劲」那个坑。补了
  `test_body_erases_a_status_line_that_is_still_showing`（工具不回结果就出正文）后变红。

无等价变异，无打不中的变异（每条锚点均唯一命中）。

## 五、没验到什么

1. **真机渲染没验。** `⏳` 在飞书 markdown 元素里的实际表现**没在真机上确认过**，卡片效果
   是按 SDK 能力设计的。真机若显示为豆腐块，改 `_tool_status.STATUS_PREFIX` 一个常量即可，
   不动任何逻辑。整套状态行也**没在真机飞书上跑过一次** —— 判据全是进程内的。
2. **普通对话路径的抑制组合没验。** `main` 上普通对话**没有**开启抑制
   （`_stream_reply` 的 `suppress_silent_reply` 默认 `False`，只有卡片回调传 `True`）。
   另一个分支 `fix/feishu-noreply-elision-20260904`（PR 已提，正在解冲突）让普通对话也传
   `True`。本改动的代码**能承住**那种情况（抑制门控只看 `checking_silent_reply`，不假设
   调用来路），但**判据只声称覆盖 `main` 上现有的路径**。
   **两个改动合并后，「普通对话 + 进度渲染」这个组合是没人验过的。** 本仓库出过并行卡各自
   全绿、合完炸在收集期的事，所以这条单独写出来。
3. **并发状态行的真实表现没验。** 判据构造的是「三条 `tool_call` 连着来」，与生产里 task
   group 的真实交错时序**不是同一件事**。「另有 N 个工具在跑」在真实并发下的抖动频率
   （每条 `tool_call` 都会 `set_content` 一次，即一次 HTTP）未测量。
4. **shellcheck 那一步本地没跑**（未安装）。本改动不含 `.sh` 文件，该步不受影响。
5. **失败基线未清零。** `tests/psi_agent/channel/` + `tests/psi_agent/session/` 全量
   34 failed，做过控制实验（`git stash` 掉改动、核对目标文件 diff 为空、再跑一遍）：
   改动前后失败集合**逐条相同**，全是 Windows 上 asyncio 子进程 / unix socket 那批。
   `ty check` 4 条 `os.killpg` 亦为既有基线。
