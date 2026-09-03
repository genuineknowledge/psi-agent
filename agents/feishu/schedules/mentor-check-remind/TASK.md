---
name: mentor-check-remind
description: 每周一三五 15:10 提醒各位 mentor 检查手下的 TODO 填报是否合理(只提醒不检查)。读看板表 mentor 列去重得名单,逐个私聊固定文案 + 看板表链接。
cron: "10 15 * * 1,3,5"
visibility: silent
fire: prompt
---

# Mentor 检查提醒(15:10)

## 数据源(定时触发没有对话上下文,参数必须写全)

| 数据源 | 参数 |
|---|---|
| 团队 TODO 看板表 | 链接 https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc —— /wiki/ 链接先 `feishu_api` GET /open-apis/wiki/v2/spaces/get_node 换 obj_token,再读表;表结构(表头行/人名列/mentor 列/最新日期列)每次现场探,不写死 |

## 流程

1. 换 token 后读表:认表头、定位最新日期列(当期列)与 mentor 列。
2. 取当期 mentor 列全部值去重得 mentor 名单;空值跳过。
3. 逐个用 `feishu_user_get` 把 mentor 姓名解析成 open_id;解析失败的名字单独列出,不静默跳过。
4. 用 `feishu_message_send` 私聊每个 mentor,文案固定:
   「<姓名>,请检查你手下的 TODO 填报是否合理:https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc」
5. 全部发完后简短汇总:发给了谁、谁解析失败。

## 红线

- 本任务只提醒,不做任何检查,不复述任何人的 TODO 内容。
- 读表失败、名字解析失败要明说,不得编造名单或 open_id。
- 同一个人不重复发(mentor 列可能多行同名);解析失败的人不发也不冒充。
- 私聊发送一律用 `feishu_message_send`(定时触发回合没有会话可回,不调用就是没发)。
