"""``feishu_doc_export`` — the three-step export chain, and where it refuses to start.

Export is the one drive flow that a rules table cannot carry, for two reasons at once:
the middle step has to be *repeated* until Feishu says the file is built, and the product
is bytes on disk rather than a JSON response. So the contract this file pins is the chain
itself — create → poll → download → write — plus the three ways it declines to spend a
request:

* a format the source type cannot produce (Feishu's 1069918),
* a csv export with no ``sub_id`` (1069904), because one csv cannot hold a spreadsheet's
  several worksheets and Feishu will not choose for you,
* a ``job_status`` that will never reach 0 — a document too large (107) or with too many
  images (6000) is reported instead of polled at.

The last one matters more than it looks: polling a fatal status would spend the whole
budget and then report a timeout, which sends the caller off to retry something that
cannot succeed.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from lark_channel.core.enum import HttpMethod

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")

TOKEN = "doccnFT1abcdef"
TICKET = "6933093124755423251"
EXPORT_FILE_TOKEN = "boxcnxe5OdjlAkNgSNdsJv"
PAYLOAD = b"%PDF-1.7 not really a pdf but these exact bytes must land on disk"


class _FakeInvoke:
    """Stands in for ``_invoke``: records requests, replays scripted responses.

    Requests arrive as factories (the polling loop must build a fresh request per
    attempt), so each one is called to get the ``BaseRequest`` being recorded.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.requests: list[Any] = []
        self._responses = responses

    async def __call__(self, request: Any, **_kwargs: Any) -> dict[str, Any]:
        built = request() if callable(request) else request
        self.requests.append(built)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return self._responses[index]

    @property
    def uris(self) -> list[str]:
        return [r.uri for r in self.requests]


def _created(ticket: str = TICKET) -> dict[str, Any]:
    return {"ok": True, "data": {"ticket": ticket}}


def _status(job_status: int, **extra: Any) -> dict[str, Any]:
    result = {"job_status": job_status, **extra}
    return {"ok": True, "data": {"result": result}}


def _done(file_token: str = EXPORT_FILE_TOKEN, file_name: str = "季度报告.pdf") -> dict[str, Any]:
    return _status(0, file_token=file_token, file_name=file_name, file_size=len(PAYLOAD))


def _run(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any]],
    target: Path,
    downloaded: tuple[bytes | None, str] = (PAYLOAD, ""),
    **overrides: Any,
) -> tuple[_FakeInvoke, dict[str, Any]]:
    """Run one export with a scripted API and a scripted download."""
    fake = _FakeInvoke(responses)
    monkeypatch.setattr(_impl, "_invoke", fake)
    monkeypatch.setattr(_impl, "_download_export_bytes", lambda *a, **k: _immediate(downloaded))
    # The real delays are seconds long and this suite exercises the loop several times.
    # Shrinking them keeps the *sequence* under test (which is the contract) without
    # paying the wall-clock budget — the delays themselves are not what is being asserted.
    monkeypatch.setattr(_impl, "_EXPORT_POLL_DELAYS", (0.0,) * len(_impl._EXPORT_POLL_DELAYS))
    # ``overrides`` wins over the positional default, so a test can blank out save_path
    # itself without colliding with this helper's own parameter.
    kwargs: dict[str, Any] = {
        "token": TOKEN,
        "file_type": "docx",
        "file_extension": "pdf",
        "save_path": str(target),
        **overrides,
    }
    out: dict[str, Any] = anyio.run(lambda: _impl.export_doc_impl(**kwargs))
    return fake, out


async def _immediate(value: tuple[bytes | None, str]) -> tuple[bytes | None, str]:
    return value


# ------------------------------------------------------------------- the happy chain


def test_export_runs_create_then_poll_then_writes_the_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """One call does all three steps, and the file on disk is byte-for-byte the payload."""
    target = tmp_path / "out" / "季度报告.pdf"
    fake, out = _run(monkeypatch, [_created(), _done()], target)

    assert out["ok"] is True, out
    assert fake.uris == [
        "/open-apis/drive/v1/export_tasks",
        "/open-apis/drive/v1/export_tasks/:ticket",
    ]
    assert target.read_bytes() == PAYLOAD
    assert out["path"] == str(target)
    assert out["bytes"] == len(PAYLOAD)
    assert out["ticket"] == TICKET
    assert out["file_name"] == "季度报告.pdf"


def test_create_step_sends_the_documented_body(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``token`` / ``type`` / ``file_extension``, and no ``sub_id`` when none was given."""
    fake, out = _run(monkeypatch, [_created(), _done()], tmp_path / "a.pdf")
    assert out["ok"] is True, out
    create = fake.requests[0]
    assert create.http_method == HttpMethod.POST
    assert create.body == {"token": TOKEN, "type": "docx", "file_extension": "pdf"}
    assert "sub_id" not in create.body


def test_poll_step_carries_the_doc_token_in_the_query(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The ticket identifies the task but Feishu still requires ``token`` — 1069904 otherwise."""
    fake, out = _run(monkeypatch, [_created(), _done()], tmp_path / "a.pdf")
    assert out["ok"] is True, out
    poll = fake.requests[1]
    assert poll.http_method == HttpMethod.GET
    assert poll.paths == {"ticket": TICKET}
    assert [(k, str(v)) for k, v in (poll.queries or [])] == [("token", TOKEN)]


def test_polls_until_the_job_reports_done(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``job_status`` 1 and 2 mean "not yet"; only 0 produces a usable file_token."""
    target = tmp_path / "a.pdf"
    fake, out = _run(monkeypatch, [_created(), _status(1), _status(2), _done()], target)
    assert out["ok"] is True, out
    assert fake.uris.count("/open-apis/drive/v1/export_tasks/:ticket") == 3
    assert target.read_bytes() == PAYLOAD


def test_csv_export_passes_sub_id_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A spreadsheet's csv export names the worksheet, and it reaches the request body."""
    fake, out = _run(
        monkeypatch,
        [_created(), _done(file_name="表.csv")],
        tmp_path / "a.csv",
        file_type="sheet",
        file_extension="csv",
        sub_id="6e5ed3",
    )
    assert out["ok"] is True, out
    assert fake.requests[0].body == {
        "token": TOKEN,
        "type": "sheet",
        "file_extension": "csv",
        "sub_id": "6e5ed3",
    }


# --------------------------------------------------- refusals that cost no HTTP call


def test_mismatched_format_is_refused_before_any_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A docx cannot become an xlsx; Feishu says 1069918 and we say so first."""
    fake, out = _run(monkeypatch, [_created(), _done()], tmp_path / "a.xlsx", file_extension="xlsx")
    assert out["ok"] is False, out
    assert fake.requests == [], "a format mismatch must not reach the API"
    assert "1069918" in json.dumps(out, ensure_ascii=False)


def test_unknown_source_type_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``file_type`` is a closed set — mindnote and slides have no export at all."""
    fake, out = _run(monkeypatch, [_created(), _done()], tmp_path / "a.pdf", file_type="mindnote")
    assert out["ok"] is False, out
    assert fake.requests == []


def test_csv_without_sub_id_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """One csv holds one worksheet, so Feishu requires the caller to say which."""
    fake, out = _run(monkeypatch, [_created(), _done()], tmp_path / "a.csv", file_type="sheet", file_extension="csv")
    assert out["ok"] is False, out
    assert fake.requests == []
    message = json.dumps(out, ensure_ascii=False)
    assert "sub_id" in message and "sheet_id" in message


def test_bitable_csv_asks_for_a_table_id_by_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The same missing field is called ``sheet_id`` for a sheet and ``table_id`` for a base.

    Telling a base user to pass a ``sheet_id`` sends them looking for something their
    document does not have.
    """
    _fake, out = _run(monkeypatch, [_created(), _done()], tmp_path / "a.csv", file_type="bitable", file_extension="csv")
    assert out["ok"] is False, out
    assert "table_id" in json.dumps(out, ensure_ascii=False)


def test_leading_dot_on_the_extension_is_accepted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``".pdf"`` is what a caller who thought in filenames would pass."""
    fake, out = _run(monkeypatch, [_created(), _done()], tmp_path / "a.pdf", file_extension=".pdf")
    assert out["ok"] is True, out
    assert fake.requests[0].body["file_extension"] == "pdf"


# ------------------------------------------------------- failures that stop the loop


@pytest.mark.parametrize(
    ("status", "expected"),
    [(107, "文档过大"), (6000, "图片过多"), (110, "没有导出权限"), (111, "已被删除")],
)
def test_fatal_job_status_stops_polling_and_explains(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int, expected: str
) -> None:
    """A status that will never reach 0 is reported, not waited on.

    ``requests`` length is the assertion that matters: create + exactly one poll. Polling
    a fatal status would burn the whole budget and then report a timeout, sending the
    caller to retry something that cannot succeed.
    """
    fake, out = _run(monkeypatch, [_created(), _status(status, job_error_msg="nope")], tmp_path / "a.pdf")
    assert out["ok"] is False, out
    assert len(fake.requests) == 2, fake.uris
    assert expected in json.dumps(out, ensure_ascii=False)
    assert out["job_status"] == status


def test_done_without_a_file_token_is_an_error_not_a_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``job_status: 0`` with no token cannot be downloaded — say so rather than proceed."""
    target = tmp_path / "a.pdf"
    _fake, out = _run(monkeypatch, [_created(), _status(0)], target)
    assert out["ok"] is False, out
    assert not target.exists()


def test_create_failure_is_returned_as_is(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A rejected create step is reported with Feishu's own error, not a generic one."""
    fake, out = _run(monkeypatch, [{"ok": False, "message": "1069902 no permission"}], tmp_path / "a.pdf")
    assert out["ok"] is False, out
    assert len(fake.requests) == 1, "polling must not start after a failed create"
    assert "1069902" in json.dumps(out, ensure_ascii=False)


def test_missing_ticket_stops_the_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A create that answers ok but carries no ticket has nothing to poll."""
    fake, out = _run(monkeypatch, [{"ok": True, "data": {}}], tmp_path / "a.pdf")
    assert out["ok"] is False, out
    assert len(fake.requests) == 1


def test_expired_download_mentions_the_ten_minute_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Feishu deletes the built file 10 minutes after the task finishes.

    A 404 here reads as "the export failed" unless the message says otherwise, and the
    fix (export again) is not the one a caller would guess.
    """
    target = tmp_path / "a.pdf"
    _fake, out = _run(monkeypatch, [_created(), _done()], target, downloaded=(None, "HTTP 404"))
    assert out["ok"] is False, out
    assert "10 分钟" in json.dumps(out, ensure_ascii=False)
    assert not target.exists()


def test_polling_gives_up_rather_than_looping_forever(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A job stuck at "in progress" ends in a report, not an unbounded loop.

    And the ticket is deliberately *not* offered as a way to resume: the built file is
    deleted 10 minutes after the task finishes, so a ticket handed back would usually be
    worthless by the time anyone used it.
    """
    target = tmp_path / "a.pdf"
    fake, out = _run(monkeypatch, [_created(), _status(2)], target)
    assert out["ok"] is False, out
    assert len(fake.requests) == 1 + len(_impl._EXPORT_POLL_DELAYS)
    assert "10 分钟" in json.dumps(out, ensure_ascii=False)
    assert not target.exists()


@pytest.mark.parametrize("blank", ["token", "save_path"])
def test_token_and_save_path_are_both_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, blank: str) -> None:
    """Neither can be filled in for the caller: one names the document, one the target."""
    # Typed rather than splatted inline: `**{blank: "  "}` tells the checker only that
    # *some* keyword gets a str, so it picks the first candidate parameter and reports a
    # mismatch against ``downloaded``.
    overrides: dict[str, Any] = {blank: "  "}
    fake, out = _run(monkeypatch, [_created(), _done()], tmp_path / "a.pdf", **overrides)
    assert out["ok"] is False, out
    assert fake.requests == []
