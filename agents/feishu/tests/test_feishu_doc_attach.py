"""``feishu_doc_append_image`` / ``_append_file`` — the create → upload → bind sequence.

Putting a local image or file into a document is three calls, and the tests that matter
are about the three constants that have to travel together:

    image   block_type 27   parent_type "docx_image"   patch field ``replace_image``
    file    block_type 23   parent_type "docx_file"    patch field ``replace_file``

A mismatched pair — a file block bound with ``replace_image`` — is accepted by Feishu with
``code: 0`` and renders as a broken placeholder, so the wrong combination cannot be caught
downstream. Each triple is asserted end to end.

The other half is cleanup. Step 3 is what makes a block render; without it the upload is
attached and the reader sees a placeholder. So a failure at step 2 or 3 has to remove the
empty block it created, and both failure points are covered — an orphaned placeholder in
someone's document is worse than no attachment at all.

The image path already existed for the chart tools (``test_feishu_chart.py`` covers it
from that angle); what is new here is the file path and the two exposed tools.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")

#: block_type numbers, restated here rather than imported: a test that reads the constant
#: from the module under test would pass just as happily if someone changed it.
IMAGE_BLOCK_TYPE = 27
FILE_BLOCK_TYPE = 23


class _FakeFeishu:
    """Records each ``_invoke`` call so the create → upload → patch sequence is assertable.

    Call sites hand ``_invoke`` a request *factory* (a retry under a second identity needs
    a clean request), so resolve it the way the real ``_invoke`` does before recording.
    """

    def __init__(self, *, fail_at: str = "") -> None:
        self.calls: list[Any] = []
        self.fail_at = fail_at

    async def __call__(
        self, request: Any, user_key: str | None = None, prefer: str = "tenant", **_kw: Any
    ) -> dict[str, Any]:
        request = request() if callable(request) else request
        self.calls.append(request)
        uri = getattr(request, "uri", "")
        method = request.http_method.name
        if "medias/upload_all" in uri:
            if self.fail_at == "upload":
                return {"ok": False, "message": "upload rejected"}
            return {"ok": True, "data": {"file_token": "tok_attach"}}
        if method == "PATCH":
            if self.fail_at == "patch":
                return {"ok": False, "message": "patch rejected"}
            return {"ok": True, "data": {}}
        if method == "DELETE":
            return {"ok": True, "data": {}}
        if "children" in uri:
            return {"ok": True, "data": {"children": [{"block_id": "blk1"}], "index": 3}}
        return {"ok": True, "data": {}}

    def methods(self) -> list[str]:
        return [c.http_method.name for c in self.calls]


def _local_file(tmp_path: Path, name: str = "报告.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.7" + b"0" * 64)
    return path


# ------------------------------------------------------------- the file block's triple


@pytest.mark.asyncio
async def test_append_file_runs_create_upload_patch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All three constants of the *file* path, in one assertion each."""
    doc_file = _local_file(tmp_path)
    fake = _FakeFeishu()
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.append_doc_file_impl("doc1", str(doc_file))

    assert result["ok"] is True, result
    assert result["block_id"] == "blk1"
    assert result["file_token"] == "tok_attach"
    assert fake.methods() == ["POST", "POST", "PATCH"]  # create block, upload, bind token

    created = fake.calls[0]
    assert created.body["children"][0]["block_type"] == FILE_BLOCK_TYPE
    assert created.body["children"][0]["file"] == {"token": ""}

    upload = fake.calls[1]
    assert upload.body["parent_type"] == "docx_file"
    assert upload.body["parent_node"] == "blk1", "the upload must target the new block, not a folder"

    patch = fake.calls[2]
    assert patch.body == {"replace_file": {"token": "tok_attach"}}
    assert "replace_image" not in patch.body, "binding a file block with replace_image renders broken"


@pytest.mark.asyncio
async def test_append_image_uses_the_image_triple(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The image path's three constants, asserted beside the file path's so they cannot drift."""
    png = tmp_path / "chart.png"
    png.write_bytes(b"\x89PNG\r\n" + b"0" * 64)
    fake = _FakeFeishu()
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.append_doc_image_impl("doc1", str(png))

    assert result["ok"] is True, result
    assert fake.calls[0].body["children"][0]["block_type"] == IMAGE_BLOCK_TYPE
    assert fake.calls[1].body["parent_type"] == "docx_image"
    assert fake.calls[2].body == {"replace_image": {"token": "tok_attach"}}


@pytest.mark.asyncio
async def test_the_two_paths_do_not_share_a_constant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Image and file must differ in all three places, not two out of three."""
    png = tmp_path / "c.png"
    png.write_bytes(b"\x89PNG\r\n" + b"0" * 32)
    attachment = _local_file(tmp_path)

    image_fake = _FakeFeishu()
    monkeypatch.setattr(_impl, "_invoke", image_fake)
    await _impl.append_doc_image_impl("doc1", str(png))

    file_fake = _FakeFeishu()
    monkeypatch.setattr(_impl, "_invoke", file_fake)
    await _impl.append_doc_file_impl("doc1", str(attachment))

    assert image_fake.calls[0].body["children"][0]["block_type"] != file_fake.calls[0].body["children"][0]["block_type"]
    assert image_fake.calls[1].body["parent_type"] != file_fake.calls[1].body["parent_type"]
    assert set(image_fake.calls[2].body) != set(file_fake.calls[2].body)


# ------------------------------------------------------------------------- cleanup


@pytest.mark.parametrize("fail_at", ["upload", "patch"])
@pytest.mark.asyncio
async def test_append_file_cleans_up_the_empty_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail_at: str
) -> None:
    """A failure after the block exists must remove it.

    Leaving it behind is not a cosmetic problem: the reader sees an attachment that cannot
    be opened, in a document the tool reported an error about somewhere else entirely.
    """
    doc_file = _local_file(tmp_path)
    fake = _FakeFeishu(fail_at=fail_at)
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.append_doc_file_impl("doc1", str(doc_file))
    assert result["ok"] is False, result
    assert "DELETE" in fake.methods(), fake.methods()


# ---------------------------------------------------------------- refusals and captions


@pytest.mark.asyncio
async def test_append_file_requires_document_id() -> None:
    result = await _impl.append_doc_file_impl("  ", "x.pdf")
    assert result["ok"] is False
    assert "document_id" in result["message"]


@pytest.mark.asyncio
async def test_append_file_reports_a_missing_local_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """The block is created first, so a missing file has to be cleaned up too."""
    fake = _FakeFeishu()
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.append_doc_file_impl("doc1", "no/such/report.pdf")
    assert result["ok"] is False
    assert "not found" in result["message"]
    assert "DELETE" in fake.methods(), "the placeholder block must not survive a missing file"


@pytest.mark.asyncio
async def test_append_file_refuses_over_twenty_megabytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``upload_all`` caps at 20MB; the message has to name the size, not just fail."""
    big = tmp_path / "big.zip"
    big.write_bytes(b"0" * (_impl._UPLOAD_ALL_MAX_BYTES + 1))
    fake = _FakeFeishu()
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.append_doc_file_impl("doc1", str(big))
    assert result["ok"] is False
    assert result["size"] == _impl._UPLOAD_ALL_MAX_BYTES + 1
    assert "20MB" in result["message"]


@pytest.mark.asyncio
async def test_append_file_writes_a_caption_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """File blocks carry no caption field, so a caption becomes its own paragraph."""
    doc_file = _local_file(tmp_path)
    fake = _FakeFeishu()
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.append_doc_file_impl("doc1", str(doc_file), "附件: 季度报告")
    assert result["ok"] is True, result
    assert result["caption_written"] is True
    assert len(fake.calls) > 3, "the caption is an extra call after the three-step sequence"


# ------------------------------------------------------------------- the tools exist


@pytest.mark.parametrize("name", ["feishu_doc_append_image", "feishu_doc_append_file"])
def test_tool_is_async_with_a_docstring(name: str) -> None:
    """``load_tools_from_workspace`` only picks up ``async def``, silently skipping others."""
    mod = importlib.import_module("feishu_doc_attach")
    fn = getattr(mod, name)
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()
