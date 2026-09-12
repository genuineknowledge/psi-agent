"""feishu_sheet_strike_read 结构钉 —— 删除线(验收标记)读取:列字母、strike 解析、定位。"""

from __future__ import annotations

import importlib
import io
import json
import zipfile
from pathlib import Path


def test_col_letter_conversion() -> None:
    f = importlib.import_module("_feishu_impl")
    assert f._col_letter(0) == "A"
    assert f._col_letter(21) == "V"  # 日期列常到 V/W
    assert f._col_letter(25) == "Z"
    assert f._col_letter(26) == "AA"


def test_find_col_exact_then_substring() -> None:
    f = importlib.import_module("_feishu_impl")
    header = ["任务负责人", "mentor", "9.4", "9.7"]
    assert f._find_col(header, ("负责人", "姓名")) == 0
    assert f._find_col(header, ("9.4",)) == 2
    assert f._find_col(header, ("没有的列",)) == -1


def _make_xlsx(cell_xml: str, shared_strings: str = "") -> Path:
    """Build a minimal xlsx with one worksheet containing cell_xml."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        sheet = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            f'<row r="23">{cell_xml}</row>'
            "</sheetData></worksheet>"
        )
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        if shared_strings:
            sst = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"{shared_strings}</sst>"
            )
            z.writestr("xl/sharedStrings.xml", sst)
    buf.seek(0)
    out = Path("strike-test.xlsx")
    out.write_bytes(buf.read())
    return out


def test_parse_cell_strikes_mixed_runs() -> None:
    f = importlib.import_module("_feishu_impl")
    cell = (
        '<c r="V23" t="inlineStr">'
        "<is>"
        "<r><t>大目标/小目标</t></r>"
        '<r><rPr><strike val="1"/></rPr><t>已验收的 TODO</t></r>'
        "<r><t>持续任务</t></r>"
        "</is></c>"
    )
    xlsx = _make_xlsx(cell)
    runs = f._parse_cell_strikes(xlsx, "V23")
    xlsx.unlink()
    assert runs is not None
    assert [r["strike"] for r in runs] == [False, True, False]
    assert runs[1]["text"] == "已验收的 TODO"


def test_parse_cell_strikes_absent_cell() -> None:
    f = importlib.import_module("_feishu_impl")
    xlsx = _make_xlsx('<c r="V24" t="inlineStr"><is><r><t>x</t></r></is></c>')
    assert f._parse_cell_strikes(xlsx, "V23") is None
    xlsx.unlink()


def test_parse_cell_strikes_shared_strings() -> None:
    """导出 xlsx 走 sharedStrings:单元格 <v> 是 si 索引,strike 在 si 的 run 上。"""
    f = importlib.import_module("_feishu_impl")
    cell = '<c r="V23" t="s"><v>1</v></c>'
    sst = (
        "<si><t>plain</t></si>"
        "<si>"
        "<r><t>大目标:&#xA;</t></r>"
        "<r><rPr><strike/></rPr><t>已验收的 TODO</t></r>"
        "<r><t>持续任务</t></r>"
        "</si>"
    )
    xlsx = _make_xlsx(cell, shared_strings=sst)
    runs = f._parse_cell_strikes(xlsx, "V23")
    xlsx.unlink()
    assert runs is not None
    assert [r["strike"] for r in runs] == [False, True, False]
    assert runs[1]["text"] == "已验收的 TODO"
    assert runs[0]["text"] == "大目标:\n"  # &#xA; 数字实体解回换行


def test_norm_name_strips_at_prefix() -> None:
    f = importlib.import_module("_feishu_impl")
    assert f._norm_name("@赵胜迪") == "赵胜迪"
    assert f._norm_name(" 赵胜迪 ") == "赵胜迪"
    assert f._norm_name("@赵胜迪") == f._norm_name("赵胜迪")


def test_tool_rejects_missing_args() -> None:
    f = importlib.import_module("_feishu_impl")
    for msg in (
        "board_link is required.",
        "person_name is required.",
        "cycle_date is required (the column header, e.g. 9.4).",
    ):
        err = json.loads(f.dumps_result(f._error(msg)))
        assert err["ok"] is False
