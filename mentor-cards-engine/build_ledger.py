#!/usr/bin/env python3
"""TODO 台账（多维表格）构建脚本 —— 把 TODO LIST 本周期填报数据解析为台账行。

用途：mentor/boss 卡片底部「报表」入口指向的多维表格（bitable）数据源。
数据流：todo_list_parsed.json（解析后的 TODO LIST）→ 本脚本解析本周期列正文
→ data/ledger_<cycle>.json（台账行），再由海豚用 feishu_bitable_create_records 灌入。

字段对齐契约 0.6 节 _LEDGER_SCHEMA_FIELDS（15 字段）：
周期日期 / 负责人 / mentor / 层级 / 父项 / 标题 / 截止日期 / 状态 /
闭环五要素 / mentor打分 / mentor评语 / 外部成果 / 友商对比 / 任务GUID / 填报原文

只解析「最新周期列」的填报正文；mentor 打分/评语/外部成果/友商对比 为 mentor
后续在台账内填写字段，导入时留空。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARSED = ROOT / "todo_list_parsed.json"
OUT_DIR = Path(__file__).resolve().parent / "data"

# 周期由数据源推导（todo_list_parsed.json 最新列），main() 里 global 赋值
CYCLE = ""
CYCLE_DATE = ""


def _cycle_date(col: str) -> str:
    """周期列名（'7.24'）→ ISO 日期 'YYYY-MM-DD'（按运行年份解析）。"""
    import re
    m = re.match(r"(\d{1,2})\.(\d{1,2})", col)
    if not m:
        return ""
    from datetime import date
    return f"{date.today().year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

# 段落标题（大小写不敏感）；容忍 Markdown 前缀（# / - / - -）；
# 「子目标1：xxx」「大目标一：xxx」这类带序号的小节头也命中
SECTION_HEAD = re.compile(
    r"^\s*(?:#+\s*)?(?:[-•]\s*)*(大目标|小目标|子目标|todo|to\s*do|todolist|待办|长期任务|其他)\s*"
    r"(\d+|[一二三四五六七八九十]+)?\s*([：:])?\s*(.*)$",
    re.IGNORECASE)

# 条目行：行首编号（1、1. 1.2 1)）或项目符号（- •）
ITEM_LINE = re.compile(r"^\s*(?:\d+(?:\.\d+)*[、.)]|[-•])\s*(.*)$")

# 标签行（标准/时间/闭环/…）：是条目的说明细节，不当作新条目
LABEL_RE = re.compile(
    r"^\s*(标准|时间|闭环|给用户的价值|用户价值|价值|备注|风险|验收人|"
    r"服务用户|可能用户|壁垒|复盘|做法|目标|关联小目标|W|H|A|T)[：:]")

# 行内 @人 提取（2-3 字名；避免把「时间」的时/间并入）
AT_RE = re.compile(r"@([\u4e00-\u9fa5·]{2,3})(?!间|时)")


def strip_bullets(s: str) -> str:
    """剥掉行首的 Markdown 前缀（#、-、•、空白），露出真实内容。"""
    return re.sub(r"^[\s#\-•]+", "", s)

# 截止时间提取：时间：xxx 优先，其次 (xxx)
DEADLINE_TIME = re.compile(r"时间[:：]\s*([^ \t\n@，,。；;（）()]{1,20})")
DEADLINE_PAREN = re.compile(r"[（(]([^（）()]{1,24})[）)]")
DEADLINE_DATE = re.compile(r"^[\d./年月日\s上下晚-]{1,16}(前|晚前)?$")

# 状态标记
ST_DONE = re.compile(r"✅|已完成|已闭环|完成$|已交付")
ST_DOING = re.compile(r"🔄|进行中|推进|开发中|迭代|优化|撰写|实施|调研")
ST_OVERDUE = re.compile(r"⚠️|延后|逾期|未完成|超时|推迟")


def sec_name(header: str) -> str:
    h = header.lower().replace(" ", "")
    if h.startswith("大目标"):
        return "大目标"
    if h.startswith(("小目标", "子目标")):
        return "子目标"
    if h.startswith(("todo", "todolist", "待办")):
        return "TODO"
    if h.startswith(("长期任务", "其他")):
        return "其他"
    return "其他"


def infer_status(text: str) -> str:
    if ST_OVERDUE.search(text):
        return "逾期"
    if ST_DONE.search(text):
        return "已闭环"
    if ST_DOING.search(text):
        return "进行中"
    return "未开始"


def find_deadline(text: str) -> str:
    for m in DEADLINE_TIME.finditer(text):
        hit = m.group(1).strip()
        if hit:
            return hit[:24]
    for m in DEADLINE_PAREN.finditer(text):
        hit = m.group(1).strip()
        base = hit
        for suf in ("前", "晚前"):
            if base.endswith(suf):
                base = base[: -len(suf)]
        if hit == "待定" or DEADLINE_DATE.match(base):
            return hit[:24]
    m = re.search(r"(\d{1,2}[./-]\d{1,2})\s*(之前|前|晚前|下午|上午)", text)
    if m:
        return m.group(1) + m.group(2)
    return ""


def clean_title(line: str) -> str:
    t = line.strip()
    t = re.sub(r"\s*[（(](?:时间|标准|目标)[：:].*$", "", t).strip()
    return t[:120]


def parse_person(name: str, mentor: str, text: str) -> list[dict]:
    """把一个人本周期列的正文解析为台账行（含顶层条目 + TODO 下的直接子项）。"""
    rows: list[dict] = []
    lines = text.splitlines()

    cur_sec = "其他"
    top_items: list[dict] = []  # 当前段的顶层条目
    last_top: dict | None = None
    # 保存段落原始行，供 填报原文 回填
    raw_blocks: list[list[str]] = []

    def close_top():
        nonlocal last_top
        if last_top is not None:
            top_items.append(last_top)
        last_top = None

    i = 0
    while i < len(lines):
        line = lines[i]
        s = SECTION_HEAD.match(line)
        if s:
            close_top()
            cur_sec = sec_name(s.group(1))
            title_rest = (s.group(4) or "").strip()
            raw_blocks.append([line])
            has_numeral = bool(s.group(2))
            has_colon = bool(s.group(3))
            if has_numeral or (has_colon and title_rest and not ITEM_LINE.match(title_rest)
                               and not LABEL_RE.match(title_rest)):
                # 带编号的小节头（子目标1：xxx）或标题随行（大目标：xxx）→ 本身就是一条条目
                t = title_rest or f"{cur_sec}{s.group(2)}"
                last_top = {
                    "section": cur_sec, "title": clean_title(t), "lines": [line],
                    "sub": [], "is_subheader": True,
                }
            i += 1
            continue
        m = ITEM_LINE.match(line)
        if m and not LABEL_RE.match(strip_bullets(m.group(1))):
            close_top()
            title = clean_title(m.group(1) or "")
            last_top = {"section": cur_sec, "title": title, "lines": [line],
                        "sub": [], "is_subheader": False}
            i += 1
            continue
        # 普通行：归入当前条目 / 作为当前段的隐式条目 / 或段内散行
        if last_top is not None:
            stripped = line.strip()
            if stripped:
                # TODO 段下更深缩进的子项（- 且缩进大于条目本身）→ 子项
                sub = ITEM_LINE.match(line)
                indent = len(line) - len(line.lstrip())
                if (sub and not LABEL_RE.match(strip_bullets(sub.group(1)))
                        and last_top["section"] == "TODO"
                        and (indent > 0 or last_top["is_subheader"])):
                    last_top["sub"].append(clean_title(sub.group(1) or ""))
                    last_top["lines"].append(line)
                else:
                    last_top["lines"].append(line)
            i += 1
            continue
        # 无打开条目：标签行 → 段内散行；其它非空行 → 隐式条目（无编号的散文式填报）
        stripped = line.strip()
        if stripped and not LABEL_RE.match(line):
            last_top = {"section": cur_sec, "title": clean_title(stripped),
                        "lines": [line], "sub": [], "is_subheader": False}
        elif stripped and raw_blocks:
            raw_blocks[-1].append(line)
        i += 1
    close_top()

    # 生成行；「其他」段过滤噪声（如填报开头「8.28 TODO」「Agent 开发」这类题头）
    for t in top_items:
        if t["section"] == "其他":
            title0 = t["title"].strip()
            if (not title0 or re.fullmatch(r"[\d./\s]*todo", title0, re.I)
                    or re.fullmatch(r"\d{1,3}", title0)
                    or (len(title0) <= 8 and not re.search(r"\d|@|×|（|\(|、|：|:", title0))):
                continue
        full = "\n".join(t["lines"])
        owner_at = AT_RE.findall(full)
        owner = "、".join(dict.fromkeys(owner_at)) if owner_at else name
        base = {
            "周期日期": CYCLE_DATE,
            "负责人": owner,
            "mentor": mentor or "",
            "层级": t["section"],
            "父项": "",
            "标题": t["title"],
            "截止日期": find_deadline(full),
            "状态": infer_status(full),
            "闭环五要素": full[:2000],
            "任务GUID": f"{CYCLE}_{name}_{t['section']}_{len(rows) + 1}",
            "填报原文": text[:3000],
        }
        if t["sub"]:
            for j, sub_title in enumerate(t["sub"], 1):
                sub_text = sub_title
                sub_owner_at = AT_RE.findall(sub_title)
                sub_owner = "、".join(dict.fromkeys(sub_owner_at)) if sub_owner_at else owner
                rows.append({
                    **base,
                    "层级": "子任务",
                    "父项": t["title"],
                    "标题": sub_title[:120],
                    "截止日期": find_deadline(sub_title) or base["截止日期"],
                    "状态": infer_status(sub_title),
                    "任务GUID": f"{CYCLE}_{name}_{t['section']}_{len(rows) + 1}.{j}",
                    "闭环五要素": sub_text[:2000],
                })
        rows.append(base)
    return rows


def main() -> int:
    global CYCLE, CYCLE_DATE
    if not PARSED.exists():
        print(f"[err] 缺 {PARSED}", file=sys.stderr)
        return 1
    data = json.loads(PARSED.read_text(encoding="utf-8"))
    date_cols = data["date_cols"]
    if not date_cols:
        print("[err] 数据源无周期列", file=sys.stderr)
        return 1
    CYCLE = date_cols[-1]                # 最新周期 = 数据源推导
    CYCLE_DATE = _cycle_date(CYCLE)      # 周期日（ISO）
    people = data["people"]
    all_rows: list[dict] = []
    filled = 0
    for p in people:
        text = p["cols"].get(CYCLE, "")
        if not text.strip():
            continue
        filled += 1
        all_rows.extend(parse_person(p["name"], p.get("mentor") or "", text))
    OUT_DIR.mkdir(exist_ok=True)
    out = {"cycle": CYCLE, "cycle_date": CYCLE_DATE, "filled": filled,
           "total_people": len(people), "rows": all_rows}
    dest = OUT_DIR / f"ledger_{CYCLE}.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ok] 解析 {filled}/{len(people)} 人本周期（{CYCLE}）填报 → {len(all_rows)} 行 → {dest}")
    # 层级分布
    from collections import Counter
    c = Counter(r["层级"] for r in all_rows)
    print(f"[stat] 层级分布：{dict(c)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
