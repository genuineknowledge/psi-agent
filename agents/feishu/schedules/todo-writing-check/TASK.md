---
name: todo-writing-check
description: 每周一三五 15:00 TODO 统一检测。触发时加载 todo-writing-standard / todo-truthfulness-check / todo-alignment-check 技能,按各技能规则逐项判定(格式/时间/粒度/价值/对齐/重要全覆盖/防复制/优先级),违规项私聊本人一次按类列全;对齐存疑项落盘供 16:00 任务使用。
cron: "0 15 * * 1,3,5"
visibility: silent
fire: prompt
---

# TODO 统一检测(15:00)

## 数据源(定时触发没有对话上下文,参数必须写全)

**身份声明(本任务所有需要 user 身份的 feishu 读调用一律照此)**:读看板 / 读工作树等需要用户 token 的调用,`user_key` 一律固定传 `ou_3d5539ff58b49de05d29787700796246`(高博,已授权 mindnote_read;refresh 有效期至 2026-10-08,到期需高博重新授权)。私聊发消息仍用机器人 tenant 身份,不带 user_key。

| 数据源 | 参数 |
|---|---|
| 判定口径(唯一来源) | 依次 `skill_manage(action="view", skill_name="...")` 加载三个技能:`todo-writing-standard`、`todo-truthfulness-check`、`todo-alignment-check`。判定以技能规则为准,不自行增减规则 |
| 团队 TODO 看板表 | 链接 https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc —— /wiki/ 链接先 `feishu_api` GET /open-apis/wiki/v2/spaces/get_node 换 obj_token,再读表;表结构(表头行/人名列/mentor 列/最新日期列)每次现场探,不写死 |
| 请假事实 | `feishu_leave_query`,approval_code=`99EEC396-536A-4C7A-8B2D-412584E35CE3`(只算已通过;审批中/读不出必须单独报告) |
| 工作树 | `feishu_worktree_read`(mindnote_token=OTRKbopcVm8J5xnJQx8cjzwAnvI,user_key=`ou_3d5539ff58b49de05d29787700796246`(高博,已授权 mindnote_read))——D7 重要全覆盖用。**定时回合无 <feishu_context>,user_key 必须显式传,不传走 default 死 token 必失败** |

## 流程

1. 先加载三个技能,判定口径以技能为准。
2. 读表:认表头、定位最新日期列(当期列)、读人名列与 mentor 列。
3. 逐人判定(全员):
   - **离职/无法识别人员先过滤**:名单先交 `feishu_member_status_check` 分类。已离职/冻结(resigned)→ **跳过全部判定,不进违规名单、不私聊,收尾报告完全不体现、不解释**(不出现「疑似离职」字样);解析失败(unresolved,重名)→ 单列「解析失败,需人工」,同样不判违规。不得把离职人员判成「未填违规」;
   - **未填**(空白)→ 先查假:请假免填跳过;无请假 → 按 todo-writing-standard 按时规则违规,私聊提醒;
   - **已填** → 按技能逐类判定。以下清单只用于核对有没有**漏类**;每类的判定逻辑、档位、文案一律以技能正文为准,本清单不写判据:
     1. 格式(todo-writing-standard 规则集:按时 / 按质 / 按量)
     2. 时间(todo-truthfulness-check D2)
     3. 粒度(同技能 D6)
     4. 价值(todo-alignment-check + todo-truthfulness-check D5)
     5. 对齐(D6)
     6. 重要全覆盖(D7)
     7. 防复制(技能防复制节)
     8. 优先级(todo-writing-standard 按优先级规则:成员自标优先,未标海豚判定——紧急轴 deadline+urgency_trap,重要轴 importance 三档+小目标拆解背书;建议排序附每条依据来源;config `priority` 段)。**本类必须判定并产出建议排序,任何时候不得跳过、不得以「避免撞车」等理由裁掉**;**成员自标顺序与判定一致 → 静默通过、不提示不调整;不一致 → 按提示级处理**(提示本人按正确顺序调整,不判违规)
     9. 验收核对(SOP v1.1 删除线 = 上级验收标记,口径以技能为准):对**上期**每条 TODO 调 `feishu_sheet_strike_read` 读删除线——带删除线 = 已验收;上期声明「已完成」但无删除线 → 提示「未见上级验收标记(删除线)」;有删除线但按完成度判定未达标 → 冲突提示(守住「验收一定要守住标准」),不静默放过。
4. **先落盘,后私聊**(落盘必须在私聊之前完成,私聊 O(人数) 耗量大,排后):判定完成后立即:
   - 把每人"与 mentor 对齐存疑"项追加写进 workspace 根目录文件 `align-pending.txt`(每行:姓名|期次|缺什么依据),供 16:00 任务读取;
   - 把评测结果写进 `.todo-eval/YYYY-MM-DD.json`(person / item / item_type / dimension_hits / verdict / evidence_level / evidence_refs / rules_hit),与 align-pending 一起在私聊前落盘。
5. 报告:违规/提示项私聊本人**一次**,所有缺项合在一条消息里按类列全(格式/时间/粒度/价值/对齐/全覆盖/防复制/优先级/验收),每条带依据;合规者不打扰。
6. **私聊对象 open_id 一律从 `feishu_member_status_check` 返回的 active 名单里按姓名取**,禁止从会话上下文/历史记录手填;名单里查不到姓名的 → 不私聊,单列「解析失败,需人工」。私聊发送一律用 `feishu_message_send`;读表/读技能/查假失败明说,不得顺势判违规。
