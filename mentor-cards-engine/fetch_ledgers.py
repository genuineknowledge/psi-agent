#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取每个 mentor 的台账多维表格（bitable）记录 → data/ledger_<mentor>.json。

数据源：mentor_cards/ledger_sources.json（每个 mentor 一个台账 base 的地址清单）。
通用逻辑（马晨柯 2026-08-28 定）：每周期发卡前，海豚按来源清单逐个台账
「现取」记录并归一化，卡片 ②目标数量/③完成情况/④逾期明细/⑥评价概况 以台账
为权威来源；未注册台账的组回落人工核对档案。

台账 schema（孙逊组实测，人工维护；各 mentor 台账结构相同）：
  周期日期 / 负责人 / mentor / 层级(大目标1·小目标1-4·todo1-3) / 父项 / 标题 /
  截止日期 / 状态(待开始·进行中·已交付·请假顺延) / 闭环五要素 /
  mentor打分 / agent建议分 / mentor评语 / 外部成果 / 友商对比 / 任务GUID

归一化（所有 mentor 台账统一字段，供 build_cards.py 消费）：
  cycle(周期日期 ISO) / owners(负责人姓名) / mentors / level(层级) /
  parent / title / due(截止日期 ISO) / status(状态) / five(闭环五要素) /
  score(mentor打分 float) / agent_score / comment(mentor评语) /
  external(外部成果) / comparison(友商对比) / guid(任务GUID) / table(来源表名)

用法：
    python3 mentor_cards/fetch_ledgers.py
依赖：环境变量 PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET；仅标准库。
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://open.feishu.cn"
HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(exist_ok=True)
TZ = datetime.timezone(datetime.timedelta(hours=8))  # 飞书日期字段按本地时区解释


def _req(method: str, path: str, body: dict | None = None, token: str = "") -> dict:
    url = BASE + path
    data = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


def _to_iso(ms) -> str:
    """飞书日期字段（毫秒时间戳）→ 'YYYY-MM-DD'（UTC+8）。字符串/数字都兼容。"""
    if ms in (None, ""):
        return ""
    if isinstance(ms, str):
        s = ms.strip()
        if not s:
            return ""
        if s.isdigit():
            ms = int(s)
        else:
            return s[:10]
    if isinstance(ms, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(ms / 1000, tz=TZ).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return str(ms)
    return str(ms)


def _names(v) -> str:
    """负责人/mentor 人员字段（user 数组或姓名串）→ '、'.join 的姓名。"""
    if v is None:
        return ""
    if isinstance(v, list):
        out = []
        for u in v:
            if isinstance(u, dict):
                out.append(u.get("name") or u.get("en_name") or "")
            else:
                out.append(str(u))
        return "、".join(x for x in out if x)
    if isinstance(v, str):
        return v
    return str(v)


def _num(v):
    """打分数字字段（number 或 '4' 字符串）→ float / None。"""
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def norm_level(name: str) -> str:
    """层级选项（大目标1/小目标3/todo2）→ 大类（大目标/小目标/TODO/其他）。"""
    s = (name or "").strip().lower()
    if s.startswith("大目标") or s.startswith("big goal"):
        return "大目标"
    if s.startswith(("小目标", "子目标")):
        return "小目标"
    if s.startswith("todo"):
        return "TODO"
    if s.startswith("其他"):
        return "其他"
    return s or "其他"


def fetch_table(token: str, app_token: str, table_id: str, table_name: str) -> list[dict]:
    """拉一个表的全部记录，归一化字段。"""
    rows: list[dict] = []
    page_token = ""
    while True:
        q = urllib.parse.urlencode({
            "page_size": "500",
            "page_token": page_token,
        }) if page_token else urllib.parse.urlencode({"page_size": "500"})
        res = _req(
            "GET",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?{q}",
            token=token)
        if res.get("code") != 0:
            print(f"[err] 拉台账 {app_token}/{table_id} 失败: {res}", file=sys.stderr)
            break
        data = res.get("data", {}) or {}
        for item in data.get("items", []) or []:
            f = item.get("fields", {}) or {}
            rows.append({
                "cycle": _to_iso(f.get("周期日期")),
                "owners": _names(f.get("负责人")),
                "mentors": _names(f.get("mentor")),
                "level": norm_level(f.get("层级")),
                "level_raw": (f.get("层级") or ""),
                "parent": (f.get("父项") or ""),
                "title": (f.get("标题") or "").strip(),
                "due": _to_iso(f.get("截止日期")),
                "status": (f.get("状态") or "").strip(),
                "five": (f.get("闭环五要素") or ""),
                "score": _num(f.get("mentor打分")),
                "agent_score": _num(f.get("agent建议分")),
                "comment": (f.get("mentor评语") or "").strip(),
                "external": (f.get("外部成果") or ""),
                "comparison": (f.get("友商对比") or ""),
                "guid": (f.get("任务GUID") or "").strip(),
                "record_id": item.get("record_id", ""),
                "table": table_name,
            })
        if data.get("has_more") and data.get("page_token"):
            page_token = data["page_token"]
        else:
            break
    return rows


def fetch_ledger_for(mentor: str, src: dict, token: str, save: bool = True) -> dict:
    """现场拉取一个 mentor 的台账（归一化）→ ledger dict。

    src = ledger_sources.json 里的 {name,url,app_token,tables}。
    save=True 时顺手落盘 data/ledger_<mentor>.json 作为审计副本 / 断网回退，
    但调用方（build_cards.load_ledger）消费的是本函数返回的实时数据。
    """
    app_token = src["app_token"]
    all_rows: list[dict] = []
    for table_id in src["tables"]:
        try:
            tinfo = _req("GET",
                         f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}",
                         token=token)
            tname = ((tinfo.get("data") or {}).get("table", {}) or {}).get("name", table_id)
        except Exception:
            tname = table_id
        rows = fetch_table(token, app_token, table_id, tname)
        print(f"[ok]   {mentor} · 表 {tname}: {len(rows)} 行")
        all_rows.extend(rows)
    cycles = sorted({r["cycle"] for r in all_rows if r["cycle"]})
    latest_cycle = cycles[-1] if cycles else ""
    out = {
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": {"name": src.get("name", ""), "url": src.get("url", ""),
                   "app_token": app_token, "tables": src["tables"]},
        "latest_cycle": latest_cycle,
        "cycles": cycles,
        "rows": all_rows,
    }
    if save:
        dest = DATA_DIR / f"ledger_{mentor}.json"
        dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[ok] {mentor} 台账 {len(all_rows)} 行，最新周期 {latest_cycle or '（无周期日期）'} "
              f"→ {dest}")
    return out


def main() -> int:
    src_path = HERE / "ledger_sources.json"
    if not src_path.exists():
        print(f"[err] 缺 {src_path}（台账来源清单）", file=sys.stderr)
        return 1
    sources = json.loads(src_path.read_text(encoding="utf-8"))

    token = get_tenant_token()
    registered = {m: s for m, s in sources.items() if m and not m.startswith("_")
                  and s.get("app_token") and s.get("tables")}
    if not registered:
        print("[warn] ledger_sources.json 中没有已注册台账，无数据可拉（卡片回落人工核对口径）",
              file=sys.stderr)
        return 0

    for mentor, src in sorted(registered.items()):
        fetch_ledger_for(mentor, src, token, save=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
