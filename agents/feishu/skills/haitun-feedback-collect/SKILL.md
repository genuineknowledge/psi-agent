---
name: haitun-feedback-collect
category: productivity
description: "一层：识别用户在讨论 HaiTun/海豚 的 bug、产品意见或爽点；先口头确认类型并问是否落盘，用户答应后再写入飞书《反馈表》。现网是 wiki 电子表格 2×3 分表（端×类型），走 feishu_sheet_append；多维表路径预留。LOAD when 用户说「海豚 bug」「报个 bug」「有个意见」「这个好用/好爽」、甩截图吐槽产品，或明确要记进反馈表。NOT for 派活给人(work-assignment-delegation)、一般功能问答、二层回执。Config: config/haitun_feedback.yaml。"
agent_editable: true
---

# HaiTun 反馈收集（确认后再写）

把「随口吐槽 / 报 bug / 夸一句」在**用户明确同意落盘之后**写成反馈表一行。  
本 skill **只做一层**：识别 →（必要时澄清）→ **问是否落盘** → 用户答应 → 写入 → 短确认。  
**不做**状态流转后私聊提出人（二层另文）。

配置：`config/haitun_feedback.yaml`。可用环境变量
`HAITUN_FEEDBACK_APP_TOKEN` / `HAITUN_FEEDBACK_TABLE_ID` 覆盖多维表坐标（预留）。

## When to use

满足**任一**即 LOAD（先对话确认，**不要**一上来就写表）：

- 显式口令：`海豚 bug：…` / `报个 bug` / `记一条意见` / `记个爽点` / `写进反馈表`
- 内容像在评 **HaiTun / 海豚一号 / 飞书机器人 / 桌面端安装包** 的缺陷、体验或亮点（含截图 + 吐槽）
- 用户回答「要落盘 / 帮我记一下 / 好的写吧」且本会话刚讨论过反馈内容

## When not to use

- **派活 / 交办**（「让张三修这个」）→ `work-assignment-delegation`
- 纯产品**使用教学**、查文档、写方案，没有「这是问题 / 这是意见 / 这个爽」味道
- 外部客户不可能填公司内表时：本表只收**内部试用者 / 驻场对接人**；对外仍走代录
- **二层回执**（状态变了通知原提出人）→ 未接线前不要用本 skill 冒充已闭环

## 对话节奏（刻意为之：先确认，再落盘）

**禁止**在用户未明确同意「落盘 / 记一下 / 写进表」之前调用写入工具。

### A. 拿不准是不是反馈

> 您是在反馈 **bug / 意见 / 爽点** 吗？需要我帮您落盘到反馈表吗？

用户说「不是 / 不用」→ 正常继续，**不写表**。

### B. 能认出类型

先口头对齐，**仍不写表**，再问是否落盘。用户肯定 → 进入写入。

### C. 本轮已带落盘口令

「帮我记进反馈表：……」→ 视为已同意；仍要在回复里点明类型，再写入。  
同会话连续多条、同一缺陷 → **合并成一行**，勿刷多行。

## 现网写入路径（2×3 电子表格 · 主路径）

**刻意为之：** 线上《HaiTun Agent爽感/意见/bug反馈表》是 wiki 托管的**电子表格**（`obj_type=sheet`），不是多维表。  
形态 = **端(B/C) × 类型(bug/意见/爽点)** 共 **6 张分表**；分表本身不写「端/类型」列，靠选对 sheet 表达。

| 步骤 | 工具 |
|------|------|
| 读配置 | `read` → `config/haitun_feedback.yaml`（相对 **agent** 包根） |
| 取下一编号 | `feishu_sheet_read_grid`（看该分表已有最大「编号」） |
| 写一行 | `feishu_sheet_append` |
| 提出者 | `<feishu_context>` 的 `sender_name` / `sender_open_id` |

### 0. 配置闸门

用户**已同意落盘**后再读配置：

1. **`sheet_layout.spreadsheet_token` 非空** → 走本节电子表格路径（**即使** `app_token`/`table_id` 为空）。
2. 否则若 `app_token`+`table_id`（或对应环境变量）齐全 → 走下方「多维表预留」。
3. 两套都空 → **不要空写**。回复：反馈表坐标未配置，请发 wiki/表格链接或填 `sheet_layout` / 多维表坐标。可先展示草稿。

**禁止**把电子表格的 `spreadsheet_token` 填进 `app_token`/`table_id` —— 两套接口不通用，填错只会报错。

### 1. 选分表（钉死 sheet_id）

用配置 `sheet_layout.sheets` 的 **key**，**不要**运行时按分表标题模糊匹配（改名会串表）：

| 端 \ 类型 | bug | 意见 | 爽点/爽感 |
|-----------|-----|------|-----------|
| B | `B_bug` | `B_opinion` | `B_delight` |
| C | `C_bug` | `C_opinion` | `C_delight` |

端/类型判定：

| 信号 | 端 |
|------|----|
| 飞书机器人、群聊、ToB 网页、`feishu` 会话 | B |
| 桌面安装包、本机控制台、spa、ToC | C |
| 说不清 | 先按 **B** 写入对应类型分表，描述首行标 `[端未确认]`（**不要**为分类卡住落盘） |

| 信号 | 类型 key |
|------|----------|
| 坏了、报错、不符合预期、崩溃 | bug → `*_bug` |
| 希望改成…、建议、慢/体验差但非明确坏掉 | 意见 → `*_opinion` |
| 好用、爽、省事 | 爽点 → `*_delight`（表内 B 分表标题是「B端爽感」，C 是「C端爽点」，key 仍是 `*_delight`） |
| 说不清 | 意见分表 + 描述首行 `[待分类]` |

### 2. 组一行（列序固定）

分表列（与现网表头逐字一致）：

`编号` | `详细描述或截图` | `提出者` | `日期` | `状态` | `附件`

| 列 | 怎么填 |
|----|--------|
| 编号 | 读该分表已有最大数字编号 + 1；空表从 `1` |
| 详细描述或截图 | 用户原意整理，**不编造**复现步骤；有图未上传则文内注明 |
| 提出者 | `@显示名`（与现网样例一致，如 `@董修奇`） |
| 日期 | `YY.M.D`（如 `26.9.10`），见配置 `date_format` |
| 状态 | bug/意见 → 配置 `default_status`（默认「待确认」）；爽点可留空字符串 |
| 附件 | 通常 `""`；有 `file_token` 再填（本路径优先保证文字行落盘） |

**open_id 不进电子表格列**（表无此列）。需要二层回执时另存会话/记忆，勿硬塞进「详细描述」除非用户要求。

### 3. 调用

```text
# sheet_id 来自 sheet_layout.sheets[<B|C>_<bug|opinion|delight>].sheet_id
feishu_sheet_append(
  token="<sheet_layout.spreadsheet_token>",
  range="<sheet_id>!A1:F1",
  values_json='[[ "<编号>", "<详细描述或截图>", "@<提出者>", "<日期>", "<状态>", "" ]]',
  user_key="<sender_open_id>",
  identity="<sheet_layout.identity 或 user>"
)
```

**权限（现网实测）**：机器人对该表常 `91403 Forbidden`（读得到、写不了）。  
配置默认 `sheet_layout.identity: user` —— 以提出者身份写；若返回需授权，走 `feishu_auth` / 授权卡，**勿谎称已写入**。  
长期也可把机器人加为表格协作者，再改回 `identity: bot`。

### 4. 落盘后回用户（短）

- 「已记入 | 端 B | 类型 意见」+ 可附配置里的 `url`
- **不要**复述整段、不要承诺「已派人修」
- 失败：说清原因 + 展示草稿，**勿谎称已写入**

## 多维表预留（app_token + table_id）

仅当 `sheet_layout` 不可用且多维坐标齐全时使用：`feishu_bitable_create_records`，列名走配置 `fields.*`（一张表 + 「端」「类型」两列）。日期列若为日期类型用毫秒时间戳。详见 `feishu-bitable`。

## 边界（刻意为之）

| 做 | 不做 |
|----|------|
| 先对齐类型并问「要落盘吗」 | 未同意就 append / create_records |
| 按配置 sheet_id 选分表 | 按分表标题模糊匹配 |
| 同意后尽快写一行 | 用一堆字段逼用户填完再写 |
| 91403 时说明权限并请授权 | 把 spreadsheet_token 塞进 app_token |

## 相关

- 配置：`config/haitun_feedback.yaml`（`sheet_layout` 为主）
- 电子表格：`feishu-sheet` / `feishu_sheet_append`
- 多维表：`feishu-bitable`（预留）
- 二层（状态变更 → 私聊提出人）：尚未落 skill
