"""Subject notices and durable notification receipts for confirmed cases."""

# ruff: noqa: RUF001

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar

import _feishu_impl
import anyio
from _assignment_display import resolve_people_display
from loguru import logger

from _positive_negative_list.models import CaseDraft, LedgerRecord


@dataclass(frozen=True)
class NotificationResult:
    ok: bool
    status: str
    message_id: str = ""
    error: str = ""
    target_results: tuple[dict[str, str], ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok, "status": self.status}
        if self.message_id:
            result["message_id"] = self.message_id
        if self.error:
            result["error"] = self.error
        return result


# Imported as a module-level name so offline tests and deployments can inject a
# transport without constructing a Feishu client.
send_message_impl = _feishu_impl.send_message_impl
send_card_impl = _feishu_impl.send_card_impl


def _receipt_path(root: str | Path, case_id: str) -> Path:
    if not case_id.strip():
        raise ValueError("case_id is required")
    directory = Path(root) / "positive-negative-list" / "receipts"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / f"{case_id}.json"


def _record_receipt_path(root: str | Path, record_id: str, subject_user_key: str) -> Path:
    """Return the private receipt path for one record/subject pair."""
    if not record_id.strip() or not subject_user_key.strip():
        raise ValueError("record and subject identity are required")
    digest = hashlib.sha256(f"{record_id}\n{subject_user_key}".encode()).hexdigest()
    directory = Path(root) / "positive-negative-list" / "notification-receipts"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / f"{digest}.json"


def _real_link(value: Any) -> str:
    """Accept only real clickable links as employee-visible 记录链接."""
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://")) else ""


def _notice_text(case: CaseDraft, public_record_id: str, subject_display: str = "姓名未解析") -> str:
    nature = {"positive": "正面行为", "negative": "负面行为"}.get(case.nature, case.nature)
    lines = [
        "正负面清单记录通知",
        f"涉事人：{subject_display}",
        f"发生时间：{case.occurred_at}",
        f"行为事实：{case.fact_summary}",
        f"行为性质：{nature}",
        f"分类：{case.category}",
    ]
    record_link = _real_link(public_record_id)
    if record_link:
        lines.append(f"记录链接：{record_link}")
    if case.nature == "negative":
        lines.extend(
            (
                f"正确做法：{case.correct_behavior}",
                f"立即补救：{case.immediate_remedy}",
                f"预防措施：{case.prevention}",
                "请直接在本私聊按三部分回复复盘：",
                "1. 客观原因：当时有哪些事实、约束或信息导致了这个结果？",
                "2. 补足动作：现在准备采取什么具体动作完成补救和闭环？",
                "3. 防止再犯：下次准备设置什么提醒、节点或工作方式？",
                "复盘只保存在个人私有草稿，不写回表格，也不产生分数或绩效结论。",
            )
        )
    return "\n".join(lines)


async def notice_text_with_names(case: CaseDraft, public_record_id: str) -> str:
    subject_display = await resolve_people_display(case.subject_user_key, _feishu_impl.get_users_batch_impl)
    return _notice_text(case, public_record_id, subject_display)


def _identity_parts(value: str) -> list[str]:
    return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]


def _trusted_identities(value: str) -> tuple[list[str], str | None]:
    identities = _identity_parts(value)
    if not identities:
        return [], "identity is required"
    if any(not (identity.startswith("ou_") or identity.startswith("user_")) for identity in identities):
        return [], "display names cannot be used as Feishu message identities"
    return list(dict.fromkeys(identities)), None


def _record_content_gaps(record: LedgerRecord) -> tuple[str, ...]:
    required = {
        "occurred_at": record.occurred_at,
        "nature": record.nature,
        "category": record.category,
        "fact_summary": record.fact_summary,
    }
    return tuple(name for name, value in required.items() if not value.strip())


def _record_notice_text(record: LedgerRecord, subject_display: str = "姓名未解析") -> str:
    nature = {"positive": "正面行为", "negative": "负面行为"}.get(record.nature, record.nature)
    lines = [
        "正负面清单记录通知",
        f"涉事人：{subject_display}",
        f"发生时间：{record.occurred_at}",
        f"行为事实：{record.fact_summary}",
        f"行为性质：{nature}",
        f"分类：{record.category}",
    ]
    record_link = _real_link(record.record_link or record.fields.get("record_link") or record.fields.get("记录链接"))
    if record_link:
        lines.append(f"记录链接：{record_link}")
    if record.evidence_sources:
        lines.append(f"证据来源：{'、'.join(record.evidence_sources)}")
    if record.nature == "negative":
        lines.extend(
            (
                f"正确做法：{record.correct_behavior or '发现问题后及时同步现状、影响和补救方案'}",
                f"立即补救：{record.immediate_remedy or '请尽快补充同步，并明确下一步和时间点'}",
                f"预防措施：{record.prevention or '请在后续任务中设置明确的反馈节点'}",
                "请直接在本私聊按三部分回复：客观原因、补足动作、防止再犯。",
                "复盘只保存在个人私有草稿，不写回表格。",
            )
        )
    else:
        lines.append("这条记录用于沉淀可复用的工作方式，感谢你的实践和贡献。")
    return "\n".join(lines)


def _record_card_context(record: LedgerRecord) -> str:
    return json.dumps(
        {
            "kind": "pn_record_notice",
            "record_id": record.record_id,
            "subject_user_key": record.subject_user_key,
            "record": record.to_mapping(),
        },
        ensure_ascii=False,
    )


def _record_card_handlers(record: LedgerRecord) -> str:
    handlers = {"pn_record_review_start": "positive_negative_case_remind"} if record.nature == "negative" else {}
    return json.dumps(handlers, ensure_ascii=False)


async def record_notice_text_with_names(record: LedgerRecord) -> str:
    subject_display = await resolve_people_display(record.subject_user_key, _feishu_impl.get_users_batch_impl)
    return _record_notice_text(record, subject_display)


def render_record_notice_card(record: LedgerRecord, subject_display: str, *, notice_id: str = "") -> dict[str, Any]:
    """Render the employee-facing notice with the PR #843 Card 2.0 grammar."""
    negative = record.nature == "negative"
    nature = "负面行为" if negative else "正面行为"
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": f"**正负面清单 · 记录通知**\n对象：{subject_display} · {nature}",
        },
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**判断**　{record.fact_summary}"},
        {"tag": "markdown", "content": f"**发生时间**　{record.occurred_at}　·　**分类**　{record.category}"},
    ]
    if negative:
        elements.extend(
            [
                {
                    "tag": "markdown",
                    "content": (
                        f"**正确做法 · 建议**　{record.correct_behavior or '发现风险后及时同步现状、影响和补救方案。'}"
                    ),
                },
                {
                    "tag": "markdown",
                    "content": f"**立即补救**　{record.immediate_remedy or '补充同步并明确下一步和时间点。'}",
                },
                {
                    "tag": "markdown",
                    "content": f"**预防措施**　{record.prevention or '设置明确的节点、验收和风险反馈提醒。'}",
                },
            ]
        )
    else:
        elements.append({"tag": "markdown", "content": "这条记录用于沉淀可复用的工作方式，感谢你的实践和贡献。"})
    if negative:
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": "开始复盘"},
                                    "type": "primary",
                                    "behaviors": [
                                        {
                                            "type": "callback",
                                            "value": {
                                                "action": "pn_record_review_start",
                                                "record_id": record.record_id,
                                                "subject_user_key": record.subject_user_key,
                                                "notice_id": notice_id or record.record_id,
                                                "record": record.to_mapping(),
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                },
            ]
        )
    return {
        "schema": "2.0",
        "config": {"width_mode": "regular"},
        "header": {
            "title": {"tag": "plain_text", "content": "正负面清单 · 记录通知"},
            "template": "red" if negative else "green",
        },
        "body": {"elements": elements},
    }


async def _write_receipt_async(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a receipt without blocking the async event loop."""
    temporary = anyio.Path(f"{path}.tmp")
    await temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    await temporary.chmod(0o600)
    await temporary.replace(path)


class NotificationSender:
    _locks: ClassVar[dict[tuple[int, str, str, str], asyncio.Lock]] = {}

    def __init__(self, appdata_root: str | Path | None = None) -> None:
        self.appdata_root = Path(appdata_root) if appdata_root is not None else None
        self._notice_text_cache: dict[str, str] = {}
        self._notice_card_cache: dict[str, dict[str, str]] = {}
        self._notice_record_cache: dict[str, dict[str, dict[str, Any]]] = {}

    async def send_subject_notice(self, case: CaseDraft, public_record_id: str) -> NotificationResult:
        loop = asyncio.get_running_loop()
        key = (id(loop), str(self.appdata_root or ""), f"case:{case.case_id}", "")
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._send_subject_notice_locked(case, public_record_id)

    async def _send_subject_notice_locked(self, case: CaseDraft, public_record_id: str) -> NotificationResult:
        subjects, subject_error = _trusted_identities(case.subject_user_key)
        if subject_error:
            return NotificationResult(False, "notification_identity_unresolved", error=subject_error)
        _, reporter_error = _trusted_identities(case.reporter_user_key)
        if reporter_error:
            status = (
                "notification_reporter_identity_missing"
                if not _identity_parts(case.reporter_user_key)
                else "notification_reporter_identity_unresolved"
            )
            return NotificationResult(False, status, error=reporter_error)
        # Phase 1: build every recipient snapshot and card before any network
        # call, then persist a case-level receipt that already carries the
        # cards.  A crash in the middle of phase 2 therefore retries with the
        # original card instead of degrading to plain text, and an interrupted
        # process never leaves "message received, no state" behind.
        prepared: list[tuple[str, LedgerRecord, str, str]] = []
        for identity in subjects:
            subject_display = await resolve_people_display(identity, _feishu_impl.get_users_batch_impl)
            record = LedgerRecord.from_mapping(
                {
                    "record_id": public_record_id,
                    "case_id": case.case_id,
                    "reporter_user_key": case.reporter_user_key,
                    "subject_user_key": identity,
                    "occurred_at": case.occurred_at,
                    "nature": case.nature,
                    "category": case.category,
                    "fact_summary": case.fact_summary,
                    "evidence_sources": list(case.evidence_sources),
                    "correct_behavior": case.correct_behavior,
                    "immediate_remedy": case.immediate_remedy,
                    "prevention": case.prevention,
                    "record_link": public_record_id,
                }
            )
            text = _record_notice_text(record, subject_display)
            card = render_record_notice_card(record, subject_display, notice_id=public_record_id)
            card_json = json.dumps(card, ensure_ascii=False)
            self._notice_text_cache[case.case_id] = text
            self._notice_card_cache.setdefault(case.case_id, {})[identity] = card_json
            self._notice_record_cache.setdefault(case.case_id, {})[identity] = record.to_mapping()
            prepared.append((identity, record, text, card_json))
        if not prepared:
            return NotificationResult(False, "notification_pending_retry", error="no notification target")
        if self.appdata_root is not None:
            self.save_receipt(
                case,
                public_record_id,
                NotificationResult(False, "notification_pending_retry", error="notification delivery pending"),
            )
        # Phase 2: deliver per recipient; every attempt is durably recorded in
        # its own record receipt (card JSON included) as it happens.
        attempts: list[tuple[str, NotificationResult]] = []
        for identity, record, text, card_json in prepared:
            try:
                response = await send_card_impl(
                    identity,
                    card_json,
                    "open_id",
                    "",
                    _record_card_context(record),
                    _record_card_handlers(record),
                    False,
                )
            except Exception as exc:  # transport failures are retryable
                result = NotificationResult(
                    False,
                    "notification_pending_retry",
                    error=f"{type(exc).__name__}: {exc}",
                )
            else:
                if not isinstance(response, dict) or not response.get("ok"):
                    message = response.get("message") if isinstance(response, dict) else "notification failed"
                    result = NotificationResult(
                        False, "notification_pending_retry", error=str(message or "notification failed")
                    )
                else:
                    result = NotificationResult(
                        True, "notification_sent", message_id=str(response.get("message_id") or "")
                    )
            attempts.append((identity, result))
            logger.info(f"pnl notice: case={case.case_id} subject={identity} status={result.status}")
            if self.appdata_root is not None:
                self.save_record_receipt(record, result, notice_text=text, card_json=card_json)
        results = [result for _, result in attempts]
        target_results = tuple(
            {
                "subject_user_key": identity,
                "ok": str(result.ok).lower(),
                "status": result.status,
                "message_id": result.message_id,
                "error": result.error,
            }
            for identity, result in attempts
        )
        if not results:
            return NotificationResult(False, "notification_pending_retry", error="no notification target")
        failures = [result for result in results if not result.ok]
        if failures:
            return replace(failures[0], target_results=target_results)
        return replace(results[0], target_results=target_results)

    async def send_record_notice(self, record: LedgerRecord, force: bool = False) -> NotificationResult:
        """Send an employee-readable notice for an existing public record.

        Record notices use a digest of ``record_id`` and the subject identity, so
        repeated requests are idempotent without touching the public table.
        """
        subjects, subject_error = _trusted_identities(record.subject_user_key)
        if subject_error:
            status = (
                "notification_identity_missing"
                if not _identity_parts(record.subject_user_key)
                else "notification_identity_unresolved"
            )
            return NotificationResult(False, status, error=subject_error)
        if not record.record_id.strip():
            return NotificationResult(False, "record_identity_missing", error="record ID is required")
        gaps = _record_content_gaps(record)
        if gaps:
            return NotificationResult(False, "notification_content_incomplete", error="missing: " + ", ".join(gaps))

        reporters, reporter_error = _trusted_identities(record.reporter_user_key)
        if reporter_error:
            status = (
                "notification_reporter_identity_missing"
                if not _identity_parts(record.reporter_user_key)
                else "notification_reporter_identity_unresolved"
            )
            return NotificationResult(False, status, error=reporter_error)
        targets = [identity for identity in subjects if identity not in reporters]
        if not targets:
            return NotificationResult(True, "skipped_same_person")

        results: list[NotificationResult] = []
        for identity in targets:
            one_record = replace(record, subject_user_key=identity)
            results.append(await self._send_one_record_notice(one_record, force=force))
        failures = [result for result in results if not result.ok]
        if failures:
            return failures[0]
        if all(result.status == "already_sent" for result in results):
            return results[0]
        return next((result for result in results if result.message_id), results[0])

    async def _send_one_record_notice(self, record: LedgerRecord, force: bool = False) -> NotificationResult:
        loop = asyncio.get_running_loop()
        key = (id(loop), str(self.appdata_root or ""), record.record_id, record.subject_user_key)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._send_one_record_notice_locked(record, force=force)

    async def _send_one_record_notice_locked(self, record: LedgerRecord, force: bool = False) -> NotificationResult:

        existing: dict[str, Any] | None = None
        if self.appdata_root is not None and not force:
            existing = self._read_record_receipt(record.record_id, record.subject_user_key)
            if existing and existing.get("notification_status") == "notification_sent":
                return NotificationResult(
                    True,
                    "already_sent",
                    message_id=str(existing.get("notification_message_id") or ""),
                )

        subject_display = await resolve_people_display(record.subject_user_key, _feishu_impl.get_users_batch_impl)
        text = _record_notice_text(record, subject_display)
        card = render_record_notice_card(record, subject_display, notice_id=record.record_id)
        try:
            response = await send_card_impl(
                record.subject_user_key,
                json.dumps(card, ensure_ascii=False),
                "open_id",
                "",
                _record_card_context(record),
                _record_card_handlers(record),
                False,
            )
        except Exception as exc:  # transport failures are retryable
            result = NotificationResult(False, "notification_pending_retry", error=f"{type(exc).__name__}: {exc}")
            if self.appdata_root is not None:
                self.save_record_receipt(
                    record,
                    result,
                    notice_text=text,
                    card_json=json.dumps(card, ensure_ascii=False),
                )
            return result
        if not isinstance(response, dict) or not response.get("ok"):
            message = response.get("message") if isinstance(response, dict) else "notification failed"
            result = NotificationResult(
                False, "notification_pending_retry", error=str(message or "notification failed")
            )
            if self.appdata_root is not None:
                self.save_record_receipt(
                    record,
                    result,
                    notice_text=text,
                    card_json=json.dumps(card, ensure_ascii=False),
                )
            return result
        result = NotificationResult(True, "notification_sent", str(response.get("message_id") or ""))
        if self.appdata_root is not None:
            self.save_record_receipt(record, result, notice_text=text, card_json=json.dumps(card, ensure_ascii=False))
        return result

    def _read_record_receipt(self, record_id: str, subject_user_key: str) -> dict[str, Any] | None:
        if self.appdata_root is None:
            return None
        try:
            receipt = _record_receipt_path(self.appdata_root, record_id, subject_user_key)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
        except FileNotFoundError, json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def save_record_receipt(
        self, record: LedgerRecord, result: NotificationResult, *, notice_text: str | None = None, card_json: str = ""
    ) -> Path:
        if self.appdata_root is None:
            raise ValueError("appdata_root is required to save a receipt")
        path = _record_receipt_path(self.appdata_root, record.record_id, record.subject_user_key)
        payload = {
            "record_id": record.record_id,
            "subject_user_key": record.subject_user_key,
            "record": record.to_mapping(),
            "reporter_user_key": record.reporter_user_key,
            "notification_status": result.status,
            "notification_message_id": result.message_id,
            "notification_error": result.error,
            "notice_text": notice_text or _record_notice_text(record),
            "card_json": card_json,
            "updated_at": time.time(),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        return path

    def load_record_receipt(self, record_id: str, subject_user_key: str) -> LedgerRecord | None:
        """Recover a normalized record from a private notification receipt."""
        payload = self._read_record_receipt(record_id, subject_user_key)
        if not isinstance(payload, dict):
            return None
        raw_record = payload.get("record")
        if not isinstance(raw_record, dict):
            return None
        try:
            return LedgerRecord.from_mapping(raw_record)
        except TypeError, ValueError, KeyError:
            return None

    async def retry_record_notice(self, record_id: str, subject_user_key: str) -> NotificationResult:
        if self.appdata_root is None:
            raise ValueError("appdata_root is required to retry a receipt")
        loop = asyncio.get_running_loop()
        key = (id(loop), str(self.appdata_root), record_id, subject_user_key)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._retry_record_notice_locked(record_id, subject_user_key)

    async def _retry_record_notice_locked(self, record_id: str, subject_user_key: str) -> NotificationResult:
        payload = self._read_record_receipt(record_id, subject_user_key)
        if payload is None:
            return NotificationResult(False, "receipt_not_found", error="notification receipt not found")
        if payload.get("notification_status") == "notification_sent":
            return NotificationResult(True, "already_sent", str(payload.get("notification_message_id") or ""))
        text = str(payload.get("notice_text") or "")
        try:
            card_json = str(payload.get("card_json") or "")
            if card_json:
                raw_record = payload.get("record")
                record = LedgerRecord.from_mapping(raw_record) if isinstance(raw_record, dict) else None
                response = await send_card_impl(
                    subject_user_key,
                    card_json,
                    "open_id",
                    "",
                    _record_card_context(record)
                    if record is not None
                    else json.dumps({"kind": "pn_record_notice", "record_id": record_id}, ensure_ascii=False),
                    _record_card_handlers(record) if record is not None else "{}",
                    False,
                )
            else:
                response = await send_message_impl(subject_user_key, text, "open_id")
        except Exception as exc:
            result = NotificationResult(False, "notification_pending_retry", error=f"{type(exc).__name__}: {exc}")
            self.save_record_receipt_from_payload(payload, result)
            return result
        if not isinstance(response, dict) or not response.get("ok"):
            message = response.get("message") if isinstance(response, dict) else "notification failed"
            result = NotificationResult(
                False, "notification_pending_retry", error=str(message or "notification failed")
            )
            self.save_record_receipt_from_payload(payload, result)
            return result
        result = NotificationResult(True, "notification_sent", str(response.get("message_id") or ""))
        self.save_record_receipt_from_payload(payload, result)
        return result

    def save_record_receipt_from_payload(self, payload: dict[str, Any], result: NotificationResult) -> Path:
        if self.appdata_root is None:
            raise ValueError("appdata_root is required to save a receipt")
        path = _record_receipt_path(
            self.appdata_root,
            str(payload.get("record_id") or ""),
            str(payload.get("subject_user_key") or ""),
        )
        updated = dict(payload)
        updated.update(
            {
                "notification_status": result.status,
                "notification_message_id": result.message_id,
                "notification_error": result.error,
                "updated_at": time.time(),
            }
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(updated, ensure_ascii=False), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        return path

    def save_receipt(self, case: CaseDraft, public_record_id: str, result: NotificationResult) -> Path:
        if self.appdata_root is None:
            raise ValueError("appdata_root is required to save a receipt")
        path = _receipt_path(self.appdata_root, case.case_id)
        reporter_parts = _identity_parts(case.reporter_user_key)
        targets = [dict(item) for item in result.target_results]
        reporters_trusted = bool(reporter_parts) and all(
            identity.startswith(("ou_", "user_")) for identity in reporter_parts
        )
        if not targets and result.status == "notification_pending_retry" and reporters_trusted:
            targets = [
                {
                    "subject_user_key": identity,
                    "ok": "false",
                    "status": "notification_pending_retry",
                    "message_id": "",
                    "error": "notification delivery pending",
                }
                for identity in _identity_parts(case.subject_user_key)
            ]
        payload = {
            "case_id": case.case_id,
            "writer_user_key": case.writer_user_key,
            "reporter_user_key": case.reporter_user_key,
            "public_record_id": public_record_id,
            "notification_status": result.status,
            "notification_message_id": result.message_id,
            "notification_error": result.error,
            "notice_text": self._notice_text_cache.get(case.case_id) or _notice_text(case, public_record_id),
            "subject_user_key": case.subject_user_key,
            "notification_targets": targets,
            "notification_cards": self._notice_card_cache.get(case.case_id, {}),
            "notification_records": self._notice_record_cache.get(case.case_id, {}),
            "updated_at": time.time(),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        return path

    async def retry_notification(self, case_id: str) -> NotificationResult:
        if self.appdata_root is None:
            raise ValueError("appdata_root is required to retry a receipt")
        path = _receipt_path(self.appdata_root, case_id)
        loop = asyncio.get_running_loop()
        key = (id(loop), str(self.appdata_root), f"case:{case_id}", "")
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._retry_notification_locked(path)

    async def _retry_notification_locked(self, path: Path) -> NotificationResult:
        try:
            payload = json.loads(await anyio.Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError, json.JSONDecodeError:
            return NotificationResult(False, "receipt_not_found", error="notification receipt not found")
        if not isinstance(payload, dict):
            return NotificationResult(False, "receipt_invalid", error="notification receipt is invalid")
        if payload.get("notification_status") == "notification_sent":
            return NotificationResult(True, "already_sent", str(payload.get("notification_message_id") or ""))
        if payload.get("notification_status") not in {"notification_pending_retry", "skipped_same_person"}:
            return NotificationResult(
                False,
                str(payload.get("notification_status") or "receipt_invalid"),
                error=str(payload.get("notification_error") or "notification is not retryable"),
            )
        targets = payload.get("notification_targets")
        if not (isinstance(targets, list) and targets):
            # Receipts written before a process interruption may only contain
            # the subject identity. Reconstruct per-recipient targets so a
            # multi-subject case is never sent as one invalid open_id.
            subjects = _identity_parts(str(payload.get("subject_user_key") or ""))
            targets = [
                {
                    "subject_user_key": identity,
                    "ok": "false",
                    "status": "notification_pending_retry",
                    "message_id": "",
                    "error": "notification delivery pending",
                }
                for identity in subjects
            ]
        if targets:
            return await self._retry_case_targets(path, payload, targets)
        return NotificationResult(True, "skipped_same_person")

    async def _retry_case_targets(self, path: Path, payload: dict[str, Any], targets: list[Any]) -> NotificationResult:
        text = str(payload.get("notice_text") or "")
        cards_raw = payload.get("notification_cards")
        cards: dict[str, Any] = cards_raw if isinstance(cards_raw, dict) else {}
        records_raw = payload.get("notification_records")
        records: dict[str, Any] = records_raw if isinstance(records_raw, dict) else {}
        updated_targets: list[dict[str, str]] = []
        failures: list[NotificationResult] = []
        message_id = ""
        record_id = str(payload.get("public_record_id") or "").strip()
        for raw_target in targets:
            if not isinstance(raw_target, dict):
                return NotificationResult(False, "receipt_invalid", error="notification target is invalid")
            identity = str(raw_target.get("subject_user_key") or "").strip()
            if not identity:
                return NotificationResult(False, "receipt_invalid", error="notification target identity is missing")
            if str(raw_target.get("status") or "") == "notification_sent":
                updated_targets.append({str(key): str(value) for key, value in raw_target.items()})
                continue
            # A target whose per-recipient receipt already reports success must
            # not receive a second copy after a process interruption.
            per_target = self._read_record_receipt(record_id, identity) if record_id else None
            if per_target is not None and per_target.get("notification_status") == "notification_sent":
                logger.info(f"pnl notice retry: skip already-sent subject={identity} record={record_id}")
                result = NotificationResult(
                    True, "notification_sent", message_id=str(per_target.get("notification_message_id") or "")
                )
                if result.ok:
                    message_id = message_id or result.message_id
                else:
                    failures.append(result)
                updated_targets.append(
                    {
                        "subject_user_key": identity,
                        "ok": str(result.ok).lower(),
                        "status": result.status,
                        "message_id": result.message_id,
                        "error": result.error,
                    }
                )
                continue
            try:
                card_json = str(cards.get(identity) or "")
                raw_record = records.get(identity)
                if not card_json and per_target is not None:
                    card_json = str(per_target.get("card_json") or "")
                if not isinstance(raw_record, dict) and per_target is not None:
                    raw_record = per_target.get("record")
                record = LedgerRecord.from_mapping(raw_record) if isinstance(raw_record, dict) else None
                if card_json:
                    response = await send_card_impl(
                        identity,
                        card_json,
                        "open_id",
                        "",
                        _record_card_context(record)
                        if record is not None
                        else json.dumps({"kind": "pn_record_notice"}, ensure_ascii=False),
                        _record_card_handlers(record) if record is not None else "{}",
                        False,
                    )
                else:
                    response = await send_message_impl(identity, text, "open_id")
            except Exception as exc:
                result = NotificationResult(False, "notification_pending_retry", error=f"{type(exc).__name__}: {exc}")
            else:
                if not isinstance(response, dict) or not response.get("ok"):
                    message = response.get("message") if isinstance(response, dict) else "notification failed"
                    result = NotificationResult(
                        False, "notification_pending_retry", error=str(message or "notification failed")
                    )
                else:
                    result = NotificationResult(True, "notification_sent", str(response.get("message_id") or ""))
            if result.ok:
                message_id = message_id or result.message_id
            else:
                failures.append(result)
            updated_targets.append(
                {
                    "subject_user_key": identity,
                    "ok": str(result.ok).lower(),
                    "status": result.status,
                    "message_id": result.message_id,
                    "error": result.error,
                }
            )
        status = "notification_pending_retry" if failures else "notification_sent"
        error = failures[0].error if failures else ""
        payload.update(
            {
                "notification_status": status,
                "notification_message_id": message_id,
                "notification_error": error,
                "notification_targets": updated_targets,
                "updated_at": time.time(),
            }
        )
        await _write_receipt_async(path, payload)
        return NotificationResult(not bool(failures), status, message_id=message_id, error=error)

    retry_subject_notice = retry_notification


__all__ = ["NotificationResult", "NotificationSender"]
