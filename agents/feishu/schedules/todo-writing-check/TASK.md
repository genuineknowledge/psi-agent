---
name: todo-writing-check
description: 每周一三五 15:00 TODO 统一检测。触发时加载 todo-writing-standard / todo-truthfulness-check / todo-alignment-check 技能,按各技能规则逐项判定(格式/时间/粒度/价值/对齐/重要全覆盖/防复制/优先级),违规项私聊本人一次按类列全;对齐存疑项落盘供 15:10 任务使用。
cron: "0 15 * * 1,3,5"
visibility: silent
fire: prompt
---

# TODO 统一检测(15:00)

## 数据源(定时触发没有对话上下文,参数必须写全)

| 数据源 | 参数 |
|---|---|
| 判定口径(唯一来源) | 依次 `skill_manage(action="view", skill_name="...")` 加载三个技能:`todo-writing-standard`、`todo-truthfulness-check`、`todo-alignment-check`。判定以技能规则为准,不自行增减规则 |
| 团队 TODO 看板表 | 链接 https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc —— /wiki/ 链接先 `feishu_api` GET /open-apis/wiki/v2/spaces/get_node 换 obj_token,再读表;表结构(表头行/人名列/mentor 列/最新日期列)每次现场探,不写死 |
| 请假事实 | `feishu_leave_query`,approval_code=`99EEC396-536A-4C7A-8B2D-412584E35CE3`(只算已通过;审批中/读不出必须单独报告) |
| 工作树 | `feishu_worktree_read`(mindnote_token=OTRKbopcVm8J5xnJQx8cjzwAnvI,需 mindnote 授权)——D7 重要全覆盖用 |

## 流程

1. 先加载三个技能,判定口径以技能为准。
2. 读表:认表头、定位最新日期列(当期列)、读人名列与 mentor 列。
3. 逐人判定(全员):
   - **未填**(空白)→ 先查假:请假免填跳过;无请假 → 按 todo-writing-standard 按时规则违规,私聊提醒;
   - **已填** → 按技能逐类判定。以下清单只用于核对有没有**漏类**;每类的判定逻辑、档位、文案一律以技能正文为准,本清单不写判据:
     1. 格式(todo-writing-standard 规则集:按时 / 按质 / 按量)
     2. 时间(todo-truthfulness-check D2)
     3. 粒度(同技能 D6)
     4. 价值(todo-alignment-check + todo-truthfulness-check D5)
     5. 对齐(D6)
     6. 重要全覆盖(D7)
     7. 防复制(技能防复制节)
     8. 优先级(todo-writing-standard 按优先级规则:海豚模型判定象限 + 建议排序,提示级,成员不标注;config `priority` 段)
4. 报告:违规/提示项私聊本人**一次**,所有缺项合在一条消息里按类列全(格式/时间/粒度/价值/对齐/全覆盖/防复制/优先级),每条带依据;合规者不打扰。
5. **对齐存疑落盘**:把每人"与 mentor 对齐存疑"项追加写进 workspace 根目录文件 `align-pending.txt`(每行:姓名|期次|缺什么依据),供 15:10 任务读取。
6. 私聊发送一律用 `feishu_message_send`;读表/读技能/查假失败明说,不得顺势判违规。
