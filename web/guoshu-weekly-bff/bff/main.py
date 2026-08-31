"""FastAPI application: the four BFF duties (plan chapter 6.3).

Routes (mirroring the plan 7.4 external surface, prefixed under /api):

- POST /api/login          — dev-stage shared account, signs a session cookie
- POST /api/logout         — clears the cookie
- GET  /api/health         — self + Gateway reachability
- POST /api/sessions       — create a session (per-user workspace, AI bound)
- POST /api/sessions/{id}/chat       — SSE passthrough, rate-limited
- GET  /api/sessions/{id}/history    — passthrough
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .config import BffConfig, load_config
from .gateway_proxy import GatewayProxy
from .identity import IdentityStore, SessionSigner, compare_digest, workspace_for
from .ratelimit import SlidingWindowLimiter

COOKIE_NAME = "guoshu_weekly_session"

# P2-1 attachment limits: the BFF owns every inbound validation (plan 6.3), so
# a malformed or oversized upload dies here with a 4xx instead of inside the
# Gateway. The Gateway decodes the base64 and lands the blob on its own disk.
MAX_ATTACHMENTS_PER_MESSAGE = 5
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


@asynccontextmanager
async def _lifespan(app: FastAPI):
    config = load_config()
    app.state.config = config
    app.state.signer = SessionSigner(config.session_secret)
    app.state.identities = IdentityStore(config)
    app.state.limiter = SlidingWindowLimiter(config.rate_limit_per_minute)
    app.state.proxy = GatewayProxy(config)
    try:
        yield
    finally:
        await app.state.proxy.close()


app = FastAPI(title="guoshu-weekly-bff", lifespan=_lifespan)


def _config(request: Request) -> BffConfig:
    return request.app.state.config


def _current_user(request: Request, config: BffConfig = Depends(_config)) -> str:
    """Login-state check: no valid signed session -> 401, Gateway never sees it."""
    token = request.cookies.get(COOKIE_NAME, "")
    username = request.app.state.signer.verify(token) if token else None
    if not username:
        raise HTTPException(status_code=401, detail="not logged in")
    return username


@app.post("/api/login")
async def login(
    request: Request,
    response: Response,
    config: BffConfig = Depends(_config),
) -> dict[str, object]:
    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if username != config.dev_username or not compare_digest(password, config.dev_password):
        raise HTTPException(status_code=401, detail="bad credentials")
    token = request.app.state.signer.issue(username)
    # httponly: JS cannot read the cookie; SameSite=Lax: browser POSTs carry it.
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=12 * 3600)
    return {"ok": True, "username": username}


@app.post("/api/logout")
async def logout(
    response: Response,
    username: str = Depends(_current_user),
) -> dict[str, object]:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@app.get("/api/health")
async def health(request: Request, config: BffConfig = Depends(_config)) -> dict[str, object]:
    proxy: GatewayProxy = request.app.state.proxy
    gateway_ok = False
    try:
        response = await proxy.get("/defaults")
        gateway_ok = response.status_code == 200
    except Exception:
        gateway_ok = False
    return {"ok": True, "gateway": config.gateway_base_url, "gateway_ok": gateway_ok}


@app.post("/api/sessions")
async def create_session(
    request: Request,
    username: str = Depends(_current_user),
    config: BffConfig = Depends(_config),
) -> dict[str, object]:
    proxy: GatewayProxy = request.app.state.proxy
    # AI binding: the Gateway needs backend_type/backend_id or the Session
    # gets no model and every turn dies on localhost:80. Take the first AI.
    ais = (await proxy.get("/ais")).json()
    if not isinstance(ais, list) or not ais or not isinstance(ais[0], dict):
        raise HTTPException(status_code=503, detail="gateway has no AI service configured")
    payload: dict[str, object] = {
        "backend_type": "ai",
        "backend_id": ais[0]["id"],
        "workspace": workspace_for(username, config),
    }
    response = await proxy.post_json("/sessions", payload)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"gateway rejected session: {response.text[:200]}")
    return response.json()


# Answer-organisation instructions per plan 5.3. Worded as *organisation*
# preferences, never as a self-claimed identity — the agent-side prompt already
# rules that a claimed identity cannot widen data access (system.py「身份与颗粒度」),
# and these lines must not contradict that.
_IDENTITY_INSTRUCTION = {
    "领导": "回答组织方式采用领导视角:结论先行,给汇总、风险与滞后项、跨组对比,明细收在末尾。",
    "个人": "回答组织方式采用个人视角:过程优先,逐项列出当前状态与下一步。",
}
_PREFERENCE_INSTRUCTION = {
    "结论优先": "先给结论,再给依据与明细。",
    "过程优先": "按步骤展开过程,最后给结论。",
}


def _attachments(raw: object) -> list[dict[str, str]]:
    """Validate the frontend's ``files`` payload into Gateway blob chunks.

    Each attachment is ``{name, data}`` with ``data`` the bare base64 of the
    file. The Gateway decodes and lands the blob next to the conversation
    (gateway/_chat_manager.py); the BFF only validates count/size/encoding so
    bad input never reaches it. ``name`` is reduced to its basename — the
    upload path is decided by the Gateway, never by the client.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="files must be a list")
    if len(raw) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise HTTPException(status_code=400, detail=f"at most {MAX_ATTACHMENTS_PER_MESSAGE} attachments per message")
    attachments: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="each attachment must be an object")
        name = str(item.get("name", "")).strip()
        data = item.get("data")
        if not name or not isinstance(data, str):
            raise HTTPException(status_code=400, detail="attachment needs name and base64 data")
        try:
            size = len(base64.b64decode(data, validate=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="attachment data is not valid base64") from exc
        if size > MAX_ATTACHMENT_BYTES:
            raise HTTPException(status_code=413, detail=f"attachment exceeds {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB")
        attachments.append({"name": Path(name).name, "data": data})
    return attachments


def _gateway_chat_body(body: dict[str, object]) -> dict[str, object]:
    """Translate the frontend's messages shape into the Gateway's chunk shape.

    The Gateway chat endpoint speaks ``{"chunks": [{"type": "text",
    "text": ...}]}`` (see gateway/_chat_manager.py); the frontend speaks the
    plan's ``{"messages": [...]}``. The Session keeps its own history, so only
    the newest user message is forwarded. Attachments (P2-1) travel as blob
    chunks right after the text chunk.

    Identity/preference are demo-stage page-level toggles injected here as
    answer-organisation instructions. Production swaps the identity source for
    the login-derived role; the injection point stays.
    """
    messages = body.get("messages")
    if isinstance(messages, list):
        text = ""
        for message in reversed(messages):
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                text = message["content"]
                break
        instructions: list[str] = []
        identity = str(body.get("identity", "")).strip()
        if identity in _IDENTITY_INSTRUCTION:
            instructions.append(_IDENTITY_INSTRUCTION[identity])
        preference = str(body.get("preference", "")).strip()
        if preference in _PREFERENCE_INSTRUCTION:
            instructions.append(_PREFERENCE_INSTRUCTION[preference])
        if instructions and text:
            text = "【本次回答要求】" + "".join(instructions) + "\n\n问题:" + text
        chunks: list[dict[str, object]] = []
        if text:
            chunks.append({"type": "text", "text": text})
        chunks.extend({"type": "blob", **attachment} for attachment in _attachments(body.get("files")))
        return {"chunks": chunks}
    return body


@app.post("/api/sessions/{session_id}/chat")
async def chat(
    session_id: str,
    request: Request,
    username: str = Depends(_current_user),
    config: BffConfig = Depends(_config),
) -> StreamingResponse:
    limiter: SlidingWindowLimiter = request.app.state.limiter
    if not limiter.allow(username):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    body = _gateway_chat_body(await request.json())

    async def _stream() -> Any:
        async for line in request.app.state.proxy.stream_chat(session_id, body):
            yield line + b"\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.get("/api/sessions/{session_id}/history")
async def history(
    session_id: str,
    request: Request,
    username: str = Depends(_current_user),
) -> list[dict[str, object]]:
    """The Gateway returns the transcript as a JSON *array* — the annotation
    must match it or FastAPI rejects the response with a 500."""
    response = await request.app.state.proxy.get(f"/sessions/{session_id}/history")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"gateway rejected history: {response.text[:200]}")
    return response.json()


@app.get("/api/reports/weekly-summary")
async def weekly_summary(
    request: Request,
    username: str = Depends(_current_user),
) -> Response:
    """P1-1: generate the weekly summary Word document and download it.

    Deterministic: the BFF fetches aggregated calibers from the取数 service
    and lays them out itself — no model turn involved (plan 5.4).
    """
    from .report import build_summary_document, fetch_weekly_summary

    limiter: SlidingWindowLimiter = request.app.state.limiter
    if not limiter.allow(f"{username}:report"):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    try:
        data = await fetch_weekly_summary(request.app.state.config)
        document = build_summary_document(data)
    except Exception as exc:  # noqa: BLE001 - the caller gets an honest 502
        raise HTTPException(status_code=502, detail=f"report generation failed: {exc!r}") from exc
    filename = f"weekly-summary-{date.today().isoformat()}.docx"
    return Response(
        content=document,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/sessions/{session_id}/export")
async def export_history(
    session_id: str,
    request: Request,
    username: str = Depends(_current_user),
) -> Response:
    """P1-3: export the conversation history as Excel or PDF."""
    from .export import build_excel, build_pdf

    format = request.query_params.get("format", "excel").strip().lower()
    if format not in {"excel", "pdf"}:
        raise HTTPException(status_code=400, detail="format must be excel or pdf")

    response = await request.app.state.proxy.get(f"/sessions/{session_id}/history")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"gateway rejected history: {response.text[:200]}")
    payload = response.json()
    messages = payload if isinstance(payload, list) else []
    if not messages:
        raise HTTPException(status_code=404, detail="no history for this session")

    if format == "excel":
        content = build_excel(messages)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="conversation-{date.today().isoformat()}.xlsx"'},
        )
    content = build_pdf(messages)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="conversation-{date.today().isoformat()}.pdf"'},
    )
