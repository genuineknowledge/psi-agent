#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 mentor 卡的真实数据通道：飞书通讯录 + 请假审批 + 全员考勤 + 全员入职时间，
落盘供 build_cards.py 使用。**所有数据都从飞书在线来源现取，脚本内不维护任何
人工数据**（周期窗口也由 --cycle 参数推导，默认今天）。

数据源（均 tenant token，只读；海豚发卡前跑一次即可）：
- 通讯录：GET /open-apis/contact/v3/users/find_by_department（department_id=0 全公司，
          逐页拉 name ↔ user_id ↔ open_id）→ roster.json（考勤/审批/入职查询的底座）
- 请假：GET  /open-apis/approval/v4/instances（近 90 天，approval_code=请假）
        + GET  /open-apis/approval/v4/instances/:instance_id（逐条详情）
- 考勤：POST /open-apis/attendance/v1/user_tasks/query（全公司，周期日往前 6 天 ~ 周期日）
- 入职：GET  /open-apis/contact/v3/users/:user_id（逐个，join_time 字段）

输出：
- mentor_cards/roster.json            全员通讯录（name ↔ user_id ↔ open_id，fetched_at）
- mentor_cards/data/leave.json         请假实例（含申请人姓名/类型/起止/事由/状态）
- mentor_cards/data/attendance.json    全员逐日打卡结果（原始 normalized，含窗口）
- mentor_cards/data/join.json          全员入职时间（name -> join_date，用于
                                       区分「入职前没写」与「该写没写」）

用法：
    python3 mentor_cards/fetch_leave_attendance.py                 # 周期 = 今天
    python3 mentor_cards/fetch_leave_attendance.py --cycle 2026-08-28
依赖：环境变量 PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET；仅标准库。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://open.feishu.cn"
LEAVE_APPROVAL_CODE = "99EEC396-536A-4C7A-8B2D-412584E35CE3"  # 请假
# 考勤窗口由 --cycle 推导（周期日往前 6 天 ~ 周期日，共 7 天），不再写死
ATT_DATE_FROM = 0
ATT_DATE_TO = 0
LEAVE_WINDOW_DAYS = 90  # 近 90 天

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(exist_ok=True)


def _req(method: str, path: str, body: dict | None = None, token: str = "") -> dict:
    url = BASE + path
    data = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 4xx/5xx（如通讯录可见范围受限的 403）：把飞书 body 解出来当 error dict 返回，
        # 让调用点按 code 决定回退，而不是让异常炸穿整个 fetch 流程。
        try:
            payload = json.loads(e.read().decode("utf-8"))
            if isinstance(payload, dict) and "code" in payload:
                return payload
        except (ValueError, OSError):
            pass
        return {"code": e.code, "msg": f"HTTP {e.code} {e.reason}"}


def get_tenant_token() -> str:
    app_id = os.environ.get("PSI_FEISHU_APP_ID", "")
    app_secret = os.environ.get("PSI_FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        print("[err] 缺少 PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET", file=sys.stderr)
        sys.exit(1)
    res = _req("POST", "/open-apis/auth/v3/tenant_access_token/internal",
               {"app_id": app_id, "app_secret": app_secret})
    if res.get("code") != 0:
        print(f"[err] 拿 token 失败: {res}", file=sys.stderr)
        sys.exit(1)
    return res["tenant_access_token"]


def _cycle_to_att_window(cycle: str) -> tuple[int, int]:
    """周期日（YYYY-MM-DD）→ 考勤窗口 (date_from, date_to)（周期日往前 6 天 ~ 周期日）。"""
    cd = datetime.date.fromisoformat(cycle)
    f = cd - datetime.timedelta(days=6)
    return int(f.strftime("%Y%m%d")), int(cd.strftime("%Y%m%d"))


def fetch_roster(token: str) -> None:
    """从飞书通讯录拉全公司成员（department_id=0，逐页），写 roster.json。

    roster 是考勤（employee_id）/ 请假（申请人姓名）/ 入职时间（逐个查询）
    三路的底座，不再手维护。

    **权限安全阀**：本端点受应用「通讯录权限范围」限制（实测 department_id=0
    仅返回被授权的人，可能远少于全公司）。当拉到的成员数显著少于现有
    roster.json（< 现有 50%）时视为权限不足：**保留现有 roster 不覆盖**，
    并告警提示改用海豚 feishu_department_members(recursive=True) 刷新。
    """
    members: list[dict] = []
    page_token = ""
    roster_p = HERE / "roster.json"
    while True:
        q = urllib.parse.urlencode({
            "department_id": "0",
            "page_size": "50",
            "user_id_type": "user_id",
        })
        if page_token:
            q += "&page_token=" + urllib.parse.quote(page_token, safe="")
        res = _req("GET", f"/open-apis/contact/v3/users/find_by_department?{q}", token=token)
        if res.get("code") != 0:
            # 通讯录可见范围受限（40004）等：不 fatal。已有 roster 就保留并继续跑
            # 请假/考勤/入职（它们只需 roster 里的 open_id/user_id，不依赖本次拉取）。
            if roster_p.exists():
                print(f"[warn] 通讯录端点失败（{res.get('code')}: {res.get('msg')}），"
                      f"**保留现有 roster.json 不覆盖**，继续用它跑请假/考勤/入职。"
                      f"如需刷新全员名单，请用海豚 feishu_department_members(recursive=True)。",
                      file=sys.stderr)
                return
            print(f"[err] 通讯录拉取失败且无现有 roster.json 可用: {res}", file=sys.stderr)
            sys.exit(1)
        data = res.get("data", {}) or {}
        for item in data.get("items", []):
            name = (item.get("name") or "").strip()
            uid = item.get("user_id") or ""
            oid = item.get("open_id") or ""
            if name and uid:
                members.append({"user_id": uid, "open_id": oid, "name": name})
        if data.get("has_more") and data.get("page_token"):
            page_token = data["page_token"]
        else:
            break

    cur_count = 0
    if roster_p.exists():
        try:
            cur_count = len(json.loads(roster_p.read_text(encoding="utf-8")).get("members", []))
        except (ValueError, OSError):
            cur_count = 0
    if cur_count > 0 and len(members) < cur_count * 0.5:
        print(f"[warn] 通讯录端点只拉到 {len(members)} 人（现有 roster {cur_count} 人），"
              f"疑似应用通讯录权限范围受限；**保留现有 roster 不覆盖**。"
              f"请用海豚 feishu_department_members(recursive=True) 刷新 roster.json。",
              file=sys.stderr)
        return

    out = {
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "members": members,
    }
    roster_p.write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ok] 通讯录 {len(members)} 人 → roster.json（fetched_at={out['fetched_at']}）")


def fetch_attendance(token: str) -> None:
    roster = json.loads((HERE / "roster.json").read_text(encoding="utf-8"))["members"]
    user_ids = [m["user_id"] for m in roster]
    res = _req(
        "POST",
        "/open-apis/attendance/v1/user_tasks/query"
        + "?employee_type=employee_id&ignore_invalid_users=true",
        {
            "user_ids": user_ids,
            "check_date_from": ATT_DATE_FROM,
            "check_date_to": ATT_DATE_TO,
            "need_overtime_result": False,
        },
        token,
    )
    if res.get("code") != 0:
        print(f"[err] 考勤查询失败: {res}", file=sys.stderr)
        sys.exit(1)
    data = res.get("data", {})
    results = []
    for r in data.get("user_task_results", []):
        for rec in r.get("records", []):
            cin = rec.get("check_in_record", {}) or {}
            cout = rec.get("check_out_record", {}) or {}
            def _t(rec_):
                ts = rec_.get("check_time")
                if not ts:
                    return ""
                try:
                    return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError, OverflowError):
                    return str(ts)
            results.append({
                "user_id": r.get("user_id", ""),
                "name": r.get("employee_name", ""),
                "day": r.get("day", ""),
                "check_in_time": _t(cin),
                "check_in_result": rec.get("check_in_result", ""),
                "check_in_location": cin.get("location_name", ""),
                "check_out_time": _t(cout),
                "check_out_result": rec.get("check_out_result", ""),
                "check_out_location": cout.get("location_name", ""),
            })
    out = {
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "date_from": str(ATT_DATE_FROM),
        "date_to": str(ATT_DATE_TO),
        "invalid_user_ids": data.get("invalid_user_ids", []),
        "unauthorized_user_ids": data.get("unauthorized_user_ids", []),
        "results": results,
    }
    (DATA_DIR / "attendance.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ok] 考勤 {len(results)} 条 → data/attendance.json "
          f"（invalid={out['invalid_user_ids']} unauthorized={out['unauthorized_user_ids']}）")


def fetch_leave(token: str, cycle: str) -> None:
    end = datetime.datetime.strptime(cycle, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59)
    start = end - datetime.timedelta(days=LEAVE_WINDOW_DAYS)
    q = urllib.parse.urlencode({
        "approval_code": LEAVE_APPROVAL_CODE,
        "start_time": str(int(start.timestamp() * 1000)),
        "end_time": str(int(end.timestamp() * 1000)),
        "page_size": "100",
    })
    res = _req("GET", f"/open-apis/approval/v4/instances?{q}", token=token)
    if res.get("code") != 0:
        print(f"[err] 请假实例列表失败: {res}", file=sys.stderr)
        sys.exit(1)
    codes = res.get("data", {}).get("instance_code_list", [])
    roster = {m["user_id"]: m["name"] for m in
              json.loads((HERE / "roster.json").read_text(encoding="utf-8"))["members"]}

    entries = []
    for code in codes:
        det = _req("GET", f"/open-apis/approval/v4/instances/{code}",
                   token=token)
        if det.get("code") != 0:
            print(f"[warn] 详情失败 {code}: {det.get('msg')}", file=sys.stderr)
            continue
        d = det.get("data", {})
        applicant = d.get("user_id", "")
        form = d.get("form", "") or ""
        val = None
        try:
            widgets = json.loads(form) if isinstance(form, str) else (form or [])
            for w in widgets:
                v = w.get("value")
                if isinstance(v, dict) and ("start" in v or "name" in v):
                    val = v
                    break
        except (ValueError, TypeError):
            val = None
        entries.append({
            "instance_code": code,
            "user_id": applicant,
            "name": roster.get(applicant, applicant),
            "status": d.get("status", ""),
            "leave_type": (val or {}).get("name", ""),
            "start": (val or {}).get("start", ""),
            "end": (val or {}).get("end", ""),
            "unit": (val or {}).get("unit", ""),
            "interval": (val or {}).get("interval", ""),
            "reason": (val or {}).get("reason", ""),
        })

    out = {
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "approval_code": LEAVE_APPROVAL_CODE,
        "window": [start.isoformat(), end.isoformat()],
        "entries": entries,
    }
    (DATA_DIR / "leave.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ok] 请假 {len(entries)} 条 → data/leave.json")


def fetch_join_dates(token: str) -> None:
    """逐个拉全员入职时间（contact/users/:user_id 的 join_time，秒级时间戳）。

    注意：飞书后台未填过入职日期的人会返回默认值（实测 2026-03-01 占 13 人），
    用的时候注意甄别；「未来日期」（如 08-31/09-02）是预填的待入职时间。
    """
    roster = json.loads((HERE / "roster.json").read_text(encoding="utf-8"))["members"]
    people: dict[str, dict] = {}
    for m in roster:
        r = _req("GET", f"/open-apis/contact/v3/users/{m['user_id']}?user_id_type=user_id",
                 token=token)
        if r.get("code") != 0:
            print(f"[warn] 拉 {m['name']} 入职时间失败: {r.get('msg')}", file=sys.stderr)
            continue
        u = r.get("data", {}).get("user", {}) or {}
        jt = u.get("join_time")
        jd = ""
        if jt:
            try:
                jd = datetime.datetime.fromtimestamp(int(jt)).strftime("%Y-%m-%d")
            except (ValueError, OSError, OverflowError):
                jd = str(jt)
        people[m["name"]] = {"join_ts": jt, "join_date": jd}
    out = {
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "people": people,
    }
    (DATA_DIR / "join.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ok] 入职时间 {len(people)} 人 → data/join.json")


def main() -> int:
    ap = argparse.ArgumentParser(description="拉取 mentor 卡真实数据（通讯录/请假/考勤/入职）")
    ap.add_argument("--cycle", default=datetime.date.today().isoformat(),
                    help="周期日 YYYY-MM-DD（默认今天）；考勤/请假窗口由其推导")
    args = ap.parse_args()

    global ATT_DATE_FROM, ATT_DATE_TO
    ATT_DATE_FROM, ATT_DATE_TO = _cycle_to_att_window(args.cycle)

    token = get_tenant_token()
    fetch_roster(token)          # 通讯录 → roster.json（其余三路的底座）
    fetch_attendance(token)      # 考勤（周期窗口由 --cycle 推导）
    fetch_leave(token, args.cycle)  # 请假（近 90 天，截至周期日）
    fetch_join_dates(token)      # 入职时间（逐个查询通讯录）
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
