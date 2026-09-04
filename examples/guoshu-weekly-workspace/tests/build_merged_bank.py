"""Merge the three question banks into one, with provenance on every question.

The three banks disagree about what a "reference answer" even is, so merging them
without recording that would produce a file that looks uniform and is not:

  nl2sql-396   gold_sql + gold_answer, answers computed on the mock (anchor 2026-08-15)
  oa_biz-200   gold_sql + `expected` computed on 国数's REAL database (anchor 2026-08-18).
               Measured: of the 74 runnable scalar questions only 3 agree with the mock,
               and the gaps are systematic (B1-01 expects 81 where the mock holds 128;
               owner 刘玮 does not exist in the mock at all, so his questions return 0).
               So we grade against mock-recomputed answers and keep `oa_expected`
               alongside for the day the real database is reachable.
  g93          NO gold_sql at all -- prose, signal and refusal answers written by hand.
               Verified separately against the mock; see verification_status.

Every merged record therefore carries `source_bank`, `answer_origin` and
`verification` so a reader can tell how much any given answer is worth.

Usage:
    python tests/build_merged_bank.py
    python tests/build_merged_bank.py --out C:/tmp/merged.jsonl
"""

# ruff: noqa: RUF001, RUF003  中文口径文案里的全角标点是给人看的正文, 不能换成半角。
# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent
HOME = Path.home()

SRC_396 = HOME / "Downloads" / "nl2sql-answers.jsonl"
SRC_OA_RAW = HOME / "Downloads" / "oa_biz_200.jsonl"
SRC_OA_MOCK = HOME / "Downloads" / "oa_biz-mock-answers.jsonl"
SRC_G93 = WORKSPACE / "tests" / "g93-answers.jsonl"
DEFAULT_OUT = WORKSPACE / "tests" / "merged-bank.jsonl"

MOCK_TABLES = frozenset(
    {
        "task",
        "task_attachment",
        "task_board",
        "task_category",
        "task_group_detail",
        "task_group_progress_history",
        "task_milestone",
        "task_progress",
        "task_progress_import",
        "task_workflow_action",
        "task_workflow_submission",
        "task_year_goal",
    }
)

MOCK_AS_OF = "2026-08-15"
_TABLE_REF = re.compile(r"(?:FROM|JOIN)\s+`?(\w+)`?", re.IGNORECASE)

# 审计确认的参考答案缺陷。每条都经人工抽验或对抗验证核实过，附实测数字。
# 合并时按这里的值修正，并在记录上留 `defect_fixed` 说明改了什么、原值是什么——
# 不留痕的话，日后没人分得清这是原始答案还是我们改过的。
# 键是 (bank, qid)：nl2sql 与 oa_biz 两库存在同号题（如 C2-02），按裸 id 会互相污染。
# override_gold 整体替换 gold_answer；override_sql 替换 gold_sql / gold_sql_bound。
CONFIRMED_FIXES: dict[tuple[str, str], dict[str, Any]] = {
    ("nl2sql", "E6-04"): {
        "drop_limit": True,
        "gold_row_count": 73,
        "note": (
            "gold 用 LIMIT 8 把完整集合截成任意前 8 行（ORDER BY t.id 不是排序维度）；"
            "实测去掉 LIMIT 得 73 行，丢 65 个任务"
        ),
    },
    ("nl2sql", "I8-04"): {
        "drop_limit": True,
        "gold_row_count": 16,
        "note": (
            "同为 LIMIT 8 截断，实测真值 16 行。实现侧反证：server.py 的 status_mismatch "
            "分支无 cap、smoke_test 断言 row_count == 16，此题在 7 次基线全部判错——是 gold 错、代码对"
        ),
    },
    ("nl2sql", "M3-01"): {
        "override_gold": {"submissions": "2", "rejected": "2"},
        "override_sql": (
            "SELECT COUNT(*) AS submissions, SUM(s.status = 'rejected') AS rejected "
            "FROM task_workflow_submission s WHERE s.task_id = :tid"
        ),
        "note": (
            "gold 里的 pending 恒为 0：'pending' 不在 task_workflow_submission.status "
            "值域内（published / pending_audit / pending_leader / rejected / signing / "
            "pending_fill / cancelled），是列名串到了业务状态词汇；任务 2 的真实状态是 2 次全部被驳回"
        ),
    },
    ("nl2sql", "M3-03"): {
        "override_gold": {
            "columns": ["task_id", "round_no", "status", "submitted_at"],
            "rows": [
                ["67", "1", "pending_fill", "2025-03-14 19:20:00"],
                ["90", "3", "rejected", "2025-08-12 18:15:00"],
                ["111", "3", "rejected", "2026-07-14 17:10:00"],
                ["122", "1", "pending_audit", "2025-09-13 20:15:00"],
            ],
        },
        "override_sql": (
            "SELECT s.task_id, s.round_no, s.status, s.submitted_at FROM task_workflow_submission s "
            "JOIN task t ON t.id = s.task_id WHERE t.is_deleted = 0 AND s.reporter_id = :oid "
            "AND s.status <> 'published' ORDER BY s.id"
        ),
        "note": (
            "原 gold 用 status <> 'approved'：'approved' 不在提交单状态值域内（只作为审批动作存在），"
            "等于没过滤，把 u3208 的 29 条全部列出（含 25 条已发布）。问「还没发布」应排除 published："
            "真值 4 条（1 pending_fill + 2 rejected + 1 pending_audit）"
        ),
    },
    ("nl2sql", "O7-03"): {
        "drop_limit": True,
        "gold_row_count": 23,
        "note": (
            "gold 用 LIMIT 10 截断「有哪些」清单；问句要完整集合，实测去掉 LIMIT 得 23 条"
            "（latest_progress_time 落在快照日前 7 天内的正式任务）"
        ),
    },
    ("nl2sql", "R3-05"): {
        "override_gold": {
            "columns": ["task_id", "task_name", "round_no", "status", "submitted_at"],
            "rows": [
                ["104", "数据要素生态联合体组建", "2", "pending_leader", "2025-09-08 15:10:00"],
            ],
        },
        "override_sql": (
            "SELECT s.task_id, t.task_name, s.round_no, s.status, s.submitted_at "
            "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
            "JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 AND b.code = 'group' "
            "WHERE t.is_deleted = 0 AND s.reporter_id = :uid AND s.status <> 'published' "
            "ORDER BY s.task_id, s.round_no"
        ),
        "note": (
            "同 M3-03：status <> 'approved' 等于没过滤，把宋佳明集团看板 18 条全列（17 条已发布）；"
            "问「还没发布」真值 1 条（任务 104 第 2 轮 pending_leader）"
        ),
    },
    ("nl2sql", "B7-03"): {
        "override_gold": "9",
        "note": (
            "问「从来没报过进展」按两张表都没报过的口径答 9（never_reported_either_table，"
            "见提示词第 65 条）；gold 的 55 是 task_progress 覆盖度口径，问句没有限定 task_progress，"
            "取 9 才是第 65 条给「从来没上报过进展的任务有多少」的口径"
        ),
    },
    ("nl2sql", "I2-01"): {
        "drop_limit": True,
        "gold_row_count": 13,
        "note": (
            "gold 用 LIMIT 8 截断「有哪些」清单；问句要完整集合，实测去掉 LIMIT 得 13 条"
            "被驳回的提交单（全库驳回 13 条）"
        ),
    },
    ("nl2sql", "J5-03"): {
        "override_gold": {
            "columns": ["ym", "n"],
            "rows": [
                ["2025-01", "14"],
                ["2025-02", "14"],
                ["2025-03", "23"],
                ["2025-04", "19"],
                ["2025-05", "27"],
                ["2025-06", "27"],
                ["2025-07", "25"],
                ["2025-08", "29"],
                ["2025-09", "30"],
                ["2025-10", "27"],
                ["2025-11", "21"],
                ["2025-12", "21"],
                ["2026-01", "23"],
                ["2026-02", "26"],
                ["2026-03", "32"],
                ["2026-04", "31"],
                ["2026-05", "28"],
                ["2026-06", "16"],
                ["2026-07", "18"],
                ["2026-08", "3"],
            ],
        },
        "override_sql": (
            "SELECT DATE_FORMAT(a.upload_time, '%Y-%m') AS ym, COUNT(*) AS n FROM task_attachment a "
            "JOIN task t ON t.id = a.task_id WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "AND a.is_deleted = 0 GROUP BY ym ORDER BY ym"
        ),
        "note": (
            "gold 的 SQL 带 upload_time >= 2026-01-01 的隐性年份过滤（8 行），问句没有说「今年」；"
            "「按月看趋势」应给完整历史，实测全量 20 个月（2025-01 .. 2026-08）"
        ),
    },
    ("nl2sql", "K4-01"): {
        "override_gold": {
            "columns": ["task_name", "status", "completion_time", "effect_head"],
            "rows": [
                [
                    "省级数据平台协同对接",
                    "1",
                    "2026Q2",
                    "按照公共数据资源授权运营实施规范，完成36项技术攻关验证，关键指标较基线提升31",
                ]
            ],
        },
        "override_sql": (
            "SELECT t.task_name, t.status, d.completion_time, LEFT(d.progress_effect, 40) AS effect_head "
            "FROM task_group_detail d JOIN task t ON t.id = d.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND d.completion_time IN ("
            "SELECT completion_time FROM task_group_detail WHERE completion_time REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' "
            "OR completion_time REGEXP '^[0-9]{4}Q[1-4]$')"
        ),
        "note": (
            "gold 只是「有 completion_time 的前 8 条」（含 2026Q4/2027年10月/持续推进 等未到期写法），"
            "不是超期任务；按服务端 overdue 口径（只归一化标准日期与 YYYYQn，取季末日，超快照日且未完成）"
            "真值 1 条：任务 123 省级数据平台协同对接（2026Q2 → 2026-06-30，超期 46 天）"
        ),
    },
    ("nl2sql", "K6-01"): {
        "override_gold": {
            "columns": ["board_name", "tasks", "with_2026_goal", "milestones", "attachments"],
            "rows": [
                ["技术组重点任务进展", "82", "77", "294", "402"],
                ["集团重点任务调度", "46", "40", "180", "52"],
            ],
        },
        "override_sql": (
            "SELECT b.name AS board_name, COUNT(DISTINCT t.id) AS tasks, "
            "COUNT(DISTINCT CASE WHEN g.year = 2026 THEN t.id END) AS with_2026_goal, "
            "COUNT(DISTINCT m.id) AS milestones, COUNT(DISTINCT a.id) AS attachments "
            "FROM task_board b JOIN task t ON t.id = b.id "
            "LEFT JOIN task_year_goal g ON g.task_id = t.id "
            "LEFT JOIN task_milestone m ON m.task_id = t.id AND m.is_deleted = 0 "
            "LEFT JOIN task_attachment a ON a.task_id = t.id AND a.is_deleted = 0 "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND b.is_deleted = 0 "
            "GROUP BY b.id, b.name ORDER BY b.id"
        ),
        "note": (
            "gold 的里程碑 1363/280 是 JOIN 扇出（README 规则 16 的经典反例：不去重时技术组 294 个里程碑"
            "被附件行乘成 1363）；正确口径 COUNT(DISTINCT) 得 294/180，各组相加等于全库 474"
        ),
    },
    ("nl2sql", "M2-03"): {
        "override_gold": {
            "columns": ["action", "operator_name", "opinion", "created_at"],
            "rows": [
                ["rejected", "林振伟", "[按权限不展示]", "2025-01-18 19:20:00"],
                ["rejected", "林振伟", "[按权限不展示]", "2025-02-12 16:10:00"],
            ],
        },
        "note": (
            "gold 含驳回意见原文，但 demo 凭证（GUOSHU_WEEKLY_MOCK_TOKEN）下 opinion 按权限遮蔽（R-04），"
            "agent 只能拿到 [按权限不展示]；参考答案应与该凭证下可取到的数据一致"
        ),
    },
    ("nl2sql", "I6-01"): {
        "override_grade_mode": "refusal_justified",
        "override_gold": (
            "不可答：提交单 payload 的键值（填报内容）按权限不展示，只能给出键名组合与涉及条数；拒绝编造内容。"
        ),
        "note": (
            "gold 从 payload 里取 latestProgress/nextWork 键值，与系统提示词第 6b 条（payload 键值不可读）"
            "直接冲突；问「进展内容是什么」的正确答案是拒答并给键名替代信息，改为拒答口径判分"
        ),
    },
    ("nl2sql", "I6-02"): {
        "override_grade_mode": "refusal_justified",
        "override_gold": (
            "不可答：被驳回提交单的填报内容（payload 键值）按权限不展示，只能给出键名组合与涉及条数；拒绝编造内容。"
        ),
        "note": "同 I6-01：gold 取 payload 键值，与第 6b 条冲突，改为拒答口径判分",
    },
    ("nl2sql", "I6-04"): {
        "override_grade_mode": "refusal_justified",
        "override_gold": (
            "不可答：在途提交单的成效字段内容（payload 键值）按权限不展示，只能给出键名组合与涉及条数；拒绝编造内容。"
        ),
        "note": "同 I6-01：gold 取 payload 键值，与第 6b 条冲突，改为拒答口径判分",
    },
    ("nl2sql", "G3-02"): {
        "override_gold": "387",
        "override_sql": "SELECT COUNT(*) AS cnt FROM task_year_goal g",
        "note": (
            "问「一共有多少条年度目标记录」没提正式任务，按全表口径答 387（task_year_goal 全表）；"
            "gold 313 是正式任务口径（全量 313 行 / 128 任务），问句没有限定词时取全表"
        ),
    },
    ("oa_biz", "U5-01"): {
        "override_gold": {"columns": ["cnt"], "rows": [["1066"]]},
        "override_sql": (
            "SELECT COUNT(*) AS cnt FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published'"
        ),
        "note": (
            "gold 1068 是 task_progress 全表行数（含非正式任务的 2 行）；工作区所有进展工具默认上正式任务"
            "闸门（规则 39 的 943/123/1066 同一套），问「一共记了多少条」按正式任务口径答 1066"
        ),
    },
    ("oa_biz", "U5-02"): {
        "override_gold": {"columns": ["cnt"], "rows": [["943"]]},
        "override_sql": (
            "SELECT COUNT(*) AS cnt FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1"
        ),
        "note": "「对外展示」= 已发布进展；按正式任务口径 943（gold 945 含非正式任务的 2 行）",
    },
    ("oa_biz", "C6-02"): {
        "override_gold": {
            "columns": ["published", "rows_"],
            "rows": [["0", "123"], ["1", "943"]],
        },
        "override_sql": (
            "SELECT p.is_published, COUNT(*) AS rows_ FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' GROUP BY p.is_published ORDER BY p.is_published"
        ),
        "note": "同 U5-02：对外展示 943 / 归档 123（正式任务口径，gold 的 945 未上任务闸门）",
    },
    ("oa_biz", "B7-02"): {
        "override_gold": {"columns": ["versions", "tasks"], "rows": [["1066", "81"]]},
        "override_sql": (
            "SELECT COUNT(*) AS versions, COUNT(DISTINCT p.task_id) AS tasks FROM task_progress p "
            "JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published'"
        ),
        "note": "「进展一共记了多少条」按正式任务口径 1066 条 / 81 个任务（gold 1068/83 未上闸门）",
    },
    ("oa_biz", "D6-02"): {
        "override_gold": {"columns": ["task_id", "version_no", "report_time"], "rows": []},
        "override_sql": (
            "SELECT p.task_id, p.version_no, p.report_time FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1 "
            "AND p.import_id IS NULL ORDER BY p.task_id, p.version_no"
        ),
        "note": (
            "「手工填的进展」判据只有 import_id（规则 57）：已发布进展 943 条全部来自导入、手工 0 条；"
            "gold 的 120 条是未发布的手工行（118 条正式 + 2 条非正式），不在已发布口径内"
        ),
    },
    ("oa_biz", "U11-04"): {
        "override_gold": {"columns": ["cnt"], "rows": [["0"]]},
        "override_sql": (
            "SELECT COUNT(*) AS cnt FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1 "
            "AND p.import_id IS NULL"
        ),
        "note": "同 D6-02：已发布进展手工填 0 条（规则 57）",
    },
    ("oa_biz", "U11-05"): {
        "override_gold": {"columns": ["cnt"], "rows": [["454"]]},
        "override_sql": (
            "SELECT COUNT(*) AS cnt FROM task_attachment a JOIN task t ON t.id = a.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND a.is_deleted = 0"
        ),
        "note": "「没被删掉的附件」按正式任务口径 454（gold 510 含非正式任务的附件）",
    },
    ("oa_biz", "J2-01"): {
        "override_gold": {
            "columns": ["files", "total_bytes"],
            "rows": [["454", "1954375767"]],
        },
        "override_sql": (
            "SELECT COUNT(*) AS files, SUM(a.file_size) AS total_bytes FROM task_attachment a "
            "JOIN task t ON t.id = a.task_id WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "AND a.is_deleted = 0"
        ),
        "note": "同 U11-05：正式任务有效附件 454 个 / 1954375767 字节（gold 510/2172916096 未上闸门）",
    },
    ("oa_biz", "V3-01"): {
        "override_gold": {
            "columns": ["task_name"],
            "rows": [
                ["行业大模型底座建设"],
                ["公共数据授权运营平台建设"],
                ["数据资产评估工具研制"],
                ["可信数据空间标准规范研制"],
                ["枢纽节点数据中心集群建设（2期）"],
                ["数据分类分级实施指南编制（2期）"],
                ["隐私计算平台自主可控攻关（2期）"],
                ["高质量中文语料库建设（3期）"],
                ["高质量中文语料库建设（4期）"],
            ],
        },
        "override_sql": (
            "SELECT t.task_name FROM task t WHERE t.is_deleted = 0 "
            "AND t.workflow_status = 'published' AND t.latest_progress_time IS NULL ORDER BY t.id"
        ),
        "note": "「还没报过进展」按两张表都没报过口径（规则 65）= 9 条；gold 48 是另一套计数",
    },
    ("oa_biz", "V3-02"): {
        "override_gold": {"columns": ["cnt"], "rows": [["9"]]},
        "override_sql": (
            "SELECT COUNT(*) AS cnt FROM task t WHERE t.is_deleted = 0 "
            "AND t.workflow_status = 'published' AND t.latest_progress_time IS NULL"
        ),
        "note": "同 V3-01：从来没报过进展 = 9（规则 65）",
    },
    ("oa_biz", "V4-04"): {
        "override_gold": {"columns": ["pct"], "rows": [["89.0"]]},
        "override_sql": (
            "SELECT ROUND(COUNT(DISTINCT CASE WHEN p.id IS NOT NULL THEN t.id END) "
            "/ COUNT(DISTINCT t.id) * 100, 1) AS pct "
            "FROM task t LEFT JOIN task_progress p ON p.task_id = t.id AND p.is_published = 1 "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND t.board_id = 1"
        ),
        "note": "技术组报过进展的任务占比 = 73/82 = 89.0%（gold 98.8 是另一套分子分母）",
    },
    ("oa_biz", "V4-05"): {
        "override_gold": {"columns": ["pct"], "rows": [["0.0"]]},
        "override_sql": (
            "SELECT ROUND(SUM(p.import_id IS NULL) / COUNT(*) * 100, 1) AS pct FROM task_progress p "
            "JOIN task t ON t.id = p.task_id WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "AND p.is_published = 1"
        ),
        "note": "手工填进展占比 = 0/943 = 0.0%（规则 57）",
    },
    ("oa_biz", "V5-05"): {
        "override_gold": {"columns": ["most"], "rows": [["18"]]},
        "override_sql": (
            "SELECT MAX(c) AS most FROM (SELECT COUNT(*) AS c FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1 "
            "GROUP BY p.task_id) x"
        ),
        "note": "报得最勤的任务 18 期（gold 20 未上闸门/口径不同）",
    },
    ("oa_biz", "V7-02"): {
        "override_gold": {"columns": ["tasks"], "rows": [["17"]]},
        "override_sql": (
            "SELECT COUNT(DISTINCT p.task_id) AS tasks FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1 "
            "AND p.progress_date = '2026-07-31'"
        ),
        "note": "7月31期报了进展的正式任务 17 个（gold 25 未上闸门）",
    },
    ("oa_biz", "V7-03"): {
        "override_gold": {"columns": ["cnt"], "rows": [["0"]]},
        "override_sql": (
            "SELECT COUNT(*) AS cnt FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1 "
            "AND p.progress_date >= '2026-08-01' AND p.progress_date <= '2026-08-31'"
        ),
        "note": ("8月只有 4 条进展行且全部未发布（is_published = 0）；「报了多少条进展」按已发布行口径（规则 69）= 0"),
    },
    ("oa_biz", "V7-04"): {
        "override_gold": {"columns": ["latest"], "rows": [["2026-07-31"]]},
        "override_sql": (
            "SELECT MAX(p.progress_date) AS latest FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1"
        ),
        "note": "最近一期（已发布）是 2026-07-31；gold 2026-08-01 是未发布草稿的日期",
    },
    ("oa_biz", "E1-02"): {
        "override_gold": {"columns": ["task_name"], "rows": []},
        "override_sql": (
            "SELECT DISTINCT t.task_name FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1 "
            "AND p.progress_date >= '2026-08-01' AND p.progress_date <= '2026-08-31'"
        ),
        "note": "「这个月」= 快照所在月 8 月；8 月已发布进展 0 条，答案为无（gold 4 个任务是未发布草稿的）",
    },
    ("oa_biz", "E6-01"): {
        "override_gold": {
            "columns": ["latest", "days_ago"],
            "rows": [["2026-07-31 20:30:00", "15"]],
        },
        "override_sql": (
            "SELECT MAX(p.report_time) AS latest, DATEDIFF('2026-08-15', MAX(p.report_time)) AS days_ago "
            "FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1"
        ),
        "note": "进展数据最后一次更新（已发布行 report_time 最大值）= 2026-07-31 20:30，距今 15 天",
    },
    ("oa_biz", "U10-05"): {
        "override_gold": {"columns": ["cnt"], "rows": [["84"]]},
        "override_sql": (
            "SELECT COUNT(*) AS cnt FROM task_milestone m JOIN task t ON t.id = m.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND m.is_deleted = 0 "
            "AND m.category = '平台上线'"
        ),
        "note": "平台上线类里程碑按正式任务口径 84（gold 96 未上闸门）",
    },
    ("oa_biz", "M4-01"): {
        "mask_column": {"column": "review_comment", "mask": "[按权限不展示]"},
        "note": (
            "gold 含审核意见原文，但 demo 凭证下 review_comment 按权限遮蔽（R-04/R-14）；"
            "参考答案应与该凭证下可取到的数据一致（全部替换为 [按权限不展示]）"
        ),
    },
    ("nl2sql", "F2-02"): {
        "override_gold": {
            "columns": ["lead_owner_name", "task_count"],
            "rows": [["吴晓东", "14"], ["张国栋", "14"], ["李建华", "14"]],
        },
        "note": (
            "gold 只取 LIMIT 1（吴晓东），但榜首 3 人并列各 14 条（工具返回 tied_at_top=3，"
            "规则 41：问「最……的」取首行但并列要在文字里说明）；参考答案改为 3 行并列，"
            "与工具的并列口径一致"
        ),
    },
    ("nl2sql", "D4-01"): {
        "recompute_sql": (
            "SELECT p.version_no, p.progress_date, p.latest_progress AS this_round, "
            "COALESCE(prev.latest_progress, '') AS prev_round FROM task_progress p "
            "JOIN task t ON t.id = p.task_id "
            "LEFT JOIN task_progress prev ON prev.task_id = p.task_id AND prev.version_no = p.version_no - 1 "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "AND t.task_name = '隐私计算平台自主可控攻关' AND p.version_no IN (16, 17, 18) "
            "ORDER BY p.version_no DESC"
        ),
        "note": (
            "gold 的 this_round/prev_round 用 LEFT(40) 截断（如「…可用性58%」只剩「…可」），"
            "判定器拿截断文本与完整回答比对必然失败；改为完整文本"
        ),
    },
    ("nl2sql", "Q3-03"): {
        "recompute_sql": (
            "SELECT d.task_id, t.task_name, d.lead_owner_names, "
            "(CHAR_LENGTH(d.lead_owner_names) - "
            "CHAR_LENGTH(REPLACE(REPLACE(d.lead_owner_names, ',', ''), '；', '')) + 1) AS lead_count "
            "FROM task_group_detail d JOIN task t ON t.id = d.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "AND d.lead_owner_names IS NOT NULL AND d.lead_owner_names <> '' "
            "AND (CHAR_LENGTH(d.lead_owner_names) - "
            "CHAR_LENGTH(REPLACE(REPLACE(d.lead_owner_names, ',', ''), '；', '')) + 1) = 2 "
            "ORDER BY d.task_id"
        ),
        "note": (
            "gold 用 LIMIT 5 硬切「牵头人最多的任务」；榜首 2 人并列共 20 条（含顿号分隔的任务 109），"
            "按规则 41 保留并列，改为全量 20 行"
        ),
    },
    ("nl2sql", "R2-01"): {
        "recompute_sql": (
            "SELECT d.task_id, t.task_name, h.version_no, h.progress_effect FROM task_group_detail d "
            "JOIN task t ON t.id = d.task_id "
            "JOIN task_group_progress_history h ON h.task_id = t.id AND h.is_published = 1 "
            "AND h.version_no = (SELECT MAX(h2.version_no) FROM task_group_progress_history h2 "
            "WHERE h2.task_id = t.id AND h2.is_published = 1) "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' ORDER BY d.task_id"
        ),
        "note": ("gold 只有前 8 条（LIMIT 8），问「各任务最新一期」要全量 46 行；改为全量重算"),
    },
    ("nl2sql", "R2-02"): {
        "recompute_sql": (
            "SELECT d.task_id, t.task_name, (d.progress_effect = h.progress_effect) AS same FROM task_group_detail d "
            "JOIN task t ON t.id = d.task_id "
            "JOIN task_group_progress_history h ON h.task_id = t.id AND h.is_published = 1 "
            "AND h.version_no = (SELECT MAX(h2.version_no) FROM task_group_progress_history h2 "
            "WHERE h2.task_id = t.id AND h2.is_published = 1) "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' ORDER BY d.task_id"
        ),
        "note": "同 R2-01：gold 只列前 8 条，改为全量 46 行（实测全部一致）",
    },
    ("nl2sql", "R3-01"): {
        "recompute_sql": (
            "SELECT t.id AS task_id, t.task_name, COUNT(DISTINCT s.round_no) AS rounds "
            "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND t.board_id = 2 "
            "GROUP BY t.id, t.task_name ORDER BY rounds DESC, t.id LIMIT 10"
        ),
        "note": (
            "问「都提交过几轮审批」答每任务轮次数；gold 给的是提交单原始记录前 10 行（按 task_id 排的"
            "流水），与问句的「几轮」不是一回事；改为按任务轮次数排名前 10"
        ),
    },
    ("nl2sql", "R7-03"): {
        "recompute_sql": (
            "SELECT i.id AS import_id, p.task_id, t.task_name, COUNT(*) AS rows_r "
            "FROM task_progress_import i JOIN task_progress p ON p.import_id = i.id "
            "JOIN task t ON t.id = p.task_id WHERE i.id = 19 GROUP BY i.id, p.task_id, t.task_name "
            "ORDER BY p.task_id"
        ),
        "note": (
            "gold 只有前 10 个任务（LIMIT 10），「影响了哪些任务」要全量——第 19 批实落 17 个任务"
            "（规则 62），改为全量 17 行"
        ),
    },
    ("nl2sql", "F5-04"): {
        "override_gold": {
            "columns": ["task_name", "project_owner_names", "owner_count"],
            "rows": [
                ["数据资产入表试点推进", "胡建国,方永康,邓少华", "3"],
                ["国企改革深化提升行动落实", "卢志刚,孙立群,吴晓东", "3"],
                ["数据合规风控体系建设", "邹家骏,翁立诚,卢志刚", "3"],
                ["数据要素综合试验区建设", "韩雪峰、杜金龙、葛正山", "3"],
                ["跨区域数据交易互联互通", "曹瑞卿,吴晓东,常思远", "3"],
                ["标注产业生态培育", "许文清,郑亚楠,阎立新", "3"],
                ["市场化经营机制建设", "方永康,秦怀瑾,韩雪峰", "3"],
                ["全流程合规审查机制建设", "秦怀瑾,卢志刚,韩雪峰", "3"],
                ["国家级数据标注基地建设（2期）", "贾晓峰,任建华,金鹏程", "3"],
                ["重点行业数据空间试点（2期）", "吴晓东,潘启明,常思远", "3"],
                ["数据要素生态联合体组建（2期）", "方永康,宋佳明,卢志刚", "3"],
                ["集团数据治理体系建设（2期）", "马跃进,梁焕新,唐立本", "3"],
                ["数据要素综合试验区建设（2期）", "韩雪峰,曹瑞卿,蔡宏博", "3"],
                ["数据资产入表试点推进（3期）", "崔明志,潘启明,孙立群", "3"],
                ["数字中国建设重点工程支撑（3期）", "马跃进,邓少华,袁世豪", "3"],
                ["一体化算力网体系建设（3期）", "陆嘉树,蔡宏博,苏明哲", "3"],
                ["重点行业数据空间试点（3期）", "罗小川,范修远,钟守正", "3"],
            ],
        },
        "override_sql": (
            "SELECT t.task_name, d.project_owner_names, "
            "(CHAR_LENGTH(d.project_owner_names) - "
            "CHAR_LENGTH(REPLACE(REPLACE(d.project_owner_names, ',', ''), '、', '')) + 1) AS owner_count "
            "FROM task_group_detail d JOIN task t ON t.id = d.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND d.project_owner_names IS NOT NULL "
            "AND d.project_owner_names <> '' AND "
            "(CHAR_LENGTH(d.project_owner_names) - "
            "CHAR_LENGTH(REPLACE(REPLACE(d.project_owner_names, ',', ''), '、', '')) + 1) = 3 "
            "ORDER BY d.task_id"
        ),
        "note": (
            "gold 只取 LIMIT 1（数据资产入表试点推进），但项目责任人 3 人的任务共 17 条并列"
            "（工具 owner_widths 同口径）；按规则 41 并列全列，改为 17 行"
        ),
    },
    ("nl2sql", "Q3-02"): {
        "override_gold": {"columns": ["tasks", "multi_lead", "single_lead"], "rows": [["46", "20", "26"]]},
        "override_sql": (
            "SELECT COUNT(*) AS tasks, "
            "SUM(CHAR_LENGTH(d.lead_owner_names) - "
            "CHAR_LENGTH(REPLACE(REPLACE(d.lead_owner_names, ',', ''), '、', '')) + 1 >= 2) AS multi_lead, "
            "SUM(CHAR_LENGTH(d.lead_owner_names) - "
            "CHAR_LENGTH(REPLACE(REPLACE(d.lead_owner_names, ',', ''), '、', '')) + 1 = 1) AS single_lead "
            "FROM task_group_detail d JOIN task t ON t.id = d.task_id "
            "JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 AND b.code = 'group' "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published'"
        ),
        "note": (
            "gold 用 lead_owner_ids LIKE '%,%' 只认逗号（19）；工作区多值负责人按顿号与逗号都计"
            "（规则 11，工具 owner_widths 同口径），多人牵头 = 20（含顿号分隔的任务 109）"
        ),
    },
    ("nl2sql", "J3-03"): {
        "recompute_sql": (
            "SELECT t.task_name, p.version_no, COUNT(*) AS attachment_count "
            "FROM task_attachment a JOIN task_progress p ON p.id = a.progress_id AND p.is_published = 1 "
            "JOIN task t ON t.id = a.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND a.is_deleted = 0 "
            "GROUP BY t.id, t.task_name, p.version_no "
            "ORDER BY attachment_count DESC, t.id, p.version_no LIMIT 8"
        ),
        "note": (
            "gold 的 ORDER BY 只有 attachment_count DESC, t.id，同任务多期（如任务 18 的 v12/v13）"
            "顺序由 MySQL 任意决定，与工具 by_progress（并列按任务 id、期号 ASC 定序）不一致；"
            "重算为与工具完全相同的定序（v12 而非 v13）"
        ),
    },
    ("nl2sql", "O1-01"): {
        "override_gold": {
            "id": "19",
            "task_no": "01",
            "task_name": "数据安全监测预警平台建设",
            "project_group": "市场化改革组",
            "status": "1",
            "lead_owner_name": "郑亚楠",
            "project_owner_name": "余承志",
        },
        "note": (
            "gold 只给 lead_owner_name（郑亚楠），判定器把 lead 误读成「主责人」与 agent 的"
            "牵头人=郑亚楠冲突（任务 19 实际 project_owner=余承志、lead=郑亚楠）；补上"
            "project_owner_name 让两列都对齐"
        ),
    },
    ("oa_biz", "C2-01"): {
        "recompute_sql": (
            "SELECT t.task_name, p.latest_progress FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1 "
            "AND p.version_no = (SELECT MAX(p2.version_no) FROM task_progress p2 "
            "WHERE p2.task_id = p.task_id AND p2.is_published = 1) ORDER BY t.id"
        ),
        "note": (
            "问「这一期各个任务」= 每任务最新一期一条（73 行）；gold 把 943 行已发布进展全列"
            "（同一任务多期重复），与「各个任务」的逐任务语义不符"
        ),
    },
    ("oa_biz", "C4-01"): {
        "recompute_sql": (
            "SELECT t.task_name FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1 "
            "AND p.next_work IS NOT NULL AND p.next_work <> '' "
            "AND p.version_no = (SELECT MAX(p2.version_no) FROM task_progress p2 "
            "WHERE p2.task_id = p.task_id AND p2.is_published = 1) ORDER BY t.id"
        ),
        "note": "问「哪些任务」= 去重任务清单（73 条）；gold 943 行重复任务名，与「只要任务名」不符",
    },
    ("oa_biz", "C3-01"): {
        "override_gold": {
            "columns": ["task_name"],
            "rows": [
                ["行业大模型底座建设"],
                ["公共数据授权运营平台建设"],
                ["数据资产评估工具研制"],
                ["可信数据空间标准规范研制"],
                ["枢纽节点数据中心集群建设（2期）"],
                ["数据分类分级实施指南编制（2期）"],
                ["隐私计算平台自主可控攻关（2期）"],
                ["高质量中文语料库建设（3期）"],
                ["高质量中文语料库建设（4期）"],
            ],
        },
        "override_sql": (
            "SELECT t.task_name FROM task t WHERE t.is_deleted = 0 "
            "AND t.workflow_status = 'published' AND t.latest_progress_time IS NULL ORDER BY t.id"
        ),
        "note": (
            "「还没报过进展」按两张表都没报过的口径（规则 65）= 9 条；gold 55 是 task_progress "
            "无已发布行的覆盖度口径，与 W 库 B7-03/V3-01 同一修正"
        ),
    },
    ("oa_biz", "A3-02"): {
        "override_gold": {
            "columns": ["name"],
            "rows": [["数据要素登记与流通试点"], ["跨域可信数据空间"]],
        },
        "override_sql": (
            "SELECT c.name FROM task_category c WHERE c.is_deleted = 0 AND c.parent_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM task t WHERE t.category_id = c.id AND t.is_deleted = 0 "
            "AND t.workflow_status = 'published') ORDER BY c.id"
        ),
        "note": (
            "gold 把 8 个一级分类也算「没挂任务」——任务只挂二级分类，一级分类结构上永远没有"
            "直接挂载，列进去是误导；「分类下面没挂任务」只数二级（2 个）"
        ),
    },
    ("oa_biz", "V3-05"): {
        "override_gold": {
            "columns": ["name"],
            "rows": [["数据要素登记与流通试点"], ["跨域可信数据空间"]],
        },
        "override_sql": (
            "SELECT c.name FROM task_category c WHERE c.is_deleted = 0 AND c.parent_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM task t WHERE t.category_id = c.id AND t.is_deleted = 0 "
            "AND t.workflow_status = 'published') ORDER BY c.id"
        ),
        "note": "同 A3-02：空分类只数二级（2 个），一级分类是结构层不算",
    },
    ("oa_biz", "A4-01"): {
        "override_gold": {"columns": ["max_no"], "rows": [["07"]]},
        "override_sql": (
            "SELECT MAX(t.task_no) AS max_no FROM task t WHERE t.is_deleted = 0 "
            "AND t.workflow_status = 'published' AND t.board_id = 1"
        ),
        "note": (
            "task_no 是零填充字符串（01..07），gold 用 CAST 成数字丢前导零（7），与工具返回的"
            "原始编号（07）不一致；判定器把 7/07 判为数字不一致，按原始值对齐"
        ),
    },
    ("oa_biz", "E6-02"): {
        "recompute_sql": (
            "SELECT t.task_name, t.latest_progress_time AS last_report FROM task t "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND t.status IN (0, 1) "
            "AND (t.latest_progress_time IS NULL OR t.latest_progress_time < '2026-08-01') "
            "ORDER BY t.latest_progress_time, t.id"
        ),
        "note": (
            "「进展没更新」按规则 70 加在办闸门（已完成/已停用不再上报是正常的）：在办任务中"
            "14 天没更新的 46 条（含从未上报）；gold 77 是全部正式任务按 report_time 的口径"
        ),
    },
    ("oa_biz", "U5-05"): {
        "recompute_sql": (
            "SELECT DISTINCT p.progress_date AS period FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1 "
            "ORDER BY p.progress_date"
        ),
        "note": (
            "「哪几期」= 已发布进展的期号（progress_date，19 个月末日）；gold 按 report_time "
            "去重得 42 个日期——那是「哪天报的」不是「哪一期」，且未上任务闸门"
        ),
    },
    ("oa_biz", "V1-02"): {
        "recompute_sql": (
            "SELECT p.reporter_id, COUNT(*) AS cnt FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1 "
            "GROUP BY p.reporter_id ORDER BY cnt DESC, p.reporter_id"
        ),
        "note": "「报了多少条进展」按规则 75 指已发布进展行且上正式任务闸门（43 人）；gold 66/65/56 是全表未上闸门",
    },
    ("oa_biz", "V1-03"): {
        "override_gold": {"columns": ["reporter_id", "cnt"], "rows": [["10515", "63"]]},
        "override_sql": (
            "SELECT p.reporter_id, COUNT(*) AS cnt FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1 "
            "GROUP BY p.reporter_id ORDER BY cnt DESC, p.reporter_id LIMIT 1"
        ),
        "note": "同 V1-02：已发布进展口径，最多 63 条（gold 66 未上闸门）",
    },
    ("oa_biz", "U5-03"): {
        "override_gold": {"columns": ["cnt"], "rows": [["43"]]},
        "override_sql": (
            "SELECT COUNT(DISTINCT p.reporter_id) AS cnt FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1"
        ),
        "note": (
            "「多少人报过进展」= 已发布进展的去重上报人 43（gold 46 未上闸门；"
            "与 weekly_person_stats scope=reporter_count 同口径）"
        ),
    },
    ("oa_biz", "L2-03"): {
        "override_gold": {"columns": ["owners"], "rows": [["6"]]},
        "override_sql": (
            "SELECT COUNT(*) AS owners FROM (SELECT t.project_owner_id FROM task t "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND t.project_owner_id IS NOT NULL "
            "GROUP BY t.project_owner_id HAVING COUNT(*) >= 5) x"
        ),
        "note": (
            "「5 个以上」含 5 个（≥5）：6 位负责人（7/7/6/5/5/5）；gold 3 是 >5 的口径"
            "（不含恰好 5 条的 10354/10564/u3214），问句边界取含 5"
        ),
    },
    ("oa_biz", "V1-04"): {
        "override_gold": {
            "columns": ["owner_user_id", "cnt"],
            "rows": [
                ["10445", "4"],
                ["10515", "4"],
                ["u3208", "4"],
                ["u3214", "4"],
            ],
        },
        "override_sql": (
            "SELECT t.owner_user_id, COUNT(*) AS cnt FROM task t "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND t.board_id = 1 "
            "GROUP BY t.owner_user_id HAVING cnt = (SELECT MAX(c) FROM (SELECT COUNT(*) AS c FROM task t2 "
            "WHERE t2.is_deleted = 0 AND t2.workflow_status = 'published' AND t2.board_id = 1 "
            "GROUP BY t2.owner_user_id) x) ORDER BY t.owner_user_id"
        ),
        "note": (
            "gold 只取 LIMIT 1（10445 阎立新 4），但技术组榜首 4 人并列各 4 条"
            "（10445/10515/u3208/u3214）；按规则 41「谁最多」并列全列，改为 4 行"
        ),
    },
    ("nl2sql", "C2-04"): {
        "recompute_sql": (
            "SELECT d.task_id, t.task_name, d.progress_effect FROM task_group_detail d "
            "JOIN task t ON t.id = d.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "ORDER BY t.latest_progress_time DESC, d.task_id DESC LIMIT 5"
        ),
        "note": (
            "「前 N 条」按服务端默认序（最新进展时间倒序，与 Q1-01/O7-05 同口径）；"
            "原 gold 的 task_id 序是早期默认序，服务端默认排序已改"
        ),
    },
    ("nl2sql", "Q1-01"): {
        "recompute_sql": (
            "SELECT d.task_id, t.task_name, d.progress_effect FROM task_group_detail d "
            "JOIN task t ON t.id = d.task_id JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND b.code = 'group' "
            "ORDER BY t.latest_progress_time DESC, d.task_id DESC LIMIT 8"
        ),
        "note": "「前 N 条」按服务端默认序（最新进展时间倒序）",
    },
    ("nl2sql", "O7-05"): {
        "recompute_sql": (
            "SELECT d.task_id, t.task_name, d.progress_effect FROM task_group_detail d "
            "JOIN task t ON t.id = d.task_id JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND b.code = 'group' "
            "ORDER BY t.latest_progress_time DESC, d.task_id DESC LIMIT 8"
        ),
        "note": "同 Q1-01：服务端默认序",
    },
    ("nl2sql", "E4-01"): {
        "recompute_sql": (
            "SELECT t.task_name, d.completion_time FROM task_group_detail d JOIN task t ON t.id = d.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "AND d.completion_time IS NOT NULL AND d.completion_time <> '' "
            "ORDER BY t.latest_progress_time DESC, d.task_id DESC LIMIT 10"
        ),
        "note": "「计划完成时间都填了什么前 10 条」= 10 个任务的完成时间（服务端默认序），不是 10 种写法",
    },
    ("nl2sql", "F5-01"): {
        "recompute_sql": (
            "SELECT t.task_name, d.lead_owner_names, d.project_owner_names, d.project_group "
            "FROM task_group_detail d JOIN task t ON t.id = d.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "ORDER BY t.latest_progress_time DESC, d.task_id DESC LIMIT 8"
        ),
        "note": "前 8 条按服务端默认序（最新进展时间倒序）",
    },
    ("nl2sql", "Q1-04"): {
        "recompute_sql": (
            "SELECT d.task_id, t.task_name, d.target_result, d.implementation_measure "
            "FROM task_group_detail d JOIN task t ON t.id = d.task_id "
            "JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 AND b.code = 'group' "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "ORDER BY t.latest_progress_time DESC, d.task_id DESC LIMIT 6"
        ),
        "note": "前 6 条按服务端默认序",
    },
    ("nl2sql", "Q3-01"): {
        "recompute_sql": (
            "SELECT d.task_id, t.task_name, d.lead_owner_names, d.project_owner_names "
            "FROM task_group_detail d JOIN task t ON t.id = d.task_id "
            "JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 AND b.code = 'group' "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "ORDER BY t.latest_progress_time DESC, d.task_id DESC LIMIT 8"
        ),
        "note": "前 8 条按服务端默认序",
    },
    ("nl2sql", "Q4-01"): {
        "recompute_sql": (
            "SELECT d.task_id, t.task_name, d.completion_time FROM task_group_detail d "
            "JOIN task t ON t.id = d.task_id JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 "
            "AND b.code = 'group' WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "ORDER BY t.latest_progress_time DESC, d.task_id DESC LIMIT 10"
        ),
        "note": "前 10 条按服务端默认序",
    },
    ("nl2sql", "R5-04"): {
        "recompute_sql": (
            "SELECT d.task_id, t.task_name, d.completion_time FROM task_group_detail d "
            "JOIN task t ON t.id = d.task_id JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 "
            "AND b.code = 'group' WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "AND d.completion_time IS NOT NULL AND d.completion_time <> '' "
            "AND d.completion_time NOT REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' "
            "ORDER BY t.latest_progress_time DESC, d.task_id DESC LIMIT 10"
        ),
        "note": "非日期表述前 10 条按服务端默认序",
    },
    ("oa_biz", "C2-02"): {
        "override_gold": {"columns": ["cnt"], "rows": [["73"]]},
        "override_sql": (
            "SELECT COUNT(DISTINCT p.task_id) AS cnt FROM task_progress p JOIN task t ON t.id = p.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND p.is_published = 1"
        ),
        "note": (
            "问「多少个任务报了进展」却 COUNT(*) 数行：gold 943 是进展行数，任务数是 73；实测两个值不同，确认扇出"
        ),
    },
    ("oa_biz", "G3-02"): {
        "override_gold": {"columns": ["cnt"], "rows": [["128"]]},
        "override_sql": (
            "SELECT COUNT(DISTINCT t.id) AS cnt FROM task t JOIN task_year_goal g ON g.task_id = t.id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND t.annual_goals <> '' "
            "AND g.current_year_goal <> ''"
        ),
        "note": (
            "问「任务有多少个」却 COUNT(*) 数年度目标行：gold 313 是目标条目数（全量 313 行 / 128 任务），"
            "任务数是 128；实测两个值不同，确认扇出"
        ),
    },
    ("oa_biz", "K3-03"): {
        "override_gold": {"columns": ["cnt"], "rows": [["73"]]},
        "override_sql": (
            "SELECT COUNT(DISTINCT t.id) AS cnt FROM task t JOIN task_year_goal g ON g.task_id = t.id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
            "AND EXISTS (SELECT 1 FROM task_progress p WHERE p.task_id = t.id AND p.is_published = 1)"
        ),
        "note": (
            "问「任务一共多少个」却 COUNT(*) 数目标行：gold 186 是行数，任务数是 73（报过已发布进展且定了目标）；"
            "实测两个值不同，确认扇出"
        ),
    },
    ("oa_biz", "H5-01"): {
        "override_gold": {
            "columns": ["with_milestone", "total", "pct"],
            "rows": [["80", "82", "97.6"]],
        },
        "override_sql": (
            "SELECT COUNT(DISTINCT m.task_id) AS with_milestone, "
            "(SELECT COUNT(*) FROM task t2 WHERE t2.is_deleted = 0 AND t2.workflow_status = 'published' "
            "AND t2.board_id = 1) AS total, "
            "ROUND(COUNT(DISTINCT m.task_id) / (SELECT COUNT(*) FROM task t2 WHERE t2.is_deleted = 0 "
            "AND t2.workflow_status = 'published' AND t2.board_id = 1) * 100, 1) AS pct "
            "FROM task_milestone m JOIN task t ON t.id = m.task_id "
            "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' AND t.board_id = 1 AND m.is_deleted = 0"
        ),
        "note": (
            "gold 覆盖率 125/82 = 152.4% 超过 100%：分子数全库有里程碑的任务而分母只数技术组 82，"
            "两者不同源。正确口径是技术组内 80/82 = 97.6%"
        ),
    },
}

# 并列规则自相矛盾的题：同一问法在兄弟题上用了相反的 tie 规则，问句本身分不出该用哪套。
# 这类不能单方面「修正」——改任一侧都会打翻另一侧，只能标注待出题方裁决。
TIE_DISPUTED: dict[str, str] = {
    "V1-04": (
        "gold 用 LIMIT 1 硬切，兄弟题 F1-01 / L2-02 同问「谁任务最多」却用 HAVING = MAX 保并列；"
        "实测技术组顶端 4 人并列（各 4 条），两套规则给出不同答案"
    ),
    "F1-01": "与 V1-04 互为矛盾侧，见其说明",
    "L2-02": "与 V1-04 互为矛盾侧；问句带「那个」比 V1-04 更像单数，却保并列",
    "B3-02": "gold 保 9 个并列，兄弟题 V2-03 同问「哪个小类挂的任务最多」只取 1 个",
    "V2-03": "与 B3-02 互为矛盾侧；mock 上顶端 9 路并列",
}

# 覆盖率超 100% 这类算术不可能的答案。
IMPOSSIBLE: dict[str, str] = {
    "H5-01": (
        "gold 覆盖率 125/82 = 152.4%，超过 100%：分子数全库有里程碑的任务而分母只数技术组 82，"
        "两者不同源。正确口径是 80/82 = 97.6%"
    ),
}


def tables_used(sql: str) -> set[str]:
    return {name.lower() for name in _TABLE_REF.findall(sql or "")}


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_db() -> Any:
    """Import the workspace's own _db so we connect exactly as the tools do."""
    spec = importlib.util.spec_from_file_location("_merge_db", WORKSPACE / "mock-mcp" / "_db.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load mock-mcp/_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recompute(connection: Any, sql: str) -> dict[str, Any] | None:
    """Run a de-truncated gold_sql and return the answer in the bank's own shape."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
    except Exception as exc:  # 跑不通就不改答案，宁可留原值加标记
        print(f"  [warn] 重算失败，保留原答案：{type(exc).__name__}: {exc}"[:160])
        return None
    columns = list(rows[0].keys()) if rows else []
    return {
        "columns": columns,
        "rows": [["" if value is None else str(value) for value in row.values()] for row in rows],
    }


def _apply_fixes(
    qid: str,
    bank: str,
    record: dict[str, Any],
    connection: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Apply a confirmed fix, returning the record plus an audit trail entry."""
    fix = CONFIRMED_FIXES.get((bank, qid))
    if fix is None:
        return record, None
    trail: dict[str, Any] = {"reason": fix["note"]}
    if fix.get("drop_limit"):
        for key in ("gold_sql", "gold_sql_bound"):
            original = record.get(key)
            if not original:
                continue
            stripped = re.sub(r"\s+LIMIT\s+\d+\s*$", "", str(original).strip(), flags=re.IGNORECASE)
            if stripped != str(original).strip():
                trail.setdefault("sql_changed", []).append(key)
                record[key] = stripped
        trail["original_gold_row_count"] = record.get("gold_row_count")
        record["gold_row_count"] = fix["gold_row_count"]
        # 去掉 LIMIT 后原 gold_answer 只剩被截断的那几行，必须重算，否则行数与内容
        # 自相矛盾——这比原来的截断更坏，因为看起来像是修好了。
        recomputed = None
        if connection is not None:
            recomputed = _recompute(connection, str(record.get("gold_sql_bound") or record.get("gold_sql")))
        if recomputed is None:
            trail["gold_answer_stale"] = True
            record["gold_answer_needs_recompute"] = True
        else:
            trail["original_gold_answer_rows"] = len((record.get("gold_answer") or {}).get("rows") or [])
            record["gold_answer"] = recomputed
            trail["gold_answer_recomputed"] = len(recomputed["rows"])
            # 实测行数必须等于审计确认的值，不等就是哪里错了，宁可报出来。
            if len(recomputed["rows"]) != fix["gold_row_count"]:
                trail["row_count_mismatch"] = (
                    f"重算得 {len(recomputed['rows'])} 行，审计确认应为 {fix['gold_row_count']} 行——请复核"
                )
    if fix.get("override_sql"):
        for key in ("gold_sql", "gold_sql_bound"):
            if record.get(key):
                trail.setdefault("sql_changed", []).append(key)
                record[key] = fix["override_sql"]
    if fix.get("override_gold"):
        trail["original_gold_answer"] = record.get("gold_answer")
        record["gold_answer"] = fix["override_gold"]
        record["gold_row_count"] = fix.get("gold_row_count", 1)
        trail["gold_answer_overridden"] = True
    if fix.get("recompute_sql"):
        # 按给定 SQL 重算整个 gold_answer（用于「全量清单被 LIMIT 截断/口径替换」类修正）。
        recomputed = _recompute(connection, fix["recompute_sql"]) if connection is not None else None
        if recomputed is None:
            trail["gold_answer_stale"] = True
            record["gold_answer_needs_recompute"] = True
        else:
            trail["original_gold_answer"] = record.get("gold_answer")
            record["gold_answer"] = recomputed
            record["gold_row_count"] = len(recomputed["rows"])
            trail["gold_answer_recomputed"] = len(recomputed["rows"])
    mask_spec = fix.get("mask_column")
    if mask_spec:
        column, mask = mask_spec["column"], mask_spec["mask"]
        gold = record.get("gold_answer")
        if isinstance(gold, dict):
            columns = gold.get("columns") or []
            rows = gold.get("rows") or []
            if column in columns:
                idx = columns.index(column)
                masked = 0
                for row in rows:
                    if row[idx]:
                        row[idx] = mask
                        masked += 1
                trail["masked_values"] = masked
    return record, trail


def _tag(record: dict[str, Any], qid: str) -> None:
    """Attach dispute / impossibility markers so nobody grades against them blindly."""
    if qid in TIE_DISPUTED:
        record["tie_rule_disputed"] = TIE_DISPUTED[qid]
        record["grade_with_care"] = "并列规则与兄弟题冲突，评分前需出题方裁决取 hard_cut 还是 keep_ties"
    if qid in IMPOSSIBLE:
        record["arithmetically_impossible"] = IMPOSSIBLE[qid]
        record["grade_with_care"] = "参考答案本身算术不成立，不要据它判分"


def build_396(records: list[dict[str, Any]], connection: Any | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for src in records:
        qid = str(src.get("id"))
        record = dict(src)
        record, trail = _apply_fixes(qid, "nl2sql", record, connection)
        merged = {
            "id": f"W-{qid}",
            "origin_id": qid,
            "source_bank": "nl2sql-396",
            "answer_origin": "mock_executed",
            "as_of": MOCK_AS_OF,
            "grade_mode": "exact_value",
            **{k: v for k, v in record.items() if k != "id"},
        }
        if trail:
            merged["defect_fixed"] = trail
        fix_meta = CONFIRMED_FIXES.get(("nl2sql", qid)) or {}
        if fix_meta.get("override_grade_mode"):
            merged["grade_mode"] = fix_meta["override_grade_mode"]
        _tag(merged, qid)
        out.append(merged)
    return out


_OWNER_NAME_MAP: dict[str, str] | None = None


def _load_owner_name_map(connection: Any) -> dict[str, str]:
    """task.project_owner_id -> project_owner_name (one id, one name)."""
    global _OWNER_NAME_MAP
    if _OWNER_NAME_MAP is None:
        _OWNER_NAME_MAP = {}
        with connection.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT project_owner_id, project_owner_name FROM task WHERE project_owner_id IS NOT NULL"
            )
            for row in cur.fetchall():
                _OWNER_NAME_MAP.setdefault(str(row["project_owner_id"]), str(row["project_owner_name"]))
    return _OWNER_NAME_MAP


def _nameify_owner_gold(record: dict[str, Any], connection: Any | None) -> bool:
    """oa_biz 的 gold 用 owner_user_id, 工作区工具按姓名返回——把答案改成姓名口径。

    同名不同 id 的行合并计数(工具按姓名分组就是合并的)。返回是否发生了改写。
    """
    gold = record.get("gold_answer")
    if not isinstance(gold, dict) or connection is None:
        return False
    columns = gold.get("columns") or []
    rows = gold.get("rows") or []
    if "owner_user_id" not in columns:
        return False
    idx = columns.index("owner_user_id")
    name_map = _load_owner_name_map(connection)
    merged: dict[str, list[str]] = {}
    for row in rows:
        name = name_map.get(str(row[idx]), str(row[idx]))
        if name not in merged:
            merged[name] = [str(v) for v in row]
            merged[name][idx] = name
        else:
            for c in range(len(row)):
                if c != idx and merged[name][c].isdigit() and str(row[c]).isdigit():
                    merged[name][c] = str(int(merged[name][c]) + int(row[c]))
    columns[idx] = "owner_name"
    gold["columns"] = columns
    gold["rows"] = list(merged.values())
    record["gold_row_count"] = len(gold["rows"])
    return True


def build_oa(
    raw: list[dict[str, Any]],
    mock: list[dict[str, Any]],
    connection: Any | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """oa_biz: grade against mock-recomputed answers, keep `expected` for later reconciliation."""
    by_id = {str(r.get("id")): r for r in raw}
    mock_by_id = {str(r.get("id")).replace("OA-", ""): r for r in mock}
    out: list[dict[str, Any]] = []
    dropped: list[tuple[str, str]] = []

    for qid, src in by_id.items():
        missing = tables_used(src.get("gold_sql", "")) - MOCK_TABLES
        if missing:
            dropped.append((qid, "缺表：" + "+".join(sorted(missing))))
            continue
        m = mock_by_id.get(qid)
        if m is None:
            dropped.append((qid, "mock 上无数据（结果为空或 0），构建期已剔除"))
            continue
        record = dict(m)
        record, trail = _apply_fixes(qid, "oa_biz", record, connection)
        nameified = _nameify_owner_gold(record, connection)
        merged = {
            "id": f"OA-{qid}",
            "origin_id": qid,
            "source_bank": "oa_biz-200",
            "answer_origin": "mock_recomputed",
            "as_of": MOCK_AS_OF,
            "grade_mode": "exact_value",
            # 保留真实库那份，供拿到访问权后一键对账。
            "oa_expected": src.get("expected"),
            "oa_expected_as_of": src.get("as_of"),
            "reconcile_note": (
                "oa_expected 按国数真实库快照算出，与本记录的 gold_answer 不可互换："
                "实测 74 道可执行标量题里仅 3 道两者一致，差异系统性（如 B1-01 真实库 81 / mock 128）"
            ),
            **{k: v for k, v in record.items() if k not in {"id", "oa_expected", "snapshot_note"}},
        }
        if trail:
            merged["defect_fixed"] = trail
        if nameified:
            merged["owner_id_nameified"] = True
        fix_meta = CONFIRMED_FIXES.get(("oa_biz", qid)) or {}
        if fix_meta.get("override_grade_mode"):
            merged["grade_mode"] = fix_meta["override_grade_mode"]
        _tag(merged, qid)
        out.append(merged)
    return out, dropped


# g93 的评分方式与另两库根本不同：那两库按值精确比对，这里 60 道是散文。
# 按 grade_mode 分开标注，免得有人拿 exact_value 的判分器去套散文答案。
_G93_GRADE = {
    "fact": "exact_value_in_prose",
    "signal": "assertion_plus_figures",
    "refusal": "refusal_justified",
}

# 核验跑不收敛的题：宁可剔除，也不要留一条没核过的答案冒充已核。
G93_DROPPED: dict[str, str] = {
    "G09": "逐题核验未收敛（跨库取证越查越宽，两次均未出结论），构建期剔除",
}


def build_g93(
    records: list[dict[str, Any]], verification: dict[str, str]
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    out: list[dict[str, Any]] = []
    dropped: list[tuple[str, str]] = []
    for src in records:
        qid = str(src.get("id"))
        if qid in G93_DROPPED:
            dropped.append((qid, G93_DROPPED[qid]))
            continue
        mode = str(src.get("grade_mode") or "fact")
        merged = {
            "id": f"G-{qid}",
            "origin_id": qid,
            "source_bank": "g93",
            # 这一库没有 gold_sql，答案是人写的；verification 才是它的可信度依据。
            "answer_origin": "hand_written",
            "as_of": MOCK_AS_OF,
            "grade_mode": _G93_GRADE.get(mode, "exact_value_in_prose"),
            "g93_grade_mode": mode,
            "verification": verification.get(qid, "unchecked"),
            **{k: v for k, v in src.items() if k not in {"id", "grade_mode"}},
        }
        if merged["verification"] in {"defective", "partly_defective"}:
            merged["grade_with_care"] = "本题参考答案经核验存在错误数字，修正前不要据它判分"
        elif merged["verification"] == "unverifiable":
            merged["grade_with_care"] = "mock 无法判定本题，判分需人工复核"
        elif merged["verification"] == "unchecked":
            # 「没核过」不等于「核过没问题」：这一库无 gold_sql，未核题一律不能当已核用。
            merged["grade_with_care"] = "本题尚未逐条核验，判分前需先核对参考答案里的每个数字"
        out.append(merged)
    return out, dropped


def _load_verification(path: Path) -> dict[str, str]:
    """Read the g93 verification ledger if it exists; absent means unchecked."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="把三个题库合并成一个，并在每条上留可信度出处")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--g93-verification",
        # 台账跟脚本一起进仓：不然换台机器合并出来的 g93 全是 unchecked，可信度无从复现。
        default=str(WORKSPACE / "tests" / "g93-verification.json"),
        help="g93 逐题核验结论（id -> verified/defective/partly_defective/unverifiable）",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="不连库：去掉 LIMIT 的两题只改行数，gold_answer 留标记待重算",
    )
    args = parser.parse_args()

    connection = None
    if not args.no_db:
        try:
            connection = _load_db().connect()
        except Exception as exc:  # 连不上库不该让合并失败，降级成加标记
            print(f"[warn] 连不上 mock 库，去 LIMIT 的题只改行数：{type(exc).__name__}: {exc}"[:160])

    verification = _load_verification(Path(args.g93_verification))
    merged: list[dict[str, Any]] = []
    try:
        merged += build_396(_read(SRC_396), connection)
        oa, dropped = build_oa(_read(SRC_OA_RAW), _read(SRC_OA_MOCK), connection)
    finally:
        if connection is not None:
            connection.close()
    merged += oa
    g93, g93_dropped = build_g93(_read(SRC_G93), verification)
    merged += g93

    out_path = Path(args.out)
    out_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in merged) + "\n",
        encoding="utf-8",
    )

    print(f"合并 {len(merged)} 题 -> {out_path}")
    print()
    for key in ("source_bank", "answer_origin", "grade_mode"):
        counts = collections.Counter(str(item.get(key)) for item in merged)
        print(f"按 {key}：")
        for name, count in counts.most_common():
            print(f"  {name:<24} {count}")
        print()

    fixed = [item for item in merged if item.get("defect_fixed")]
    care = [item for item in merged if item.get("grade_with_care")]
    recompute = [item for item in merged if item.get("gold_answer_needs_recompute")]
    print(f"已按审计修正 {len(fixed)} 题：{', '.join(i['id'] for i in fixed)}")
    print(f"标注需谨慎判分 {len(care)} 题：{', '.join(i['id'] for i in care)}")
    if recompute:
        print(f"\n注意：{len(recompute)} 题去掉 LIMIT 后 gold_answer 已过期，需重算行内容：")
        for item in recompute:
            print(f"  {item['id']:<10} 应为 {item.get('gold_row_count')} 行")
    if not verification:
        print("\n注意：未找到 g93 核验结论，全部标为 unchecked。")
    if g93_dropped:
        print(f"\ng93 剔除 {len(g93_dropped)} 题：")
        for qid, why in g93_dropped:
            print(f"  {qid:<8} {why}")
    still_unchecked = [i["id"] for i in merged if i.get("verification") == "unchecked"]
    if still_unchecked:
        print(f"\n注意：g93 仍有 {len(still_unchecked)} 题未核验：{', '.join(still_unchecked)}")
    print(f"\noa_biz 剔除 {len(dropped)} 题：")
    for qid, why in dropped:
        print(f"  {qid:<8} {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
