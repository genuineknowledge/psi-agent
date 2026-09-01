---
name: card-dsl
description: "卡片通用元素 DSL —— 用 XML 声明一张飞书交互卡片(标题/信息行/打分/评语/按钮),由渲染引擎编译成飞书卡片 2.0 JSON。Use when creating or rebuilding a Feishu interactive card (review card, approval card, report card, ...) from a DSL declaration instead of hand-writing Feishu card JSON. Companion spec: card.xsd (machine-readable vocabulary)."
category: productivity
---

# 卡片通用元素 DSL

卡片由 XML 声明,渲染引擎(`feishu_card_render` 工具)编译成飞书卡片 2.0 JSON。
业务只写"卡上有什么",飞书协议限制(按钮单次消费、input 值回传、组件限制)
全部由引擎消化——XML 里不出现任何飞书 JSON 概念。

## 词汇表(第一版,按场景组装、按需增长)

| 元素 | 属性 | 说明 |
|---|---|---|
| `card` | `title`(必填)/ `template` | 根容器:标题与卡头配色 |
| `info` | `label` / `value`(必填) | 信息行,如 负责人/截止日期 |
| `score` | `min`(默认1)/ `max`(默认5)/ `rounds`(默认20)/ `bind-record` / `selected` | 评分按钮组,点选即回调,可多轮改分 |
| `comment` | `placeholder` / `bind-record` | 评语输入框(引擎自动配「确认」按钮,点确认才回传文字) |
| `action-row` | — | 按钮行容器 |
| `button` | `text` / `type` / `action`(必填) | 动作按钮 |
| `list` | `shape`(默认circle) | 待办列表(todo 卡):多行逐条勾选,子元素 `row` |
| `row` | `title`(必填)/ `task-guid` / `detail` / `shape` / `bind-record` / `done` | 一行待办;`done="true"` 的行发卡时即只读(无按钮) |

`template` 取值与配色:`blue`(普通)/ `green`(成功、完成态)/ `red`(告警、逾期)/ `grey`(归档、只读)。
`button type` 语义色(业务只写语义,不写颜色):`accept` 通过→蓝(绿是设计意图,飞书按钮暂无绿色);
`reject`/`danger` 驳回、危险→红;`default` 普通→灰;`primary` 主操作→蓝。

## 动作(action)规则

- `score` 默认动作 `review_score`(→ 工具 `feishu_review_card_select`);
  `comment` 默认动作 `review_input`(→ 工具 `feishu_review_input`);
  内置映射还有 `review_reject`(→ 工具 `feishu_review_reject`)。
  其他动作用渲染工具的 `handler_overrides_json` 补充映射。
- 飞书按钮**点一次永久消费**:引擎为每个动作预注册 20 轮(`{action}_r0`…`_r19`),
  每次重建卡片**轮次 +1**(`round_` 参数),卡片才能反复操作。
- 回调 value 自动携带:`bind-record`(→ `record_id`)+ `context_json` 注入的业务字段
  (如 owner_name/task_guid)+ `action` + `round`(+ 分数按钮的 `score`)。
  业务动作(打分落账、评语写台账)仍由直调工具执行,引擎只编译卡片。

## 固定卡型模板(首选路径)

固定卡型的 XML 骨架已固化成模板(`templates/` 目录,与 SKILL.md 同目录):
`review-card.xml`(评价卡)、`todo-card.xml`(todo 卡)。**发固定卡型一律用模板,
只填数据、不手写结构**(手写 XML 仅用于模板覆盖不了的新卡型):

```
feishu_card_render(template="review-card",
  values_json={"owner_name":..., "title":..., "delivered_at":..., "record_id":..., "selected_score":0},
  context_json={"owner_name":..., "owner_open_id":..., "cycle_date":..., "task_guid":...,
                "ledger_app_token":..., "ledger_table_id":..., "comment_value":...})
```

```
feishu_card_render(template="todo-card",
  values_json={"title":"今日 TODO", "rows":[{"title":..., "task_guid":..., "detail":...,
               "bind_record":..., "done":false}]},
  context_json={"ledger_app_token":..., "ledger_table_id":...})
```

模板占位符是 `{key}`(值自动 XML 转义);`{rows}` 由行数组展开。

## 示例:评价卡(自由声明,约 10 行 XML)

```xml
<card title="TODO 评价" template="blue">
  <info label="执行人" value="黄子建"/>
  <info label="任务" value="优化TODO方案"/>
  <score min="1" max="5" rounds="20" bind-record="recXXX" selected="4"/>
  <comment placeholder="写点评语" bind-record="recXXX"/>
  <action-row>
    <button text="打回重做" type="reject" action="review_reject"/>
  </action-row>
</card>
```

调用 `feishu_card_render(card_xml=..., context_json={"owner_name":"黄子建","task_guid":"..."})`
拿到 `{card, handlers}`,再交给 `feishu_message_send_card`(card_json + action_handlers_json,
`multi_use=True`)发卡。**重建**:同一份 XML 改 `selected`/`round_` 后重新渲染,再
`feishu_message_edit_card` 原位更新(评价卡"点分高亮/评语回填"就是这么来的)。

## 边界

- 词汇表是开放的积木盒:新 SOP 需要新交互(如日期选择、多选块)时扩充元素,引擎同步支持。
- **list 卡(todo 卡)是 legacy 行机制**,第一版不与 score/comment 等 2.0 元素混用
  (混用直接报错);list 卡的回调走现有 `feishu_todo_card_tick`/`_untick` 工具,
  与手写版 todo 卡行为完全一致。
- 样式是固定语义映射,不是自由定制(自由 CSS 属后续版本);换配色只改引擎映射表。
- `card.xsd` 是本词汇表的机器可读规范(带注释),生成 XML 后按它自检合法性。
