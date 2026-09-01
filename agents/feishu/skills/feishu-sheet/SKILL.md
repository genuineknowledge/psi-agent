---
name: feishu-sheet
description: 飞书电子表格（sheets）接口表 —— 建独立表格、改表名、列工作表、工作表增删复制、行列增删插、合并/拆分单元格、查找替换、查保护范围。用 feishu_api 按表调用。读区间、写入、套格式仍然是专用工具（裸 !A1 会静默丢数据）。表格事实问题铁律：用 feishu_sheet_find_columns 定位列 + feishu_sheet_read_grid 分块读全（has_more=false 为止），禁止用 feishu_sheet_read 做事实问答（20000 字符静默截断，会漏行）；对齐单元格用返回的 cols 数组（rows 每个 cell 与 cols 列字母一一对应），禁止手数，偏一列全盘错；判定"某天是否写过"看返回的 filled_cols 非空列清单，单元格文本里的日期数字（如 (8.24)）不是填写证据；每问必本轮重读，没有"够新"豁免。
---

# 飞书电子表格接口

## 读取铁律(表格事实类问题,违反任一条都是错误答案)

1. **每次实时读取,没有"够新"豁免**:回答任何表格事实(谁的内容、谁的 mentor、
   几行几列、数量对比),**必须在本轮调用读取工具重新读表**,答案标注来源(哪张表、
   哪列、哪几行)。表格是易变的外部数据源,读取结果只在那一刻有效——会话历史里的
   旧读取结果只能用来定位「该读哪张表、哪几列」,禁止直接作为答案依据;历史答案
   不算数,每次都要重读。**哪怕上一分钟刚读过、哪怕用户重复提问、哪怕上次结果与
   预期一致,回答前都必须本轮重新读**——「刚读过/结果没变」不是跳过理由
   (实测事故:间隔 5 分钟跳过重读,复述了 25 分钟前的错误结论)。
2. **读全再下结论**:读取结果带 `truncated` 或 `has_more=true` 时,**必须继续读**
   (用 `feishu_sheet_read_grid` 从 `next_start_row` 接着读,直到 `has_more=false`);
   **禁止用残缺数据下结论**——没读到的行不是"空",是"没读"。
3. **列定位用工具,列语义自己读**:拿列字母用 `feishu_sheet_find_columns`,**禁止自己数表头**
   (数错列是已实测的高频错误:行号横跳、把日期列读成内容列)。工具回的是
   `{"col": "C", "header": "导师"}` —— 列字母是算出来的(26 进制、区间不从 A 起时带偏移),
   `header` 是**原文照抄**;除日期列带 `kind: "date"` 外**不回 kind**,也就是说
   「这列是什么」由你读表头文字判断,工具不替你判。
   **对齐单元格用返回里的 `cols` 数组**:`rows` 每个 cell 与 `cols` 里的列字母一一
   对应,直接索引对齐;禁止假设第一列是 A 或从 A 开始数——偏一列就全盘错
   (实测事故:读 A37:S37 却按 B 起数,8.17 的内容被当成 8.14)。
   **取某列的内容**:单行读取的返回带 `cells`(列字母 → 内容 的映射,代码生成),
   **按列字母键取值**;禁止从 `rows` 数组里数第几个元素取内容——超长文本连排时
   数错一格即偏一格(实测事故:报 R 列内容读成了 Q 列的,日期定位对了、内容偏了)。
   多行读取不附 `cells`,要取内容就改单行/单格读取再取。
4. **负责人和 mentor 是两个人,别弄反**:负责人是**干这件事、领这条 todo 的人**,
   mentor 是**带他的人**,两列各写一个人,一行读出来是"负责人 → 他的 mentor"这个方向。
   已实测事故:答"谁的 mentor 是谁"时把两列对调,把被带的人报成了带教。三条防线:
   - **认列靠表头原文,不靠列的位置**。mentor 列的表头未必写 `mentor`,中文表里常写
     **导师 / 带教 / 带教人 / 师父**;负责人列常写 **负责人 / 姓名 / 名字 / owner / 责任人**。
     工具不替你判这些词(它只回原文),所以由你对着表头念一遍再定。
   - **表头同时像两个角色时,先看数据再定**。`带教负责人(mentor)` 这种表头两边都沾,
     光看词判不了;从两列各读几行,对照已知的人际关系(谁是新人、谁带人)再下结论。
   - **答案里把方向说全**:"X 的 mentor 是 Y(X 在 B 列负责人,Y 在 C 列导师)",
     带上列字母和表头原文;方向写出来了才能被用户当场纠正。
5. **判定"某天是否写过"看 `filled_cols`,不看单元格文本**:`read_grid` 返回里每行带
   `filled_cols`(该行非空列的字母清单,代码直接给出,不用数)。单元格**文本里**出现的
   日期(如 todo 内容里的 "(8.24)")只是填写内容的一部分,**不是**该日期列的填写证据——
   实测事故:8.21 格内容提到 (8.24),被当成 8.24 列写了,漏写的人被报成没漏。

用 `feishu_api` 按下表调用。表里每一行都对应一个真实接口；`rules` 块是同一份知识的可执行副本，
参数不合规会在**发请求之前**被拦下来。

本域**结构操作**（建表、加行列、合并、查找替换）进了表格，**读写单元格内容**留在专用工具里
（`feishu_sheet_read` / `_write` / `_append` / `_format`）。分界线是那条老坑：区间必须写全
`"<sheet_id>!A1:B2"`，写成裸 `"<sheet_id>!A1"` 时飞书**返回成功、`updatedRange` 是空的、一个字都没写进去**。
这属于静默失败那一类，所以不表格化。

两个 token 先分清：

- `spreadsheet_token` 是**整个表格文件**的 token，网址 `feishu.cn/sheets/<token>` 里那段。
- `sheet_id` 是**一张工作表**（底部的 tab）的 id，**网址里没有**，只能打「列工作表」问出来。
  所有区间、所有行列操作都要它。

## 建表与改名

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 建一个独立电子表格 | POST | `/open-apis/sheets/v3/spreadsheets` | `title`、`folder_token` |
| 改表格标题 | PATCH | `/open-apis/sheets/v3/spreadsheets/:spreadsheet_token` | `title` |
| 列工作表（拿 `sheet_id`） | GET | `/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query` | 无 |

建表**只能建空表**，不支持带内容创建 —— 先建再用 `feishu_sheet_write` 写。想套模板就拿模板的
`spreadsheet_token` 走 `feishu-drive` 的复制文件接口。要建在**知识库里**的表格不走这个端点，
走 wiki 建节点并指定 sheet 类型。

改标题传空串会变成「未命名表格」，不是「保持原名」。

## 工作表（tab）增删复制

| 要做的事 | method | endpoint | body 里放什么 |
|---|---|---|---|
| 加/删/复制工作表 | POST | `/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/sheets_batch_update` | `requests[]`，每项一个操作 |

三种操作写在同一个 `requests` 数组里，可以一次多个：

```json
{"requests": [
  {"addSheet": {"properties": {"title": "新工作表", "index": 1}}},
  {"copySheet": {"source": {"sheetId": "2jm6f7"}, "destination": {"title": "副本"}}},
  {"deleteSheet": {"sheetId": "l8Gdub"}}
]}
```

`addSheet` 不给 `index` 会插到最前面（index 0）而不是最后。`copySheet` 落在源表后面，
不给 `destination.title` 时飞书自己起名叫「Sheet1(副本_0)」。响应在 `data.replies[]` 里按请求顺序对应。

## 行列增删插

三个端点，**索引基准互不相同**，这是本域最容易错的地方：

| 要做的事 | method | endpoint | 索引怎么算 |
|---|---|---|---|
| 在**末尾**加行列 | POST | `/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/dimension_range` | 不用索引，只给 `length` |
| 在**指定位置**插行列 | POST | `/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/insert_dimension_range` | **0-based，左闭右开** |
| 删行列 | DELETE | `/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/dimension_range` | **1-based，两端都闭** |

三个都把参数包在一个 `dimension` 对象里（`sheetId` + `majorDimension` 取 `ROWS` / `COLUMNS`）：

```json
{"dimension": {"sheetId": "2jm6f6", "majorDimension": "ROWS", "startIndex": 3, "endIndex": 7}}
```

同一份 `{"startIndex": 3, "endIndex": 7}`：
**插入**时是 0-based 半开，得到 **4 行**（第 4-7 行位置）；**删除**时是 1-based 闭区间，删掉 **5 行**（第 3-7 行）。
照抄另一个端点的索引会多删一行或插错位置，而两边都返回成功。

`length` / 区间单次上限 **5000** 行或列。`insert_dimension_range` 的 `inheritStyle` 取 `BEFORE`
（继承起始位置那格的样式）或 `AFTER`，不传就是无格式。删除**删不完**：一张工作表至少留 1 行 1 列。

## 合并与拆分单元格

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 合并 | POST | `/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/merge_cells` | `range`、`mergeType` |
| 拆分 | POST | `/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/unmerge_cells` | `range` |

`mergeType` 三种：`MERGE_ALL` 整片并成一格、`MERGE_ROWS` 每行内各自并、`MERGE_COLUMNS` 每列内各自并。
单次上限 5000 行 100 列。`range` 照旧是 `"<sheet_id>!F11:G12"`。

## 查找与替换

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 查找 | POST | `/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/:sheet_id/find` | `find_condition`、`find` |
| 替换 | POST | `/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/:sheet_id/replace` | 同上 + `replacement` |

`find_condition` 里 `range` 必填，其余四个开关都默认 false：`match_case`、`match_entire_cell`、
`search_by_regex`（`find` 当正则用）、`include_formulas`（true 只搜公式，false 只搜单元格内容）。
返回 `find_result.matched_cells[]` / `matched_formula_cells[]` / `rows_count`。

`range` **不能超出实际有数据的区域**，超了报 `1310202 Wrong Range` —— 200 行的表要 1-201 行就失败。
所以先用「列工作表」看 `grid_properties`，但注意 `row_count` 是表格上限不是数据行数。
替换单次 5000 格、20 次/分。

## 保护范围

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 查保护范围详情 | GET | `/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/protected_range_batch_get` | `protectIds`、`memberType` |

`protectIds` 是**逗号拼接**的字符串，一次**最多 5 个**。返回里可编辑成员在
`editors.users[]`（嵌套的，不是平铺的 `users`），每项 `{memberType, memberId}`；
`dimension` 为空表示保护了整张工作表；`dimension.startIndex/endIndex` 是 **1-based 两端都闭**。

**加保护范围的写入端点本文档未核实**（飞书文档页是 JS 渲染的，抓不到正文）。需要时先查官方文档，
或第一次调用照飞书的报错提示改 —— 别照着上面这个读端点的字段名反推写端点的 body。

## 透视表和图表：没有创建接口

飞书开放平台**没有**「创建透视表」或「创建图表」的端点。要在文档里放数据图表，走
`feishu_chart` / `feishu_chart_figure`（matplotlib 渲成 PNG → 插进 docx 图片块）。
别去猜一个 `/pivot_tables` 或 `/charts` 的路径反复试错，那不是权限问题，是接口不存在。

## 条件格式：本文档未核实

`condition_formats` 那批端点没能从官方文档核实路径和字段名，所以不写进表格。要用先查官方文档。

## 读写单元格为什么还是工具

这四个端点各自有专用工具，`feishu_api` 打它们会附一条指路的 warning（不是硬拒 ——
通用路径对这一批一直是软劝，改成硬拒会拦掉本来能跑通的手搓调用）：

| 要做的事 | method | endpoint | 用这个 |
|---|---|---|---|
| 读一个区间 | GET | `/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values/:range` | `feishu_sheet_read` |
| 写一个区间 | **PUT** | `/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values` | `feishu_sheet_write` |
| 追加到末尾 | POST | `/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values_append` | `feishu_sheet_append` |
| 套单元格样式 | PUT | `/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/style` | `feishu_sheet_format` |

- `feishu_sheet_read` 要把 @人 和富文本**压平成可见文字**，不然读回来是一堆 `mention` / `text_run` 结构。
- `feishu_sheet_write` / `_append` 校验网格坐标：裸 `"<sheet_id>!A1"` 飞书**返回成功却什么都没写**，
  这是要在发请求前拦掉的那类失败。注意写入是 **PUT** 不是 POST，按 POST 打会 404 得莫名其妙。
- `feishu_sheet_format` 套样式，`style_json` 的形状比一行表格装得下的多。

以上都要 `spreadsheet_token`；wiki 里的表格先用 `feishu_api` 打 `wiki/v2/spaces/get_node`
换出 `obj_token` 再用。

```rules
- endpoint: POST /open-apis/sheets/v3/spreadsheets
  token: tenant_then_user
  fields:
    # A length cap on a *string* has to be a pattern: `max` coerces with float() and
    # gives up on anything non-numeric, so `max: 255` on a title checks nothing.
    # [\s\S] rather than . because . excludes newlines, and a title that happens to
    # contain one should be refused for its length, not for the newline.
    title: {pattern: '^[\s\S]{0,255}$', on_fail: "title 最长 255 字符"}
  pitfalls:
    - 只能建空表, 不支持带内容创建;要内容先建后写(feishu_sheet_write)。
    - 想套模板就拿模板的 spreadsheet_token 走 drive 的复制文件接口, 不是这里。
    - 要建在知识库里的表格走 wiki 建节点并指定 sheet 类型, 不走这个端点。
    - 返回在 data.spreadsheet 里: spreadsheet_token / url / title / folder_token。

- endpoint: PATCH /open-apis/sheets/v3/spreadsheets/:spreadsheet_token
  token: tenant_then_user
  pitfalls:
    - body 只有 title;传空串会变成"未命名表格", 不是保持原名。
    - 改的是整个表格文件的名字;改一张工作表的名字要走 sheets_batch_update 的 updateSheet。

- endpoint: GET /open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query
  token: tenant_then_user
  pitfalls:
    - 返回的 sheet_id 才是区间前缀, 表格网址里没有它;区间一律写成 "<sheet_id>!A1:B2"。
    - 行列数在 grid_properties.row_count / column_count 里, 不在 sheets[] 的顶层。
    - row_count 是表格的上限而不是有数据的行数, 拿它当数据范围会读回一大片空行。
    - wiki 里的表格要先用 GET /open-apis/wiki/v2/spaces/get_node 换 obj_token, 别拿 node token 打这里。

- endpoint: POST /open-apis/sheets/v2/spreadsheets/:spreadsheet_token/sheets_batch_update
  token: tenant_then_user
  required: [requests]
  fields:
    requests:
      min_items: 1
      on_fail: "requests 至少一项;空数组飞书返回成功却一个工作表都不动"
  pitfalls:
    - 每项一个操作: addSheet.properties.title / copySheet.source.sheetId / deleteSheet.sheetId。
    - addSheet 不给 index 会插到最前面(index 0)而不是最后。
    - copySheet 落在源表后面;不给 destination.title 时飞书自己起名"Sheet1(副本_0)"。
    - 删工作表连同它的数据一起没了, 且这个端点不进回收站 —— 删之前先确认。
    - 响应在 data.replies[] 里按请求顺序对应。

- endpoint: POST /open-apis/sheets/v2/spreadsheets/:spreadsheet_token/dimension_range
  token: tenant_then_user
  required: [dimension]
  pitfalls:
    - 这个端点只往末尾加, 要在中间插用 insert_dimension_range。
    - dimension 里是 sheetId / majorDimension(ROWS|COLUMNS) / length, length 上限 5000。
    - 返回 addCount 是真加了几行, 跟 length 不等时说明撞了表格上限。

- endpoint: POST /open-apis/sheets/v2/spreadsheets/:spreadsheet_token/insert_dimension_range
  token: tenant_then_user
  required: [dimension]
  pitfalls:
    - 'startIndex/endIndex 是 0-based 左闭右开: {3, 7} 插入 4 行(第 4-7 行位置)。'
    - 跟 DELETE dimension_range 的索引基准正好相反(那边是 1-based 两端都闭), 照抄会插错位置。
    - inheritStyle 取 BEFORE(继承起始位置那格样式)或 AFTER;不传就是无格式。
    - 单次上限 5000 行或列。

- endpoint: DELETE /open-apis/sheets/v2/spreadsheets/:spreadsheet_token/dimension_range
  token: tenant_then_user
  required: [dimension]
  pitfalls:
    - 'startIndex/endIndex 是 1-based 两端都闭: {3, 7} 删掉 5 行(第 3-7 行)。'
    - 跟 insert_dimension_range 的索引基准正好相反(那边是 0-based 半开), 照抄会多删一行。
    - 删掉的数据不进回收站, 没有撤销;删之前先读一遍要删的区间确认。
    - 一张工作表至少留 1 行 1 列, 删不完;单次上限 5000。
    - 返回 delCount 是真删了几行。

- endpoint: POST /open-apis/sheets/v2/spreadsheets/:spreadsheet_token/merge_cells
  token: tenant_then_user
  required: [range, mergeType]
  fields:
    mergeType:
      choices: [MERGE_ALL, MERGE_ROWS, MERGE_COLUMNS]
      on_fail: "mergeType 只认 MERGE_ALL(整片并一格) / MERGE_ROWS(每行内并) / MERGE_COLUMNS(每列内并)"
  pitfalls:
    - range 写成 "<sheet_id>!F11:G12";sheet_id 从列工作表接口拿, 网址里没有。
    - 单次上限 5000 行 100 列。
    - 合并会只保留左上角那格的内容, 其余格的数据丢掉。

- endpoint: POST /open-apis/sheets/v2/spreadsheets/:spreadsheet_token/unmerge_cells
  token: tenant_then_user
  required: [range]
  pitfalls:
    - 拆开不会把合并时丢掉的数据找回来, 只有左上角那格有内容。
    - 单次上限 5000 行 100 列。

- endpoint: POST /open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/:sheet_id/find
  token: tenant_then_user
  required: [find_condition, find]
  pitfalls:
    - find_condition 里 range 必填, 其余四个开关默认 false: match_case / match_entire_cell / search_by_regex / include_formulas。
    - range 不能超出实际有数据的区域, 超了报 1310202 Wrong Range(200 行的表要 1-201 行就失败)。
    - include_formulas=true 只搜公式, false 只搜单元格内容 —— 不是"也搜"。
    - 返回 find_result.matched_cells[] / matched_formula_cells[] / rows_count。

- endpoint: POST /open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/:sheet_id/replace
  token: tenant_then_user
  required: [find_condition, find, replacement]
  pitfalls:
    - 替换是就地改写, 没有撤销;先用 find 看清命中哪些格再替。
    - search_by_regex=true 时 find 是正则, 一不小心会命中远超预期的格。
    - 单次 5000 格、20 次/分。

- endpoint: GET /open-apis/sheets/v2/spreadsheets/:spreadsheet_token/protected_range_batch_get
  token: tenant_then_user
  required: [query.protectIds]
  pitfalls:
    - protectIds 是逗号拼接的字符串, 一次最多 5 个。
    - 可编辑成员在 editors.users[] 里(嵌套的), 不是平铺的 users。
    - dimension 为空表示保护了整张工作表;它的 startIndex/endIndex 是 1-based 两端都闭。
    - 加保护范围的写入端点本技能未核实, 别照这个读端点的字段名反推 body。

- endpoint: PUT /open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values
  prefer_tool: feishu_sheet_write
  why: >
    裸 "<sheet_id>!A1" 这种不写终点的区间, 飞书返回成功、updatedRange 是空的、一个字都没写进去。
    工具校验网格坐标。注意写入是 PUT 而不是 POST。
  pitfalls:
    - 区间必须写全 "<sheet_id>!A1:C3";只写起点会静默丢数据。

- endpoint: POST /open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values_append
  prefer_tool: feishu_sheet_append
  why: 同 values, 区间不写全会静默丢数据。
  pitfalls:
    - 追加是 POST values_append, 覆盖写是 PUT values —— 两个端点两个方法, 别混。

- endpoint: PUT /open-apis/sheets/v2/spreadsheets/:spreadsheet_token/style
  prefer_tool: feishu_sheet_format
  why: style_json 的形状比一行表格装得下的多, 且同样吃那条区间坐标的坑。

- endpoint: GET /open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values/:range
  prefer_tool: feishu_sheet_read / feishu_sheet_read_grid
  hard: true
  why: >
    读回来的格子里 @人 和富文本是嵌套结构, 工具把它们压平成可见文字并内嵌
    行号与列字母标签; 直接打这个端点拿到的是一堆 mention/text_run 对象,
    原始 JSON 超长被截断后手动对齐已实测错位(没写的人被报成写了)。
```

授权与权限：需要 `drive:drive` 或 `sheets:spreadsheet` scope（建表也接受
`sheets:spreadsheet:create`）。表格不是机器人创建的时候，几乎一定要走用户身份 ——
报 403 / `1310213` 时先确认 `user_key` 传了没有，而不是去改参数。
