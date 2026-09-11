---
name: todo-ledger-push
description: 每周一三五 16:00 动态台账推送(合并原 15:10 mentor 提醒):对每 mentor 组生成上期 vs 本期前后对比文本表格(新开/承接/搞定/进行中/请假顺延),附对齐存疑清单与看板表链接,一条消息私聊发对应 mentor。不建多维表格、不写 base。
cron: "0 16 * * 1,3,5"
visibility: silent
fire: prompt
---

# 动态台账推送(16:00,已合并 15:10 mentor 提醒)

## 数据源(定时触发没有对话上下文,参数必须写全)

| 数据源 | 参数 |
|---|---|
| 对比口径(唯一来源) | `skill_manage(action="view", skill_name="company-todo-audit")` 的「前后对比摘要(上期 vs 本期,逐人)」节——新开/承接/消失/已闭环/回流/请假顺延口径以技能为准,不自行增减 |
| 团队 TODO 看板表 | 链接 https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc —— /wiki/ 链接先 `feishu_api` GET /open-apis/wiki/v2/spaces/get_node 换 obj_token,再读表;表结构(表头行/人名列/mentor 列/最新日期列)每次现场探,不写死。**本期列=最新日期列,上期列=紧邻上一列** |
| 请假事实 | `feishu_leave_query`,approval_code=`99EEC396-536A-4C7A-8B2D-412584E35CE3`(只算已通过) |
| 对齐存疑清单 | workspace 根目录文件 `align-pending.txt`(15:00 检测落盘,每行:姓名|期次|缺什么依据)。**存在则附进对应组的消息;不存在则跳过,不报错** |

## 硬约束(已定口径)

- **不建多维表格、不写 base、不调用 feishu_mentor_ledger_ensure / feishu_mentor_ledger_cycle_table / feishu_todo_compare_table / feishu_mentor_ledger_cycle_push 任何台账工具**——对比结果以**结构化卡片表格**呈现:发飞书交互卡片,body 用 markdown 组件放 GFM 表格,飞书渲染成真表格(带边框分栏),不是 | 符号堆出来的文本;
- 一条消息发对应 mentor:原 15:10 的检查提醒已并入本任务,不再单独提醒;
- 字段不照搬台账 schema:表格**不用 mentor 字段**(组名在卡片标题体现),只保留人 + 前后两日条目 + 搞定情况。

## 流程

1. 加载 company-todo-audit 技能,前后对比口径以该技能「前后对比摘要」节为准。
2. 读看板:当期列 + 上期列、人名列、mentor 列;按 mentor 分组。读看板需要用户 token 时,`user_key` 固定传 `ou_3d5539ff58b49de05d29787700796246`(高博,已授权;refresh 至 2026-10-08)。
3. 每组(每个 mentor)逐人对比,判每条 TODO 的**连续性**。**离职/无法识别人员不进表格**:名单先交 `feishu_member_status_check` 分类,已离职/冻结(resigned)→ 不出现在对比表格里(不写「未填报」行),**说明段也不体现、不解释**(不出现「疑似离职」字样);解析失败(unresolved,重名)→ 单列「解析失败,需人工」。不得把离职人员写成「未填报(无请假)」:
   - **搞定**:上期有、本期消失 → 推断已完成(拿不准写「待确认」);**上期该条经 `feishu_sheet_strike_read` 确认带删除线 = 已验收完成,直接写「已验收完成」,不写待确认**;
   - **承接**:两期都在(标题延续)→ 进行中;
   - **新开**:本期新增;
   - **请假顺延**:按查假结果;
   - **回流**:按技能口径。
4. 生成并发送**结构化卡片表格**:调 `feishu_todo_compare_card_send`(卡片布局由工具代码固定,不要自己拼卡片 JSON):
   - `receive_id` = 该 mentor 的 open_id,`mentor_name` = 该 mentor 姓名,`cycle_date` = 当期列日期;
   - `rows_json` = 本组逐人对比行:`[{"member": 姓名, "prev": 上期条目简写(多条用「;」隔), "curr": 本期条目简写, "status": 搞定情况}, ...]`;
   - **status 必须写具体,禁止笼统写「进行中」**:逐条给结论与依据,形如「搞定待确认2(9.4 到期两项);已验收完成1(删除线);消失待确认2;承接2(含SOP演进);新开2」/「未填报(两期,无请假)」——条目用简写标题+括号注明出处;
   - `notes` = 说明段,内容固定包括:期次说明(上期=X 列、本期=Y 列)、搞定定义(上期条目本期消失→推断已完成,待确认;带删除线=已验收完成)、本期已验收 N 条计数、未填报人员名单(无已通过请假覆盖的)、回流计数,以及手机提示句「手机上点表格行可展开查看详情」;
   - `align_notes` = align-pending.txt 里该组人员的行(有则传,无则传空)。
5. 工具发送后核对返回 message_id;任何一步失败明说,不静默跳过。
