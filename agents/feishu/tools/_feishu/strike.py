"""Sheet strikethrough impl — 导出 xlsx 读单元格富文本 strike(删除线验收标记)。"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest


def _build_wiki_get_node_request(token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/wiki/v2/spaces/get_node"
    req.add_query("token", token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _norm_name(cell: str) -> str:
    """看板人名列常带 ``@`` 前缀(如 ``@赵胜迪``),匹配前两边都剥掉。"""
    return cell.strip().lstrip("@").strip()


def _find_col(header: list[str], candidates: tuple[str, ...]) -> int:
    for i, cell in enumerate(header):
        if cell in candidates:
            return i
    for i, cell in enumerate(header):
        for cand in candidates:
            if cand and cand in cell:
                return i
    return -1


def _col_letter(idx: int) -> str:
    """0-based column index → spreadsheet column letter (0→A, 25→Z, 26→AA)."""
    letters = ""
    n = idx + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


async def sheet_strike_read_impl(
    board_link: str, person_name: str, cycle_date: str, user_key: str = ""
) -> dict[str, Any]:
    """Locate the cell (person x cycle column), export xlsx, parse its run strikes."""
    import _feishu_impl as _core  # noqa: PLC0415

    from _feishu.drive import export_doc_impl  # noqa: PLC0415
    from _feishu.sheet import read_sheet_grid_impl  # noqa: PLC0415

    # 1. wiki → obj_token
    wiki_token = board_link.rstrip("/").split("/")[-1]
    res = await _core._invoke(_build_wiki_get_node_request(wiki_token), user_key=user_key)
    if not res.get("ok"):
        return res
    node = res.get("data", {}).get("node", {}) if isinstance(res.get("data"), dict) else {}
    obj_token = node.get("obj_token", "")
    if not obj_token:
        return _core._error(f"wiki get_node 没拿到 obj_token: {board_link}")

    # 2. 定位:表头(限列单行)、人名列、周期列,再窄列分页找该人行。
    #    全宽整表读会被行边界字符预算截成两三行,找人行必须限列 + 分页。
    header_grid = await read_sheet_grid_impl(obj_token, range_="!A1:AZ1", max_rows=1, user_key=user_key)
    if not header_grid.get("ok"):
        return header_grid
    header_rows = header_grid.get("rows", []) or header_grid.get("values", []) or []
    if not header_rows:
        return _core._error("看板表读不到内容。")
    header = [str(c).strip() for c in header_rows[0]]
    person_col = _find_col(header, ("负责人", "人", "姓名"))
    if person_col < 0:
        return _core._error(f"人名列认不出来,表头: {header[:8]}")
    date_col = _find_col(header, (cycle_date,))
    if date_col < 0:
        return _core._error(f"周期列 {cycle_date!r} 在表头里找不到: {header[:12]}")
    row_no = -1
    start_row = 2  # 表头占第 1 行
    for _ in range(8):  # 人名列窄列分页,最多翻 8 页(400 行)
        grid = await read_sheet_grid_impl(
            obj_token, range_="!A1:B400", max_rows=50, start_row=start_row, user_key=user_key
        )
        if not grid.get("ok"):
            return grid
        rows = grid.get("rows", []) or grid.get("values", []) or []
        for i, row in enumerate(rows):
            if len(row) > person_col and _norm_name(str(row[person_col])) == _norm_name(person_name):
                row_no = start_row + i
                break
        if row_no > 0 or not grid.get("has_more"):
            break
        start_row = grid.get("next_start_row") or start_row + 1
    if row_no < 0:
        return _core._error(f"人名 {person_name!r} 在看板里找不到。")

    cell_ref = f"{_col_letter(date_col)}{row_no}"

    # 3. 导出 xlsx(临时文件)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "board.xlsx"
        exported = await export_doc_impl(obj_token, "sheet", "xlsx", str(out), "", user_key)
        if not exported.get("ok"):
            return exported
        runs = _parse_cell_strikes(out, cell_ref)
        if runs is None:
            return _core._error(f"xlsx 里找不到单元格 {cell_ref}(导出结构异常,报告为查询失败)。")

    struck = sum(1 for r in runs if r["strike"])
    return {
        "ok": True,
        "person": person_name,
        "cycle_date": cycle_date,
        "cell": cell_ref,
        "runs": runs,
        "struck_runs": struck,
        "total_runs": len(runs),
    }


def _parse_cell_strikes(xlsx_path: Path, cell_ref: str) -> list[dict[str, Any]] | None:
    """Parse one cell's rich-text runs from xlsx XML; returns runs or None if cell absent.

    导出 xlsx 把文本放进 ``xl/sharedStrings.xml`` 共享字符串表,单元格 ``<v>`` 只是
    si 索引;富文本的删除线(rPr/strike)在 si 的 run 上,不在单元格节点里。
    """
    with zipfile.ZipFile(xlsx_path) as z:
        names = [n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
        if not names:
            return None
        # 第一个 sheet 优先
        names.sort(key=_sheet_no)
        xml = z.read(names[0]).decode("utf-8", errors="replace")
        shared: list[list[dict[str, Any]]] | None = None
        if "xl/sharedStrings.xml" in z.namelist():
            sst = z.read("xl/sharedStrings.xml").decode("utf-8", errors="replace")
            shared = [_si_runs(si) for si in re.findall(r"<si>(.*?)</si>", sst, flags=re.S)]

    # 定位单元格节点 <c r="V23" ...> ... </c>
    m = re.search(rf'<c r="{cell_ref}"[^>]*>.*?</c>', xml, flags=re.S)
    if not m:
        return None
    cell_xml = m.group(0)
    t_attr = re.search(r'<c r="[A-Z]+\d+"[^>]*t="([^"]+)"', cell_xml)
    t_kind = t_attr.group(1) if t_attr else ""
    if t_kind == "s" and shared is not None:
        # 共享字符串:单元格值是 si 索引
        vm = re.search(r"<v[^>]*>(.*?)</v>", cell_xml, flags=re.S)
        idx = int(vm.group(1)) if vm else -1
        if 0 <= idx < len(shared):
            return shared[idx]
    runs: list[dict[str, Any]] = []
    # 富文本 run: <r><rPr><strike .../></rPr><t>text</t></r>
    for rm in re.finditer(r"<r>(.*?)</r>", cell_xml, flags=re.S):
        run_xml = rm.group(1)
        strike = "<strike" in run_xml
        tm = re.search(r"<t[^>]*>(.*?)</t>", run_xml, flags=re.S)
        text = tm.group(1) if tm else ""
        runs.append({"text": _xml_unescape(text), "strike": strike})
    if not runs:
        # 无 run 结构的普通单元格:整体无删除线
        tm = re.search(r"<t[^>]*>(.*?)</t>", cell_xml, flags=re.S)
        if not tm:
            tm = re.search(r"<v[^>]*>(.*?)</v>", cell_xml, flags=re.S)
        runs.append({"text": _xml_unescape(tm.group(1)) if tm else "", "strike": False})
    return runs


def _sheet_no(name: str) -> int:
    """``xl/worksheets/sheet<N>.xml`` → N;列名已按 sheet 模式过滤,兜底 0。"""
    m = re.search(r"sheet(\d+)", name)
    return int(m.group(1)) if m else 0


def _si_runs(si_xml: str) -> list[dict[str, Any]]:
    """Parse one ``<si>`` shared-string entry into runs with strike flags."""
    runs: list[dict[str, Any]] = []
    for rm in re.finditer(r"<r>(.*?)</r>", si_xml, flags=re.S):
        run_xml = rm.group(1)
        strike = "<strike" in run_xml
        tm = re.search(r"<t[^>]*>(.*?)</t>", run_xml, flags=re.S)
        text = tm.group(1) if tm else ""
        runs.append({"text": _xml_unescape(text), "strike": strike})
    if not runs:
        tm = re.search(r"<t[^>]*>(.*?)</t>", si_xml, flags=re.S)
        runs.append({"text": _xml_unescape(tm.group(1)) if tm else "", "strike": False})
    return runs


def _xml_unescape(text: str) -> str:
    out = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )
    # 数字实体:&#xA; / &#10; / &#x0A;
    return re.sub(
        r"&#(x[0-9a-fA-F]+|\d+);",
        lambda m: chr(int(m.group(1)[1:], 16)) if m.group(1).startswith("x") else chr(int(m.group(1))),
        out,
    )
