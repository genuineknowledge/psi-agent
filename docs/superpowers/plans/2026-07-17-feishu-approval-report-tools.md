# 飞书审批报告闭环工具 + 技能 —— 实现计划

日期：2026-07-17
分支：`add-feishu-tools1`
状态：待批准

## 目标

补齐 haitun agent 在飞书上跑「假勤 / 报销」审批报告闭环所缺的工具与技能，让 agent 能：

1. **月度考勤劳务费**：拿到部门/群成员名单 → 批量查考勤 → 按你给的规则算劳务费 → 生成表格。
2. **报销单据归档校验**：拉审批实例 → 下载单据附件 → 按你给的条件校验 → 每笔一个文件夹 + 汇总表。

## 缺口结论（来自能力评估）

现有工具能查考勤（`feishu_attendance_query`）、查审批任务（`list_approval_tasks`）、读多维表格，但**缺三块**：

- 拿不到「某部门 / 全体成员的 user_id 名单」→ 考勤要按人查，没名单无法批量。
- 拿不到「某审批流下的所有实例 + 每笔详情」→ 报销要逐笔核对，只查"待我审"的任务不够。
- 下载不了「审批单据附件（发票图片/PDF）」→ 归档无从谈起。

所以本计划加 **3 个工具**（成员名单 / 审批实例列表+详情 / 文件下载）+ **2 个通用配方技能**。

## 已查证的接口与约束

### 1. 通讯录成员名单（Contact v3，tenant token 可用）

- 发现部门：`GET /open-apis/contact/v3/departments/:department_id/children`（根部门传 `0`，拿子部门 id）。
- 取部门成员：`GET /open-apis/contact/v3/users/find_by_department`
  - query：`department_id`、`department_id_type`（`open_department_id`/`department_id`）、`user_id_type`（`open_id`/`user_id`/`union_id`）、`page_size`（≤50）、`page_token`。
  - 返回 `items[]`：`user_id` / `open_id` / `name` / `mobile`(视权限) 等。
- **鉴权**：tenant_access_token 可用；scope `contact:contact.base:readonly`（+ `contact:user.employee_id:readonly` 才回 `user_id`/工号字段）。应用需在后台设「通讯录权限范围 = 全部成员」，否则只回授权范围内的人。

### 2. 审批实例列表 + 详情（Approval v4，tenant token 可用）

- 列实例：`GET /open-apis/approval/v4/instances`
  - query：`approval_code`（**必填**，某审批流的定义码）、`start_time`/`end_time`（Unix **毫秒**字符串）、`page_size`（≤100）、`page_token`。
  - 返回 `data.instance_code_list[]`（一串实例码）。
- 查详情：`GET /open-apis/approval/v4/instances/:instance_code`
  - 返回表单内容 `form`（JSON 字符串，含各控件 widget）、状态、发起人、审批节点等。
- **鉴权**：tenant token；scope `approval:approval:readonly`。

### 3. 附件下载（关键坑，已确认）

- 通用云文档素材：`GET /open-apis/drive/v1/medias/:file_token/download`（二进制流，tenant token，scope `drive:drive:readonly`）。
- **重要区别**：审批表单里的附件控件（`attachmentV2`/`image`/`imageV2`）**不是 drive token**——它们是表单 `value` 字段里的**直链 URL，仅 12 小时有效**，要**直接 GET 那个 URL 下载**，不走 medias 接口。只有 `document` 控件才回一个可走 drive 的 document token。
- 所以文件下载工具要**同时支持两种输入**：drive file_token（走 medias）或直链 URL（直接下）。

## 架构（沿用现有分层，全落 haitun-workspace，src/ 零改动）

### A. `tools/_feishu_impl.py` 新增 impl（手搭 BaseRequest，token_types {TENANT,USER}）

**成员名单**
- `_build_dept_children_request(department_id, department_id_type, page_size, page_token)` → GET `/contact/v3/departments/:department_id/children`。
- `_build_find_by_department_request(department_id, department_id_type, user_id_type, page_size, page_token)` → GET `/contact/v3/users/find_by_department`。
- `list_department_members_impl(department_id, department_id_type, user_id_type, recursive) -> dict`：
  内部 `while True` 翻页取全量成员；`recursive=True` 时先 children 递归收集子部门 id 再逐个取（沿用现有全量翻页模式）。精简成 `[{user_id, open_id, name}]`。

**审批实例**
- `_build_list_instances_request(approval_code, start_time, end_time, page_size, page_token)` → GET `/approval/v4/instances`。
- `_build_get_instance_request(instance_code)` → GET `/approval/v4/instances/:instance_code`。
- `list_approval_instances_impl(approval_code, start_time, end_time) -> dict`：翻页收全量 `instance_code_list`，回 `{codes:[...], count}`。
- `get_approval_instance_impl(instance_code) -> dict`：回状态/发起人/表单；**解析 `form` JSON**，抽出附件控件，输出 `attachments:[{name, kind:"url"|"drive", value}]`（url 类附带 12h 有效期提醒），方便技能直接喂给下载工具。

**文件下载**
- `_build_media_download_request(file_token)` → GET `/drive/v1/medias/:file_token/download`（需读 `resp.raw.content` 原始字节）。
- `download_file_impl(source, save_path, is_url) -> dict`：
  - `is_url=True`：用已有 httpx/client 直接 GET 直链 URL → 写 `save_path`（先建父目录）。
  - `is_url=False`：走 medias 接口拿字节 → 写 `save_path`。
  - 回 `{ok, path, bytes, message}`；URL 过期（403/404）时给出"附件链接可能已过 12 小时失效，请重新读实例详情"的明确提示。

### B. 工具薄壳（参数只用 str/int/bool，ToolRegistry 限制）

- `tools/feishu_contact.py`
  - `feishu_department_members(department_id: str = "0", department_id_type: str = "open_department_id", user_id_type: str = "open_id", recursive: bool = False) -> str`
- `tools/feishu_approval.py`（现有文件追加）
  - `feishu_approval_instances(approval_code: str, start_time: str = "", end_time: str = "") -> str`（start/end 为毫秒时间戳字符串，空则用近30天默认，由 impl 兜底）
  - `feishu_approval_instance_detail(instance_code: str) -> str`
- `tools/feishu_drive.py`（现有文件追加）
  - `feishu_file_download(source: str, save_path: str, is_url: bool = False) -> str`

### C. 技能（两个，均做成**通用配方**——规则不写死，每次调用你在对话里给）

- `skills/feishu-attendance-payroll/SKILL.md`（category: `productivity`）
  流程：确认人员范围（部门 id 或成员名单）→ `feishu_department_members` 取名单 → `feishu_attendance_query` 批量查考勤 → **按用户当次给出的劳务费公式与表格列格式**计算 → 用 powerpoint/excel 或多维表格工具产出。明确"公式/格式每次由用户提供，技能不内置任何金额规则"。
- `skills/feishu-reimbursement-archive/SKILL.md`（category: `productivity`）
  流程：`feishu_approval_instances`（给 approval_code + 时间段）→ 逐笔 `feishu_approval_instance_detail` → 对每笔在指定根目录下建 `报销-{申请人}-{实例码}/` 文件夹 → `feishu_file_download` 下载每个附件（**优先用详情返回的 url 类附件，注意 12h 时效，先下再核**）→ **按用户当次给出的校验清单**（金额/发票/抬头/日期等）逐条核对，输出结果表 + 每笔文件夹。明确"校验条件每次由用户提供，技能不内置"。

## 测试（`tests/test_feishu.py` 追加，mock `_invoke`，不打真实 API）

- 成员：`list_department_members_impl` 断言 GET `/contact/v3/users/find_by_department`、query 带 department_id/user_id_type/page_size；翻页两页合并；children 递归路径。
- 审批实例：`list_approval_instances_impl` 断言 GET `/approval/v4/instances`、query 带 approval_code + start/end；翻页收 code。`get_approval_instance_impl` 断言 `:instance_code` 路径；**form JSON 解析出 attachments（url 类与 drive 类各一例）**。
- 下载：`download_file_impl` 两条路径——`is_url=True` 走直链（用 `_FakeRaw`/monkeypatch 拦截写盘，断言 save_path 被写入、父目录创建）、`is_url=False` 断言 GET `/drive/v1/medias/:file_token/download` 且读 `resp.raw.content` 字节。URL 失效错误路径给友好 message。
- 各工具壳 `inspect.iscoroutinefunction` + 非空 `getdoc`。
- 全绿：`ruff check` + `ruff format --check` + `ty check` + `pytest tests/test_feishu.py`。

## 依赖与打包

- **零新增依赖**（手搭 BaseRequest 走已有 client；直链下载复用已有 httpx）。不改 pyproject/nuitka/pyinstaller。

## 用户侧前置（飞书后台，需你配）

- 应用开通并申请 scope：`contact:contact.base:readonly`（+ `contact:user.employee_id:readonly`）、`approval:approval:readonly`、`drive:drive:readonly`、以及考勤已有的 `attendance:task:readonly`。
- 通讯录权限范围设「全部成员」（否则名单不全）。
- 报销技能需要你提供对应审批流的 `approval_code`（在审批后台该审批的定义里可查）。
- 两个技能运行时需你给出：劳务费公式+表格格式 / 报销校验条件清单。

## 提交与验证

- 验证门：`uv run ruff check` + `uv run ruff format` + `uv run ty check` + `uv run pytest examples/haitun-workspace/tests/test_feishu.py -o addopts="" -q` 全绿。
- 编辑 `_feishu_impl.py` 后**必须重启 gateway**（下划线模块不热重载）。
- 提交前 `grep -niE "cli_aadf|smGBJ|M0AweXU"` 确认无凭据入库；只 push 到 `add-feishu-tools1`，不动 main。
