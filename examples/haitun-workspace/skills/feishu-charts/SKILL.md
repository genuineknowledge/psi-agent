---
name: feishu-charts
description: "Putting real data charts into a Feishu/Lark cloud document — pie, donut, funnel, line, area, stacked area, column, bar, grouped/stacked column, waterfall, histogram, box, scatter, bubble, heatmap, radar, Pareto, combo, Gantt, progress, plus multi-panel combined figures with (a)/(b)/(c) sub-plots under one numbered caption. Use whenever the user asks for 图表/饼图/折线图/柱状图/趋势图/占比图/热力图/甘特图 in a 飞书文档, or asks to visualise data, add a chart to a report, turn a table into a chart, or combine several charts into one figure. Covers which chart fits which question, how to annotate it, and 图N/表N caption numbering."
category: output
---

# Feishu 文档数据图表

在飞书云文档里放**真正的数据图表**（饼图/折线图/柱状图……）。工具会渲染 PNG 并作为
飞书原生图片块插入文档，同时把图片留在本地供 Word/PPT 复用。

回复用中文，除非用户明显在用其他语言。

## 先选对图，再画图

图选错了，画得再漂亮也是误导。按**用户问的问题**选，不要按数据长什么样选：

| 用户在问什么 | 用哪个工具 | 关键前提 |
|---|---|---|
| 各部分占整体多少 | `feishu_chart_pie` | 2-6 类，且合计有意义 |
| 占比，且总量本身重要 | `feishu_chart_donut` | 同上，环心显示合计 |
| 每一环节流失了多少 | `feishu_chart_funnel` | 顺序有意义、逐级递减 |
| 随时间怎么变 | `feishu_chart_line` | x 轴有序；2-4 条线 |
| 累积量/水位随时间怎么变 | `feishu_chart_area` | 1-2 条，面积有含义 |
| 构成随时间怎么变 | `feishu_chart_stacked_area` | 非负；`percent=true` 看结构 |
| 各类别谁高谁低 | `feishu_chart_column` | ≤8 类且名称短 |
| 排名（类别多/名称长） | `feishu_chart_bar` | 横向，默认降序 |
| 每类里几个指标对比 | `feishu_chart_grouped_column` | 2-4 个系列 |
| 每类的总量**和**内部构成 | `feishu_chart_stacked_column` | 非负；`percent=true` 比结构 |
| 从期初怎么变成期末 | `feishu_chart_waterfall` | 传**增减量**，不是余额 |
| 某个量的分布形态 | `feishu_chart_histogram` | 传原始观测值 |
| 几组的分布/稳定性对比 | `feishu_chart_box` | 每组 ≥2 个观测 |
| 两个量有没有关系 | `feishu_chart_scatter` | 轴标签必须带单位 |
| 三个量一起看 | `feishu_chart_bubble` | ≤12 个气泡 |
| 两个维度交叉的强弱分布 | `feishu_chart_heatmap` | 行×列网格 |
| 多维能力画像 | `feishu_chart_radar` | 3-8 轴且同量纲 |
| 少数原因占了大头（80/20） | `feishu_chart_pareto` | 归因、定优先级 |
| 量（绝对值）+ 率（百分比） | `feishu_chart_combo` | 双轴，单位不同 |
| 排期/计划 | `feishu_chart_gantt` | 传真实日期 |
| 目标完成情况 | `feishu_chart_progress` | 有明确 target |

### 最常见的四个选错

- **分类超过 6 个还用饼图** → 小扇区挤成一团。用 `feishu_chart_bar`（横向、降序）。
  工具会自动把第 7 名以后折叠成「其他」并在返回里告知，但那是补救，不是本意。
- **无序类别用折线图** → 折线暗示「点之间是连续的」。部门、地区、产品之间没有连续性，
  用柱状图。
- **想精确比较各构成项却用堆叠柱** → 只有最底层那段是同一基线，其余段落眼睛读不准。
  要比较具体分项用 `feishu_chart_grouped_column`。
- **把百分比和大额绝对值放同一根轴** → 毛利率被压成一条贴地的直线。用
  `feishu_chart_combo`。

## 让图有用，而不只是有图

- **标题写结论，不写维度。** ✗「各月营收」 ✓「营收连续三个月上行，3 月回落」。
  标题是这张图唯一保证会被读的一句话。
- **一定带单位。** `unit="万元"` / `"人"` / `"h"`。没单位的数字读者只能猜。
- **标数据来源。** `source="财务台账 2026-07"`。图会在页脚注明；无出处的图在正式文档里
  站不住。
- **配图注，但别自己写编号。** `caption="各区域目标完成率"`——工具会读文档里已有的「图 N」
  往下续号，写成 `图 3：各区域目标完成率` 插在图下方。你自己写 `caption="图1：…"` 那个
  「图1」会被丢掉换成真实序号（因为你数不准文档里已经有几张图，序号对不上就是这么来的）。
  返回里的 `caption_number` 是实际发到的号，正文里说「如图 3 所示」用它，别凭记忆写。
- **突出要讨论的那一项。** `highlight=1`（饼图拉出该扇区；柱状图把其余置灰）。文档正文
  在说哪一项，图上就该指向哪一项。
- **不要把同一份数据画两遍。** 已有表格就别再配一张一模一样的柱状图；图要补充表格
  读不出的东西（趋势、分布、集中度）。

## 几个图拼成一张（学术论文那种）

同一个问题需要几个视角一起看时，用 `feishu_chart_figure` 把 2-6 张图**拼成一张图片**，
每个子图标 `(a)` `(b)` `(c)`，下面一条统一的编号图注列出各子图名字：

```
feishu_chart_figure(
  panels_json='''[
    {"chart":"line","title":"营收趋势","labels":["1月","2月","3月"],
     "series":{"营收":[120,145,138]},"y_label":"万元"},
    {"chart":"pie","title":"渠道结构","labels":["直销","渠道","线上"],"values":[62,24,14]},
    {"chart":"bar","title":"区域排名","labels":["华东","华北","华南"],"values":[118,92,76]}
  ]''',
  layout="grid",                    # horizontal 横排 / vertical 竖排 / grid 网格
  figure_title="上半年经营概览",
  caption="上半年经营三视图",        # 会写成「图 3：上半年经营三视图」+「(a) 营收趋势；(b) 渠道结构；(c) 区域排名」
  source="财务台账 2026-07",
  document_id="<docx document_id>",
  user_key="<sender open_id>",
)
```

- **每个 panel 的字段就是对应单图工具的参数去掉 `_json`**：`labels` `values` `series`
  `points` `tasks` `items`……21 种 `chart` 值都能当 panel（含 radar/heatmap/gantt/combo）。
- **什么时候该拼**：几张图回答的是**同一个**问题（同一主题的不同侧面、同一指标的本期 vs 上期、
  一个结论需要的证据链）。拼起来的好处是它们共用一条图注、永远不会被别的内容插开。
- **什么时候不该拼**：回答的是不同问题。硬拼进一张图，那条图注没法同时描述两件事，
  读者也不知道该把哪个当重点——这种就各自单图工具、各自编号。
- **横排 vs 竖排 vs 网格**：横排适合互相比较（并排看差异），竖排适合有先后顺序的（漏斗→转化），
  4 个以上用 `grid`（横排 6 个会宽到 48 英寸，飞书按原始像素显示会缩成一排小图，工具直接报错拦住）。
- 上限 6 个。再多每个子图就小到读不清了，拆成两张图。

## 典型调用

数据先在文档里，图跟在结论后面：

```
feishu_chart_pareto(
  labels_json='["登录失败","支付超时","页面卡顿","推送延迟","样式错乱"]',
  values_json='[120,85,42,25,12]',
  title="前三类缺陷占八成工单",
  y_label="工单数",
  document_id="<docx document_id>",
  caption="图2：缺陷类型帕累托分析",
  source="工单系统 2026-07",
  user_key="<sender open_id>",
)
```

双轴组合图（量 + 率）：

```
feishu_chart_combo(
  labels_json='["1月","2月","3月","4月"]',
  bar_series_json='{"营收":[120,145,138,170]}',
  line_series_json='{"毛利率":[32,35,33,38]}',
  title="营收上行，毛利率同步改善",
  y_label="营收（万元）", y2_label="毛利率",
  unit="万", line_percent=True,
  document_id="<docx document_id>",
)
```

## 用法要点

- `document_id` 传 docx 的 document_id，或知识库节点的 `obj_token`。
- `document_id` **留空**＝只生成 PNG 并返回 `image_path`，用于嵌 Word/PPT，或用
  `[SEND:绝对路径]` 直接发给用户。
- `user_key` 传发送者 open_id（来自 `<feishu_context>`）。文档归属用户、机器人不是协作者时
  必须传，否则写入会被拒。
- 多系列参数（`series_json` 等）用 `{"名称":[数值,…]}`，每个系列的长度必须等于 labels 的
  长度，否则工具直接报错而不是画一张错的图。
- 数值可以是 `1234`、`"1,234"`、`"85%"`、`"￥1200"`，工具会清洗。`"85%"` 记作 85，不是 0.85。
- 返回里带 `warning` 说明宿主机没装中文字体、中文会显示成方框；此时先告知用户，别当作画好了。

## 图表画不了的东西

`feishu_chart_*` 只做**数据图表**。流程图、泳道图不是数据图表，用现成的
`feishu_doc_append_flowchart` / `feishu_doc_append_swimlane`（飞书 API 画不了真流程图，
它们渲染成原生表格）。需要表格就用 `feishu_doc_append_table`。

这三个也都收 `caption`，编号走**独立的「表 N」序列**（表 1、表 2 和图 1、图 2 互不干扰），
而且按学术体例**写在表格上方**（图注在下、表题在上）。同样只写内容不写「表N：」。
