---
name: mentor-check-remind
description: 每周一三五 15:10 提醒各位 mentor 检查手下的 TODO 填报是否合理(只提醒不检查),并附 15:00 检测落盘的「对齐存疑清单」(海豚初判拿不准的给 mentor 人工看)。
cron: "10 15 * * 1,3,5"
visibility: silent
fire: prompt
---

# Mentor 检查提醒(15:10)

## 数据源(定时触发没有对话上下文,参数必须写全)

| 数据源 | 参数 |
|---|---|
| 对齐存疑清单 | workspace 根目录文件 `align-pending.txt`(15:00 任务落盘,每行:姓名\|期次\|缺什么依据);不存在则跳过附清单,只发提醒 |
| 团队 TODO 看板表 | 链接 https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc —— /wiki/ 链接先 `feishu_api` GET /open-apis/wiki/v2/spaces/get_node 换 obj_token,再读表;表结构(表头行/人名列/mentor 列/最新日期列)每次现场探,不写死 |

## 流程

1. 读看板 mentor 列去重得 mentor 名单(现场探);空值跳过。
2. 逐个用 `feishu_user_get`/通讯录把 mentor 姓名解析成 open_id;失败的名字单独列出。
3. 读 `align-pending.txt`(没有则跳过本步),把存疑项按 mentor 归组。
4. 用 `feishu_message_send` 私聊每个 mentor:
   - 固定文案:「<姓名>,请检查你手下的 TODO 填报是否合理:<看板表链接>」;
   - 若该 mentor 名下有存疑项,附一段:「海豚初判存疑(请人工确认):M 人 N 条」+ 逐条(姓名/期次/缺什么依据)。只附他名下的,不提其他 mentor 的人。
5. 本任务只提醒,不做任何检查,不复述任何人的 TODO 内容。
