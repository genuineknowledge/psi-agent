# Feishu Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 haitun agent 加 5 个飞书工具：`feishu_doc_read` 读文档全文，`feishu_drive_add_comment` / `list_comments` / `list_comment_replies` / `reply_comment` 管理云文档评论。

**Architecture:** 两个薄壳工具文件（`feishu_doc.py` / `feishu_drive.py`）+ 一个共享实现层（`_feishu_impl.py`）。实现层懒加载 `lark_channel` SDK，module 级缓存一个 authenticated `Client`，用原生 async `Client.arequest(BaseRequest)` 执行请求。SDK 现成的 comment builder 直接用；docx/doc/sheet raw-content 和 create-reply 端点手搭 `BaseRequest`（同 SDK 的 `api/drive/comment.py` 做法）。

**Tech Stack:** Python 3.14, anyio, lark-channel-sdk（已有依赖）, pytest + pytest-asyncio, ruff, ty.

## Global Constraints

- 零新增依赖：`lark-channel-sdk>=1.1.0` 已在 pyproject。不改 `pyproject.toml` / `.github/workflows/nuitka.yml` / `.github/workflows/pyinstaller.yml`。
- 所有工具函数 `async def`，返回 JSON 字符串（`json.dumps(..., ensure_ascii=False)`）。
- 工具参数只用 `str` / `int` / `bool`（dict/嵌套类型会被 ToolRegistry 跳过）。
- 鉴权：读 env `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`；缺失时返回 `{"ok": false, "message": ...}`，绝不抛异常、不影响其它工具加载。
- 统一返回：成功 `{"ok": true, ...}`，失败 `{"ok": false, "message": ...}`；飞书 `code != 0` 时把飞书 `code` + `msg` 原样带回。
- Google-style docstring：`Args:` 段每个参数一行，供 `ToolFunction.from_callable` 生成 schema。
- push 前：`uv run ruff check .` + `uv run ruff format --check .` + `uv run ty check .` + pytest 全过。
- 每个工具 commit 用 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` 结尾。

## File Structure

- `examples/haitun-workspace/tools/_feishu_impl.py` — 共享实现层：SDK 懒加载、client 缓存、`_invoke`、请求 builder、返回归一化、doc/drive 各操作的 impl 函数。
- `examples/haitun-workspace/tools/feishu_doc.py` — 薄壳，1 工具 `feishu_doc_read`。
- `examples/haitun-workspace/tools/feishu_drive.py` — 薄壳，4 工具。
- `examples/haitun-workspace/tests/test_feishu.py` — 单元测试，mock `_invoke`，不打真实 API。
- `examples/haitun-workspace/AGENTS.md` — 工具表加行（文档收尾任务内）。

## SDK 调用契约（已实测确认）

- `Client.builder().app_id(id).app_secret(secret).build()` → `Client`。
- `await client.arequest(req: BaseRequest) -> BaseResponse`。
- `BaseRequest`：`.http_method`（`HttpMethod.GET/POST`）、`.uri`（`:name` 占位）、`.paths[name]=val`、`.add_query(k, v)`、`.body=dict`、`.token_types={AccessTokenType.TENANT, AccessTokenType.USER}`。
- `BaseResponse`：`.code`（int, 0=成功）、`.msg`（str）、`.success`（bool）、`.raw`（`RawResponse`）。
- `RawResponse`：`.content`（bytes, JSON body）、`.status_code`、`.headers`。
- `from lark_channel.core.enum import HttpMethod, AccessTokenType`。
- `from lark_channel.core.model import BaseRequest`。
- 现成 builder（`from lark_channel.api.drive import comment`）：`build_comment_create_request(*, file_token, file_type, content)`、`build_comment_list_request(*, file_token, file_type, page_token=None, page_size=None, is_whole=None, is_solved=None)`、`build_comment_reply_list_request(*, file_token, file_type, comment_id, page_token=None, page_size=None)`。

---

### Task 1: 共享实现层基础 — client 构建 + `_invoke` + 返回归一化

**Files:**
- Create: `examples/haitun-workspace/tools/_feishu_impl.py`
- Test: `examples/haitun-workspace/tests/test_feishu.py`

**Interfaces:**
- Produces:
  - `def dumps_result(result: dict) -> str` — 紧凑 JSON 序列化。
  - `def _error(message: str, **extra) -> dict` — 返回 `{"ok": False, "message": ..., **extra}`。
  - `def _config() -> tuple[str, str] | None` — 读 env，返回 `(app_id, app_secret)`；任一缺失返回 `None`。
  - `async def _invoke(request) -> dict` — 执行 `client.arequest`，归一化为 `{"ok": bool, "code": int, "msg": str, "data": dict}`；鉴权缺失/异常返回 `_error(...)`。
  - `def _reset_client() -> None` — 清空缓存 client（测试用）。

- [ ] **Step 1: 写失败测试**

在 `examples/haitun-workspace/tests/test_feishu.py`：

```python
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PSI_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("PSI_FEISHU_APP_SECRET", raising=False)
    _impl._reset_client()


def test_config_missing_returns_none() -> None:
    assert _impl._config() is None


def test_config_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec_y")
    assert _impl._config() == ("cli_x", "sec_y")


async def test_invoke_without_auth_returns_error() -> None:
    class _Req:
        pass

    result = await _impl._invoke(_Req())
    assert result["ok"] is False
    assert "PSI_FEISHU_APP_ID" in result["message"]


def test_dumps_result_roundtrip() -> None:
    s = _impl.dumps_result({"ok": True, "data": {"名": "值"}})
    assert json.loads(s)["data"]["名"] == "值"
    assert "\\u" not in s  # ensure_ascii=False
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest examples/haitun-workspace/tests/test_feishu.py -q -p no:cacheprovider -c /dev/null`
Expected: FAIL — `ModuleNotFoundError: No module named '_feishu_impl'`

- [ ] **Step 3: 写最小实现**

创建 `examples/haitun-workspace/tools/_feishu_impl.py`：

```python
"""Private helper for the Feishu tools — authenticated client + request execution.

Wraps the ``lark_channel`` SDK (already a project dependency): builds one
authenticated ``Client`` from ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``,
caches it module-level, and runs ``BaseRequest`` objects through the SDK's native
async ``arequest``. Drive-comment requests reuse the SDK's ready-made builders;
docx/doc/sheet raw-content and create-reply requests are hand-built the same way
the SDK's own ``api/drive/comment.py`` does it.
"""

from __future__ import annotations

import json
import os
from typing import Any

_client: Any = None


def dumps_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "message": message, **extra}


def _config() -> tuple[str, str] | None:
    app_id = os.environ.get("PSI_FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("PSI_FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        return None
    return app_id, app_secret


def _reset_client() -> None:
    global _client
    _client = None


def _get_client() -> Any:
    global _client
    if _client is not None:
        return _client
    creds = _config()
    if creds is None:
        return None
    from lark_channel.client import Client

    app_id, app_secret = creds
    _client = Client.builder().app_id(app_id).app_secret(app_secret).build()
    return _client


async def _invoke(request: Any) -> dict[str, Any]:
    client = _get_client()
    if client is None:
        return _error(
            "Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET."
        )
    try:
        resp = await client.arequest(request)
    except Exception as exc:  # SDK/transport failure
        return _error(f"Feishu request failed: {type(exc).__name__}: {exc}")

    code = getattr(resp, "code", None)
    msg = getattr(resp, "msg", "") or ""
    data: dict[str, Any] = {}
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None
    if content:
        try:
            body = json.loads(bytes(content).decode("utf-8"))
            if isinstance(body, dict):
                data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
                if code is None:
                    code = body.get("code")
                if not msg:
                    msg = body.get("msg", "") or ""
        except (ValueError, UnicodeDecodeError):
            pass

    ok = code == 0
    if not ok:
        return _error(f"Feishu API error {code}: {msg}", code=code)
    return {"ok": True, "code": 0, "msg": msg, "data": data}
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest examples/haitun-workspace/tests/test_feishu.py -q -p no:cacheprovider -c /dev/null`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add examples/haitun-workspace/tools/_feishu_impl.py examples/haitun-workspace/tests/test_feishu.py
git commit -m "feat(haitun): feishu 工具共享实现层（client + _invoke）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Drive 评论实现函数（4 个）

**Files:**
- Modify: `examples/haitun-workspace/tools/_feishu_impl.py`（追加 4 个 impl 函数 + 1 个 reply builder + import）
- Test: `examples/haitun-workspace/tests/test_feishu.py`（追加）

**Interfaces:**
- Consumes: `_invoke`, `_error`（Task 1）；`comment.build_comment_create_request` / `build_comment_list_request` / `build_comment_reply_list_request`。
- Produces:
  - `async def add_comment_impl(file_token: str, file_type: str, content: str) -> dict`
  - `async def list_comments_impl(file_token: str, file_type: str, page_size: int, page_token: str) -> dict`
  - `async def list_comment_replies_impl(file_token: str, file_type: str, comment_id: str, page_size: int, page_token: str) -> dict`
  - `async def reply_comment_impl(file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str) -> dict`
  - `def _build_reply_create_request(*, file_token, file_type, comment_id, content, at_user_id) -> BaseRequest`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_feishu.py`（用 monkeypatch 把 `_invoke` 换成捕获 request 的假函数）：

```python
class _CapturedInvoke:
    """Replace _invoke; record the BaseRequest, return a canned success dict."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.request: Any = None
        self._data = data or {}

    async def __call__(self, request: Any) -> dict[str, Any]:
        self.request = request
        return {"ok": True, "code": 0, "msg": "", "data": self._data}


def _qdict(req: Any) -> dict[str, str]:
    """SDK stores queries as list[tuple[str, str]] with str-coerced values."""
    return dict(req.queries)


async def test_add_comment_builds_create_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"comment_id": "c1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.add_comment_impl("tok", "docx", "hello")
    assert result["ok"] is True
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.paths["file_token"] == "tok"
    assert _qdict(req).get("file_type") == "docx"


async def test_list_comments_passes_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.list_comments_impl("tok", "docx", 25, "pt1")
    q = _qdict(cap.request)
    assert q.get("page_size") == "25"  # add_query coerces to str
    assert q.get("page_token") == "pt1"
    assert q.get("is_whole") == "true"


async def test_reply_replies_list_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": []})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.list_comment_replies_impl("tok", "docx", "cid", 50, "")
    req = cap.request
    assert req.paths["comment_id"] == "cid"
    assert "replies" in req.uri


async def test_reply_comment_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"reply_id": "r1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.reply_comment_impl("tok", "docx", "cid", "hi", "")
    req = cap.request
    assert req.http_method.name == "POST"
    assert "replies" in req.uri
    els = req.body["content"]["elements"]
    assert els[0]["text_run"]["text"] == "hi"
    assert all(e["type"] != "person" for e in els)


async def test_reply_comment_with_mention(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"reply_id": "r2"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.reply_comment_impl("tok", "docx", "cid", "hi", "ou_abc")
    els = cap.request.body["content"]["elements"]
    assert any(e["type"] == "person" and e["person"]["user_id"] == "ou_abc" for e in els)
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest examples/haitun-workspace/tests/test_feishu.py -q -p no:cacheprovider -c /dev/null`
Expected: FAIL — `AttributeError: module '_feishu_impl' has no attribute 'add_comment_impl'`

- [ ] **Step 3: 写最小实现**

在 `_feishu_impl.py` 顶部 import 段追加：

```python
from lark_channel.api.drive import comment as _comment
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest
```

在文件末尾追加：

```python
async def add_comment_impl(file_token: str, file_type: str, content: str) -> dict[str, Any]:
    req = _comment.build_comment_create_request(
        file_token=file_token, file_type=file_type, content=content
    )
    return await _invoke(req)


async def list_comments_impl(
    file_token: str, file_type: str, page_size: int, page_token: str
) -> dict[str, Any]:
    req = _comment.build_comment_list_request(
        file_token=file_token,
        file_type=file_type,
        page_size=page_size,
        page_token=page_token or None,
        is_whole="true",
    )
    return await _invoke(req)


async def list_comment_replies_impl(
    file_token: str, file_type: str, comment_id: str, page_size: int, page_token: str
) -> dict[str, Any]:
    req = _comment.build_comment_reply_list_request(
        file_token=file_token,
        file_type=file_type,
        comment_id=comment_id,
        page_size=page_size,
        page_token=page_token or None,
    )
    return await _invoke(req)


def _build_reply_create_request(
    *, file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies"
    req.paths["file_token"] = file_token
    req.paths["comment_id"] = comment_id
    req.add_query("file_type", file_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    elements: list[dict[str, Any]] = []
    if at_user_id:
        elements.append({"type": "person", "person": {"user_id": at_user_id}})
    elements.append({"type": "text_run", "text_run": {"text": content}})
    req.body = {"content": {"elements": elements}}
    return req


async def reply_comment_impl(
    file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str
) -> dict[str, Any]:
    req = _build_reply_create_request(
        file_token=file_token,
        file_type=file_type,
        comment_id=comment_id,
        content=content,
        at_user_id=at_user_id,
    )
    return await _invoke(req)
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest examples/haitun-workspace/tests/test_feishu.py -q -p no:cacheprovider -c /dev/null`
Expected: PASS（9 passed）

- [ ] **Step 5: 提交**

```bash
git add examples/haitun-workspace/tools/_feishu_impl.py examples/haitun-workspace/tests/test_feishu.py
git commit -m "feat(haitun): feishu drive 评论 impl（add/list/replies/reply）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `feishu_drive.py` 薄壳（4 个工具）

**Files:**
- Create: `examples/haitun-workspace/tools/feishu_drive.py`
- Test: `examples/haitun-workspace/tests/test_feishu.py`（追加加载校验）

**Interfaces:**
- Consumes: `_feishu_impl.add_comment_impl` / `list_comments_impl` / `list_comment_replies_impl` / `reply_comment_impl`, `dumps_result`。
- Produces: 工具 `feishu_drive_add_comment` / `feishu_drive_list_comments` / `feishu_drive_list_comment_replies` / `feishu_drive_reply_comment`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_feishu.py`：

```python
def test_drive_tools_are_async_with_docstrings() -> None:
    import inspect

    mod = importlib.import_module("feishu_drive")
    for name in (
        "feishu_drive_add_comment",
        "feishu_drive_list_comments",
        "feishu_drive_list_comment_replies",
        "feishu_drive_reply_comment",
    ):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"


async def test_drive_add_comment_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_drive")

    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": True, "code": 0, "msg": "", "data": {"comment_id": "c9"}}

    monkeypatch.setattr(_impl, "add_comment_impl", _fake)
    out = await mod.feishu_drive_add_comment(file_token="t", file_type="docx", content="hi")
    assert json.loads(out)["data"]["comment_id"] == "c9"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest examples/haitun-workspace/tests/test_feishu.py -q -p no:cacheprovider -c /dev/null`
Expected: FAIL — `ModuleNotFoundError: No module named 'feishu_drive'`

- [ ] **Step 3: 写实现**

创建 `examples/haitun-workspace/tools/feishu_drive.py`：

```python
"""Feishu/Lark drive comment tools — read and post comments on cloud documents.

Whole-document comments on a Feishu file (docx/doc/sheet/bitable). Use these to
review a doc's discussion, leave feedback, or reply in an existing thread.
Pair with ``feishu_doc_read`` (which reads the document body).
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_drive_add_comment(file_token: str, file_type: str, content: str) -> str:
    """Add a top-level (whole-document) comment on a Feishu/Lark document or file.

    Args:
        file_token: The file's token (from its URL).
        file_type: File type — one of docx, doc, sheet, bitable, file.
        content: The comment text to post.
    """
    return _f.dumps_result(await _f.add_comment_impl(file_token, file_type, content))


async def feishu_drive_list_comments(
    file_token: str, file_type: str, page_size: int = 50, page_token: str = ""
) -> str:
    """List whole-document comments on a Feishu/Lark file, most recent first.

    Args:
        file_token: The file's token (from its URL).
        file_type: File type — one of docx, doc, sheet, bitable, file.
        page_size: Max comments to return (default 50).
        page_token: Pagination cursor from a previous call's has_more result (optional).
    """
    return _f.dumps_result(
        await _f.list_comments_impl(file_token, file_type, page_size, page_token)
    )


async def feishu_drive_list_comment_replies(
    file_token: str, file_type: str, comment_id: str, page_size: int = 50, page_token: str = ""
) -> str:
    """List replies on a specific Feishu comment thread (whole-doc or local-selection).

    Args:
        file_token: The file's token (from its URL).
        file_type: File type — one of docx, doc, sheet, bitable, file.
        comment_id: The comment thread's ID (from feishu_drive_list_comments).
        page_size: Max replies to return (default 50).
        page_token: Pagination cursor from a previous call's has_more result (optional).
    """
    return _f.dumps_result(
        await _f.list_comment_replies_impl(
            file_token, file_type, comment_id, page_size, page_token
        )
    )


async def feishu_drive_reply_comment(
    file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str = ""
) -> str:
    """Post a reply on a Feishu comment thread, with an optional @-mention.

    Args:
        file_token: The file's token (from its URL).
        file_type: File type — one of docx, doc, sheet, bitable, file.
        comment_id: The comment thread's ID to reply under.
        content: The reply text.
        at_user_id: open_id/user_id to @-mention at the start of the reply (optional).
    """
    return _f.dumps_result(
        await _f.reply_comment_impl(file_token, file_type, comment_id, content, at_user_id)
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest examples/haitun-workspace/tests/test_feishu.py -q -p no:cacheprovider -c /dev/null`
Expected: PASS（11 passed）

- [ ] **Step 5: 提交**

```bash
git add examples/haitun-workspace/tools/feishu_drive.py examples/haitun-workspace/tests/test_feishu.py
git commit -m "feat(haitun): feishu_drive_* 工具薄壳（4 个评论工具）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `feishu_doc_read` — doc 全文读取（docx/doc/sheet 分派）+ 薄壳

**Files:**
- Modify: `examples/haitun-workspace/tools/_feishu_impl.py`（追加 doc 读取 impl + 3 个 builder）
- Create: `examples/haitun-workspace/tools/feishu_doc.py`
- Test: `examples/haitun-workspace/tests/test_feishu.py`（追加）

**Interfaces:**
- Consumes: `_invoke`, `_error`, `BaseRequest`, `HttpMethod`, `AccessTokenType`（Task 1/2）。
- Produces:
  - `def _build_docx_raw_request(document_id: str) -> BaseRequest`
  - `def _build_doc_raw_request(doc_token: str) -> BaseRequest`
  - `def _build_sheet_meta_request(spreadsheet_token: str) -> BaseRequest`
  - `def _build_sheet_values_request(spreadsheet_token: str, range_: str) -> BaseRequest`
  - `async def read_doc_impl(file_type: str, token: str, max_chars: int) -> dict` — 返回 `{"ok", "file_type", "token", "content", "truncated"}` 或 `_error`。
  - 工具 `feishu_doc_read`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_feishu.py`：

```python
async def test_doc_read_rejects_bad_file_type() -> None:
    result = await _impl.read_doc_impl("pdf", "tok", 20000)
    assert result["ok"] is False
    assert "docx" in result["message"]


async def test_doc_read_docx_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"content": "hello world"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_doc_impl("docx", "doc123", 20000)
    assert result["ok"] is True
    assert result["content"] == "hello world"
    assert cap.request.paths["document_id"] == "doc123"
    assert "docx/v1/documents" in cap.request.uri


async def test_doc_read_doc_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"content": "old doc body"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_doc_impl("doc", "dtok", 20000)
    assert result["content"] == "old doc body"
    assert "doc/v2" in cap.request.uri


async def test_doc_read_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"content": "x" * 100})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_doc_impl("docx", "t", 10)
    assert result["truncated"] is True
    assert len(result["content"]) == 10


def test_doc_tool_is_async_with_docstring() -> None:
    import inspect

    mod = importlib.import_module("feishu_doc")
    fn = mod.feishu_doc_read
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest examples/haitun-workspace/tests/test_feishu.py -q -p no:cacheprovider -c /dev/null`
Expected: FAIL — `AttributeError: module '_feishu_impl' has no attribute 'read_doc_impl'`

- [ ] **Step 3: 写实现**

在 `_feishu_impl.py` 末尾追加：

```python
def _raw_get(uri: str, path_name: str, path_value: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = uri
    req.paths[path_name] = path_value
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_docx_raw_request(document_id: str) -> BaseRequest:
    return _raw_get(
        "/open-apis/docx/v1/documents/:document_id/raw_content", "document_id", document_id
    )


def _build_doc_raw_request(doc_token: str) -> BaseRequest:
    return _raw_get("/open-apis/doc/v2/:doc_token/raw_content", "doc_token", doc_token)


def _build_sheet_meta_request(spreadsheet_token: str) -> BaseRequest:
    return _raw_get(
        "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query",
        "spreadsheet_token",
        spreadsheet_token,
    )


def _build_sheet_values_request(spreadsheet_token: str, range_: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values/:range"
    req.paths["spreadsheet_token"] = spreadsheet_token
    req.paths["range"] = range_
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _sheet_values_to_text(data: dict[str, Any]) -> str:
    grid = data.get("valueRange", {}).get("values", []) if isinstance(data, dict) else []
    lines: list[str] = []
    for row in grid if isinstance(grid, list) else []:
        cells = [("" if c is None else str(c)) for c in (row if isinstance(row, list) else [])]
        lines.append("\t".join(cells))
    return "\n".join(lines)


async def _read_sheet(token: str) -> dict[str, Any]:
    meta = await _invoke(_build_sheet_meta_request(token))
    if not meta["ok"]:
        return meta
    sheets = meta["data"].get("sheets", [])
    parts: list[str] = []
    for sh in sheets if isinstance(sheets, list) else []:
        sheet_id = sh.get("sheet_id") or sh.get("sheetId")
        title = sh.get("title", "")
        if not sheet_id:
            continue
        values = await _invoke(_build_sheet_values_request(token, str(sheet_id)))
        if not values["ok"]:
            return values
        parts.append(f"# {title}\n{_sheet_values_to_text(values['data'])}")
    return {"ok": True, "content": "\n\n".join(parts)}


async def read_doc_impl(file_type: str, token: str, max_chars: int) -> dict[str, Any]:
    ft = file_type.strip().lower()
    if ft == "docx":
        res = await _invoke(_build_docx_raw_request(token))
        content = res["data"].get("content", "") if res["ok"] else ""
    elif ft == "doc":
        res = await _invoke(_build_doc_raw_request(token))
        content = res["data"].get("content", "") if res["ok"] else ""
    elif ft == "sheet":
        res = await _read_sheet(token)
        content = res.get("content", "") if res["ok"] else ""
    else:
        return _error(f"Unsupported file_type {file_type!r}. Use one of: docx, doc, sheet.")

    if not res["ok"]:
        return res

    truncated = False
    if max_chars > 0 and len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
    return {
        "ok": True,
        "file_type": ft,
        "token": token,
        "content": content,
        "truncated": truncated,
    }
```

创建 `examples/haitun-workspace/tools/feishu_doc.py`：

```python
"""Feishu/Lark document reader — read the full text of a cloud document.

Given a file's type and token (both visible in its URL), return the document's
plain-text body. Supports new docs (docx), legacy docs (doc), and spreadsheets
(sheet). Pair with the feishu_drive_* tools to read or leave comments.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_doc_read(file_type: str, token: str, max_chars: int = 20000) -> str:
    """Read the full text content of a Feishu/Lark document (Docx, Doc, or Sheet).

    Given the document's file_type and token (both from its URL), fetch the body
    as plain text. For a sheet, every worksheet is read and tab-separated.

    Args:
        file_type: One of docx (new docs), doc (legacy docs), sheet (spreadsheets).
        token: The document/spreadsheet token from its URL.
        max_chars: Max characters to return (default 20000; guards the context window).
    """
    return _f.dumps_result(await _f.read_doc_impl(file_type, token, max_chars))
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest examples/haitun-workspace/tests/test_feishu.py -q -p no:cacheprovider -c /dev/null`
Expected: PASS（16 passed）

- [ ] **Step 5: 提交**

```bash
git add examples/haitun-workspace/tools/_feishu_impl.py examples/haitun-workspace/tools/feishu_doc.py examples/haitun-workspace/tests/test_feishu.py
git commit -m "feat(haitun): feishu_doc_read（docx/doc/sheet 全文读取）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 文档 + 全量验证

**Files:**
- Modify: `examples/haitun-workspace/AGENTS.md`（工具表加行 + 前置条件）

**Interfaces:**
- Consumes: 全部前序任务的成品。
- Produces: 无新代码接口；文档 + 通过全部 lint/type/test 门禁。

- [ ] **Step 1: 更新 AGENTS.md 工具表**

在 `examples/haitun-workspace/AGENTS.md` 第 58 行（`browser` 那行）之后插入：

```markdown
| `feishu_doc` (`feishu_doc.py` + `_feishu_impl.py`) | Read full text of a Feishu/Lark document. Tool `feishu_doc_read(file_type, token, max_chars)` supports docx/doc/sheet. Requires `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`. |
| `feishu_drive` (`feishu_drive.py` + `_feishu_impl.py`) | Read/post whole-document comments on a Feishu/Lark file. Tools `feishu_drive_add_comment`, `feishu_drive_list_comments`, `feishu_drive_list_comment_replies`, `feishu_drive_reply_comment`. Requires `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`. |
```

在前置条件段（约第 92 行 Serper 那条附近）加一行：

```markdown
- **Feishu tools**: set `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET` (same app as the Feishu channel). Reuses the `lark-channel-sdk` dependency; no extra install. If unset, the tools return `ok=false` (not fatal).
```

- [ ] **Step 2: 全量 lint + 类型 + 测试**

Run:
```bash
uv run ruff check examples/haitun-workspace/tools/feishu_doc.py examples/haitun-workspace/tools/feishu_drive.py examples/haitun-workspace/tools/_feishu_impl.py examples/haitun-workspace/tests/test_feishu.py
uv run ruff format --check examples/haitun-workspace/tools/feishu_doc.py examples/haitun-workspace/tools/feishu_drive.py examples/haitun-workspace/tools/_feishu_impl.py examples/haitun-workspace/tests/test_feishu.py
uv run ty check .
uv run pytest examples/haitun-workspace/tests/test_feishu.py -q -p no:cacheprovider -c /dev/null
```
Expected: ruff `All checks passed!` + `4 files already formatted`；ty 对 feishu 文件零报错（无关的既有报错如 Xlib/tray 忽略）；pytest `16 passed`。

- [ ] **Step 3: 修 lint/type/format 问题（若有）**

若 ruff format 报未格式化，运行 `uv run ruff format <file>` 后重跑 Step 2。若 ty 报 feishu 文件的类型问题，按报错修（常见：给 `list`/`dict` 加 `[str, Any]` 标注）。

- [ ] **Step 4: 提交文档**

```bash
git add examples/haitun-workspace/AGENTS.md
git commit -m "docs(haitun): AGENTS.md 增加 feishu 工具说明

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: 推送功能分支**

```bash
git -C /c/Users/12815/psi-agent-feishu-tools push origin add-feishu-tools-work:add-feishu-tools-work
```
Expected: 分支推到远端。不合 main（由用户决定）。

---

## 完成标准

- 5 个工具全部加载（`feishu_doc_read` + 4 个 `feishu_drive_*`），未初始化鉴权时返回 `ok=false` 而非崩溃。
- 16 个单元测试通过，不打真实飞书 API。
- ruff check + format + ty 全过。
- 零新增依赖：pyproject / nuitka.yml / pyinstaller.yml 未改。
- （可选）配好 `PSI_FEISHU_APP_ID/SECRET` 后手动 smoke：对一个真实 docx token 跑 `feishu_doc_read`。
