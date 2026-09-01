---
name: feishu-attendance
description: 飞书考勤（attendance）接口表 —— 读考勤组（考勤组配置/绑定班次/排班特殊日期）和班次（打卡时间段/弹性规则/迟到早退阈值）。用 feishu_api 按表调用。查打卡结果仍然是专用工具 feishu_attendance_query。
---

# 飞书考勤接口

用 `feishu_api` 按下表调用。表里每一行都对应一个真实接口；`rules` 块是同一份知识的可执行副本，
参数不合规会在**发请求之前**被拦下来。

本域切的是**「结果」和「配置」**这条缝：

- **打卡结果**（谁几点打的卡、迟到没迟到）是 `feishu_attendance_query` 这个专用工具，见文末。
- **管理后台配置**（考勤组怎么设的、班次几点到几点、弹性多少分钟）走下面的表。

先分清两个概念，混起来是本域最常见的问不出东西的原因：

- **考勤组（group）**是「人的集合 + 规则的集合」：谁属于这个组、用什么方式打卡（GPS / Wi-Fi /
  考勤机 / IP）、允不允许外勤打卡、工作日不打卡算不算缺卡、以及**绑定了哪些班次**。
- **班次（shift）**是「一天的时间表」：几点上班几点下班（`punch_time_rule`）、迟到多少分钟算迟到、
  弹性上下班多少分钟（`flexible_rule`）、有没有休息时段。

两者靠 `punch_day_shift_ids` 连起来 —— **考勤组的配置里带着班次 id，班次的详情要再查一次**。
所以「张三几点该上班」这个问题要走两步：先读他所在考勤组的配置拿到 shift id，再读那个班次。

两个 list 接口都**只返回 id + 名字**（班次多给 `punch_times` / `is_flexible`），
规则一律要读详情接口才有。别指望 list 一次拿全。

## 考勤组（考勤组配置）

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 列出能看到的考勤组 | GET | `/open-apis/attendance/v1/groups` | `page_size`（≤50）、`page_token` |
| 读某个考勤组的完整配置 | GET | `/open-apis/attendance/v1/groups/:group_id` | `employee_type`、`dept_type` |

详情里值得盯的字段：`punch_type` 是**按位组合**的（1 GPS、2 Wi-Fi、4 考勤机、8 IP，
所以 `3` 表示 GPS + Wi-Fi 两种都行，不是「第 3 种」）；`group_type` 是 `0` 固定班制、
`2` 排班制、`3` 自由班制 —— **自由班制下 `punch_day_shift_ids` 通常是空的**，规则在
`free_punch_cfg` 里，拿不到班次不代表配置缺失。`work_day_no_punch_as_lack` 决定「没打卡」
是记缺卡还是不记。`need_punch_special_days` / `no_need_punch_special_days` 是排班特殊日期
（法定节假日调休那些），回答「某天要不要打卡」必须看这两个，光看班次会答错。

## 班次（班次配置）

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 列出能看到的班次 | GET | `/open-apis/attendance/v1/shifts` | `page_size`（≤50）、`page_token` |
| 读某个班次的完整配置 | GET | `/open-apis/attendance/v1/shifts/:shift_id` | 无（只有路径参数） |

`punch_time_rule` 是一个**数组**，一天打两次卡（上下班）就一段，打四次（含午休）就两段。
每段里 `on_time` / `off_time` 是「几点该打」，`late_minutes_as_late` /
`early_minutes_as_early` 是迟到早退的分钟阈值，同段里还带着最早可打卡 / 最晚可打卡的窗口
和缺卡阈值 —— 详情返回什么就照着读，不要预设字段名。

`is_flexible` 为真时才看 `flexible_minutes` / `flexible_rule`；弹性规则下**「迟到」的判定基准会平移**，
所以算迟到不能只拿 `on_time` 减打卡时间。

## 这些不走通用接口

| 工具 | 端点 | 为什么必须是工具 |
|---|---|---|
| `feishu_attendance_query` | POST `/open-apis/attendance/v1/user_tasks/query` | 查打卡结果。它返回的是**变换后的结果**而不是响应：飞书把打卡记录埋在 `user_task_results[].records[].check_in_record` 这样的两层数组里，工具把它摊平成「一人一天一行」，并把 `check_time`（epoch 秒的字符串）换算成本地时间字符串。另外它把 `invalid_user_ids` / `unauthorized_user_ids` 单独拎出来 —— `feishu-leave-audit-board` 和 `feishu-attendance-payroll` 都靠这两个列表把「查不到的人」标成无数据而不是标成缺卡，这是不能丢的。 |

```rules
- endpoint: GET /open-apis/attendance/v1/groups
  token: tenant_then_user
  fields:
    page_size: {default: 50, max: 50, min: 1}
  paginate: {items: group_list, page_size: 50}
  pitfalls:
    - 只返回 group_id + group_name，规则要读详情接口。

- endpoint: GET /open-apis/attendance/v1/groups/:group_id
  token: tenant_then_user
  fields:
    employee_type: {default: employee_id, choices: [employee_id, employee_no, open_id, union_id]}
    dept_type: {default: open_id, choices: [open_id, department_id]}
  pitfalls:
    - punch_type 是按位组合(1 GPS/2 Wi-Fi/4 考勤机/8 IP), 3 表示前两种都行。
    - group_type=3(自由班制)下 punch_day_shift_ids 常为空, 规则在 free_punch_cfg 里。
    - 回答"某天要不要打卡"必须看 need_punch_special_days / no_need_punch_special_days。

- endpoint: GET /open-apis/attendance/v1/shifts
  token: tenant_then_user
  fields:
    page_size: {default: 50, max: 50, min: 1}
  paginate: {items: shift_list, page_size: 50}
  pitfalls:
    - 只返回 shift_id + shift_name + punch_times + is_flexible, 规则要读详情接口。

- endpoint: GET /open-apis/attendance/v1/shifts/:shift_id
  token: tenant_then_user
  pitfalls:
    - punch_time_rule 是数组, 一天打两次卡一段、打四次两段。
    - is_flexible 为真时迟到判定基准会平移, 不能只拿 on_time 减打卡时间。

- endpoint: POST /open-apis/attendance/v1/user_tasks/query
  prefer_tool: feishu_attendance_query
  hard: true
  why: >
    打卡记录埋在 user_task_results[].records[] 两层数组里, 工具摊平成一人一天一行并把
    epoch 秒换算成本地时间; invalid_user_ids / unauthorized_user_ids 是两个技能用来把
    "查不到的人"标成无数据而不是缺卡的依据。
```

授权与权限：以上都是**只读**，用机器人的 tenant token 即可，不需要员工本人授权。需要 app 是
**自建应用**并有 `attendance:task:readonly` scope，而且**要在考勤管理后台单独授一次数据权限范围** ——
少了后者会回 1220004 / 1220005，那不是参数错，别改参数重试。
