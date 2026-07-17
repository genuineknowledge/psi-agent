# 飞书多维表格工具 + mentor 反馈收集 —— 实现计划

日期：2026-07-14
分支：`add-feishu-tools1`
状态：待批准

## 目标

给 agent 加"mentor 反馈收集+汇总"能力：mentor 在对话里给新人反馈，agent 把反馈**写入飞书多维表格（bitable）**存档；也能**读回/汇总**某人的反馈。数据落在 bitable（团队在飞书里可见可查）。

用户已确认：**不做打卡**；反馈=agent 收集+汇总；存储载体=**飞书多维表格**。

## 关键调研结论（已查证）

- **SDK 无 bitable builder**：`lark_channel/api/` 只有 cardkit/contact/drive/im/wiki，**没有 bitable/base/record**。故全部**手搭 BaseRequest**（沿用现有 `feishu_message`/`feishu_docs` 的手搭模式）。
- **bot 的 tenant token 可读写 bitable 记录**（无需用户 OAuth），权限 scope：`bitable:app`（读写）或 `bitable:app:readonly`（只读）。**前提**：把这个应用**加为该多维表格的协作者/可编辑**，否则 403（错误码 1254302 = 权限不足/高级权限）。
- 端点（均 `open.feishu.cn`，tenant token）：
  - 列数据表：`GET /open-apis/bitable/v1/apps/:app_token/tables`（返回 items[{table_id,name}]）
  - 列记录：`GET /open-apis/bitable/v1/apps/:app_token/tables/:table_id/records`（query: page_size≤500, page_token, filter, sort, field_names；返回 items[{record_id, fields}]）
  - 建记录：`POST /open-apis/bitable/v1/apps/:app_token/tables/:table_id/records`（body: `{fields: {列名: 值}}`）
- **app_token 从 URL 取**：`feishu.cn/base/XXXX` → XXXX 即 app_token；`feishu.cn/wiki/XXXX` → 先 `feishu_wiki_get_node`，当 `obj_type==bitable` 时 `obj_token` 即 app_token（已有工具，能复用）。

## 架构（沿用现有分层，全落 haitun-workspace，src/ 零改动）

### A. `tools/_feishu_impl.py` 新增 bitable impl（手搭 BaseRequest，token_types 含 TENANT）

- `_build_list_tables_request(app_token, page_size, page_token)` + `list_bitable_tables_impl(...)`
- `_build_list_records_request(app_token, table_id, page_size, page_token, filter, sort, field_names)` + `list_bitable_records_impl(...)` —— 解析 items→[{record_id, fields}] + has_more + page_token
- `_build_create_record_request(app_token, table_id, fields_dict)` + `create_bitable_record_impl(...)` —— fields 是列名→值的映射
- 复用现有 `_invoke`（tenant token 自动附加）、`_error`、`dumps_result`。

### B. 通用 bitable 工具薄壳 `tools/feishu_bitable.py`

工具参数只用 str/int/bool（ToolRegistry 限制，dict 参数会被跳过），所以 create 的 fields 用 **JSON 字符串**传入，impl 层 `json.loads`：

- `feishu_bitable_list_tables(app_token) -> str` —— 列出某多维表格里的所有数据表（拿 table_id）
- `feishu_bitable_list_records(app_token, table_id, page_size=100, page_token="", filter="", sort="") -> str` —— 读记录
- `feishu_bitable_create_record(app_token, table_id, fields_json) -> str` —— `fields_json` 是 `{"列名":"值",...}` 的 JSON 字符串，新增一行

这套是**通用 bitable 读写工具**——不只服务 mentor 反馈，任何"往飞书表格记东西/读表格"的需求都能用（含之前 SOP 执行跟踪、日常登记等）。

### C. mentor 反馈：用「技能」编排，不硬编码

"收集+汇总反馈"是**流程**不是新 API——用通用 bitable 工具 + 一个 **SKILL** 教 agent 怎么做，而不是再写死一个 `feishu_mentor_feedback` 工具（更灵活、表结构可变）。

- 新增 `skills/feishu-mentor-feedback/SKILL.md`（category: 现有合适分类），内容：
  - **前置**：用户给一个"反馈表"的 bitable 链接（agent 用 base 链接取 app_token，或 wiki 链接经 `feishu_wiki_get_node` 解析）。
  - **建议表结构**：列 `新人`(text/person)、`Mentor`(text)、`日期`(date/text)、`反馈内容`(text)、`评分`(number,可选)、`标签`(text,可选)。
  - **收集**：mentor 在对话里说反馈 → agent 用 `feishu_bitable_create_record` 写一行（fields_json 拼上述列）。
  - **汇总**：`feishu_bitable_list_records` 拉全表（翻页），按"新人"分组，agent 生成汇总（近期反馈、共性问题、进步点），可再 `feishu_message_send`/`feishu_topic_start` 发给相关人。
  - 纯 markdown 配方，复用已有工具，零新依赖。

## 测试（`tests/test_feishu.py` 追加，mock `_invoke`，不打真实 API）

- `list_bitable_tables_impl`：断言 GET `/bitable/v1/apps/:app_token/tables`、app_token 入 paths、解析 items。
- `list_bitable_records_impl`：断言 GET records、query 透传（page_size/filter/sort）、解析 record_id/fields/has_more。
- `create_bitable_record_impl`：断言 POST records、body `{fields:{...}}`、fields_json 被正确 json.loads。
- 工具壳：async + docstring 校验；`fields_json` 非法 JSON 时 `ok=false` 不崩。
- ruff check + ruff format --check + ty check + pytest 全绿。

## 依赖与打包

- **零新增依赖**（手搭 BaseRequest 走已有 client）。不改 pyproject/nuitka/pyinstaller。

## 用户侧前置（飞书后台 + 表格）

- 应用开 `bitable:app`（读写）权限，发布。
- **把应用加为目标多维表格的协作者（可编辑）**，否则读写 403。
- 建一张反馈表（或让 agent 按建议列结构指导你建）。

## 不做（YAGNI）

- 不做打卡/考勤 API。
- 不做删除/更新记录、字段管理、批量导入（先覆盖 建/列/列表格 三件事，够跑通反馈闭环）。
- 不写死 mentor 表结构（用 skill 引导，列可变）。
- 不做 bitable 视图/仪表盘操作。

## 落地顺序

1. `_feishu_impl.py` 加 3 个 bitable impl + 测试（红→绿）。
2. `tools/feishu_bitable.py` 三个工具壳。
3. `skills/feishu-mentor-feedback/SKILL.md`。
4. ruff+ty+pytest 全绿 → 提交推送 → 更新 PR #350。
5. 交付：用户配 bitable:app 权限 + 把 app 加为表格协作者 + 给反馈表链接 → 端到端联调。
