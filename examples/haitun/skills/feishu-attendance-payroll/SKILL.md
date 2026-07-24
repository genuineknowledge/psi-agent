---
name: feishu-attendance-payroll
description: "Export Feishu attendance for a set of people over a period and compute a labor-fee (劳务费) report in the format the user specifies. Use when asked to summarize attendance and calculate pay/labor fees for a team, department, or list of people (e.g. monthly). Pulls the member roster with feishu_department_members, queries clock-in results with feishu_attendance_query, then applies the calculation rule and table format the USER provides each time — this skill hard-codes no money rules. Needs the app's attendance:task:readonly + contact scopes and attendance-admin data scope."
category: productivity
---

# Feishu Attendance → Labor Fee (劳务费) Report

Turn Feishu attendance data into a labor-fee report. This skill is a **generic
recipe**: it does the data gathering and structuring, but the **calculation
formula and output format are supplied by the user each run** — it hard-codes no
pay rules and no fixed columns.

Uses existing tools:
- `feishu_department_members(department_id, ..., recursive)` — get the people
- `feishu_attendance_query(user_ids, date_from, date_to, ...)` — get their clock-in results
- an output tool of the user's choice (`powerpoint`/excel skill, or `feishu_bitable_create_record`)

## Every run: get the rule from the user first

Before computing, confirm with the user (ask if not given):

1. **Who** — a department (id or name → resolve id) or an explicit person list.
2. **Period** — `date_from`/`date_to` as `yyyyMMdd`.
3. **The labor-fee formula** — e.g. "出勤天数 × 日薪", "有效工时 × 时薪", a fixed
   monthly amount minus deductions for 迟到/缺卡, etc. Get it explicitly.
4. **The output format** — which columns, ordering, totals, and where it goes
   (a file, a bitable, a message).

If any of these is missing, ask — do not guess a pay rule.

## Flow

1. **Roster.** `feishu_department_members(department_id, recursive=True)` (root id
   `"0"` = whole org). Collect `{user_id, open_id, name}`. For attendance you
   usually need the **employee_id** form — query with `user_id_type="user_id"` if
   the attendance API is configured on employee ids, and note which id form the
   attendance query expects.
2. **Attendance.** Batch `feishu_attendance_query(user_ids, date_from, date_to,
   employee_type=...)` — `user_ids` is a comma-separated string, ≤50 per call, so
   chunk larger rosters. You get per-person-per-day check-in/out time, result
   (Normal/Late/Early/Lack), and location.
3. **Aggregate** per person for the period: present days, late/lack counts, and
   whatever the user's formula needs (e.g. total valid work hours if available).
4. **Apply the user's formula** exactly as given to compute each person's fee.
   Show the intermediate numbers so it's auditable.
5. **Produce the report** in the user's format — a table with their columns and a
   total. Deliver where they asked (save a file / write a bitable / send a message).

## Boundaries

- Never invent a pay rate, formula, or attendance number. If attendance data is
  missing for someone (`invalid_user_ids`/`unauthorized_user_ids` from the query),
  list them as "no data" rather than assuming full attendance.
- Money is sensitive: state your assumptions and show the math; let the user
  verify before it's treated as final.
- This skill only reads attendance — it never writes/edits clock-in records.
