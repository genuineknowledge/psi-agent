"""P1-1 weekly summary report: fetch via MCP, build a Word document.

The report is *generated material*, not a chat transcript (plan 5.4):
deterministic fetch of aggregated calibers from the取数 service, laid out
as a leader-style summary, exported as .docx via python-docx (the
haitun-workspace write_word pattern, with w:eastAsia set so Chinese renders
evenly).

The BFF talks to the MCP service directly with its own token — the report
never depends on a model turn, so it is stable and cheap to regenerate.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import anyio
import httpx
from docx import Document
from docx.shared import Pt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .config import BffConfig


@dataclass
class WeeklySummaryData:
    snapshot_note: str = ""
    caliber: str = ""
    status_rows: list[dict[str, Any]] = field(default_factory=list)
    board_rows: list[dict[str, Any]] = field(default_factory=list)
    freshness_rows: list[dict[str, Any]] = field(default_factory=list)
    stale_rows: list[dict[str, Any]] = field(default_factory=list)


async def fetch_weekly_summary(config: BffConfig) -> WeeklySummaryData:
    """Ask the取数 service for the four aggregates the report is built from."""
    data = WeeklySummaryData()
    timeout = httpx.Timeout(30.0)
    headers = {
        "Authorization": f"Bearer {config.mcp_token}",
        "Accept": "application/json, text/event-stream",
    }
    if not config.mcp_token:
        # An empty token must not produce an "Authorization: Bearer " header —
        # that is an illegal header value (httpx raises LocalProtocolError).
        headers.pop("Authorization", None)
    # trust_env=False: the取数 service is loopback/internal — a machine-level
    # proxy must never hijack it.
    async with (
        httpx.AsyncClient(headers=headers, timeout=timeout, trust_env=False) as http_client,
        streamable_http_client(config.mcp_url, http_client=http_client) as parts,
        ClientSession(parts[0], parts[1]) as session,
    ):
        await session.initialize()

        async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            result = await session.call_tool(name, arguments)
            # SDK field naming drifted between versions (structuredContent vs
            # structured_content) — accept both.
            structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
            if isinstance(structured, dict) and set(structured) == {"result"}:
                raw = structured["result"]
                if isinstance(raw, str):
                    import json

                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        return {"ok": False}
            return structured or {}

        status = await call("weekly_aggregate", {"group_by": "status"})
        if status.get("ok"):
            data.status_rows = _rows(status)
            data.caliber = str(status.get("caliber", ""))
            data.snapshot_note = str(status.get("snapshot_note", ""))

        board = await call("weekly_aggregate", {"group_by": "board"})
        if board.get("ok"):
            data.board_rows = _rows(board)

        freshness = await call("weekly_freshness", {})
        if freshness.get("ok"):
            data.freshness_rows = _rows(freshness)

        stale = await call("weekly_freshness_distribution", {"stale_days": 30})
        if stale.get("ok"):
            data.stale_rows = _rows(stale)

    return data


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def build_summary_document(data: WeeklySummaryData) -> bytes:
    """Lay the fetched data out as a leader-style weekly summary .docx."""
    doc = Document()
    _set_east_asia(doc)

    title = doc.add_heading("", level=0)
    _run_with_font(title, "周报总结", bold=True, size=Pt(26))

    meta = doc.add_paragraph()
    _run_with_font(meta, f"生成日期:{date.today().isoformat()}", bold=True)
    meta_second = doc.add_paragraph()
    _run_with_font(meta_second, data.snapshot_note or "数据快照见正文", size=Pt(9))
    if data.caliber:
        para = doc.add_paragraph()
        _run_with_font(para, f"口径:{data.caliber}", italic=True, size=Pt(9))

    doc.add_heading("一、总体", level=1)
    if data.status_rows:
        table = _add_table(doc, data.status_rows)
        _style_table(table)

    doc.add_heading("二、看板对比", level=1)
    if data.board_rows:
        _style_table(_add_table(doc, data.board_rows))

    doc.add_heading("三、进展时效", level=1)
    if data.freshness_rows:
        doc.add_paragraph("各看板最新进展时间:")
        _style_table(_add_table(doc, data.freshness_rows))

    doc.add_heading("四、滞后风险", level=1)
    if data.stale_rows:
        _style_table(_add_table(doc, data.stale_rows))
    else:
        doc.add_paragraph("近 30 天滞后统计无数据。")

    doc.add_paragraph()
    note = doc.add_paragraph()
    _run_with_font(note, "数据来源:演示库(weekly_mock),非集团真实周报。", bold=True)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _set_east_asia(doc: Document) -> None:
    """w:eastAsia on every used style keeps Chinese fonts even across viewers.

    Without it, heading styles fall back to MS Gothic for CJK and the
    document comes out in mismatched typefaces. Runs get the font set
    explicitly too, so numbers and Latin do not slip back to Calibri.
    """
    for style_name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3", "Heading 4"):
        try:
            style = doc.styles[style_name]
        except KeyError:
            continue
        style.font.name = "Microsoft YaHei"
        if style._element.rPr is not None and style._element.rPr.rFonts is not None:
            style._element.rPr.rFonts.set(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia",
                "Microsoft YaHei",
            )
    doc.styles["Normal"].font.size = Pt(10.5)


def _run_with_font(paragraph: Any, text: str, *, bold: bool = False, italic: bool = False, size: Pt | None = None) -> None:
    run = paragraph.add_run(text)
    run.bold = bold
    run.italic = italic
    if size is not None:
        run.font.size = size
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia",
        "Microsoft YaHei",
    )


def _add_table(doc: Document, rows: list[dict[str, Any]]):
    if not rows:
        return doc.add_table(rows=0, cols=0)
    columns = list(rows[0].keys())
    table = doc.add_table(rows=1, cols=len(columns))
    for index, name in enumerate(columns):
        table.rows[0].cells[index].text = str(name)
    for row in rows:
        cells = table.add_row().cells
        for index, name in enumerate(columns):
            cells[index].text = str(row.get(name, ""))
    return table


def _style_table(table: Any) -> None:
    try:
        table.style = "Light Grid Accent 1"
    except KeyError:
        pass


def build_summary_document_text(data: WeeklySummaryData) -> str:
    """Plain-text fallback preview (used by tests and debugging)."""
    lines = ["周报总结", f"快照:{data.snapshot_note}", f"口径:{data.caliber}"]
    for title, rows in (("状态分布", data.status_rows), ("看板", data.board_rows), ("时效", data.freshness_rows), ("滞后", data.stale_rows)):
        lines.append(f"[{title}] {len(rows)} 行")
    lines.append("数据来源:演示库(weekly_mock),非集团真实周报。")
    return "\n".join(lines)
