"""Handbook onboarding: welcome card -> form validate -> dual notify / resent card.

``feishu.hr.user_created`` triggers can ``fire=tool`` call
``handbook_onboarding_send_welcome``; Session injects ``event_payload_json``.
Card submit arrives as ``<feishu_card_action>``; agent calls
``handbook_onboarding_process_submit``.
"""

from __future__ import annotations

# ruff: noqa: E402
import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import yaml

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _runtime_paths as _paths
from feishu_message import feishu_message_send, feishu_message_send_card

_ACTION_SUBMIT = "handbook_submit"
_HANDLER = "handbook_onboarding_process_submit"
_DEFAULT_CONFIG = "config/handbook_onboarding.yaml"


async def handbook_onboarding_send_welcome(
    open_id: str = "",
    name: str = "",
    event_payload_json: str = "",
    fail_reason: str = "",
    attempt: int = 1,
) -> str:
    """Send the handbook welcome / re-confirm interactive card to a new hire.

    Prefer calling with empty ``open_id``/``name`` from a ``feishu.hr.user_created``
    trigger — Session injects ``event_payload_json`` from the envelope. Manual test:
    pass ``open_id`` (and optional ``name``) directly.

    Args:
        open_id: New hire Feishu open_id (ou_...). Empty → take from event_payload_json.
        name: Display name. Empty → take from payload or fall back to open_id.
        event_payload_json: JSON object from the event envelope payload (injected by Session).
        fail_reason: When re-sending after a failed submit, show this above the form.
        attempt: Card attempt number (1 = first welcome; >1 = retry card).
    """
    payload = _parse_json_object(event_payload_json)
    resolved_open_id = (open_id or "").strip() or str(payload.get("open_id") or "").strip()
    resolved_name = (name or "").strip() or str(payload.get("name") or "").strip() or resolved_open_id
    if not resolved_open_id:
        return _fail("open_id is required (pass it or provide event_payload_json with open_id)")

    cfg = await _load_config()
    card = _build_card(
        cfg,
        open_id=resolved_open_id,
        name=resolved_name,
        fail_reason=(fail_reason or "").strip(),
        attempt=max(1, int(attempt) if isinstance(attempt, int) else 1),
    )
    business = {
        "type": "handbook_onboarding",
        "open_id": resolved_open_id,
        "name": resolved_name,
        "attempt": max(1, int(attempt) if isinstance(attempt, int) else 1),
    }
    handlers = {_ACTION_SUBMIT: _HANDLER}
    return await feishu_message_send_card(
        resolved_open_id,
        json.dumps(card, ensure_ascii=False),
        "open_id",
        "",
        json.dumps(business, ensure_ascii=False),
        json.dumps(handlers, ensure_ascii=False),
    )


async def handbook_onboarding_process_submit(card_action_json: str = "") -> str:
    """Validate a handbook confirm card submit; notify both sides or send a new card.

    Call this when ``<feishu_card_action>`` has ``dispatch.handler`` =
    ``handbook_onboarding_process_submit``. Pass the **entire** JSON object inside
    the tag (not only ``action``). Cards are single-use: on failure this tool sends
    a **new** confirm card with the fail reason.

    Args:
        card_action_json: Full ``<feishu_card_action>`` payload JSON string.
    """
    envelope = _parse_json_object(card_action_json)
    if not envelope:
        return _fail("card_action_json must be a non-empty JSON object")

    dispatch = _as_dict(envelope.get("dispatch"))
    handler = str(dispatch.get("handler") or "").strip()
    matched = bool(dispatch.get("matched"))
    if handler and handler != _HANDLER:
        return _fail(f"unexpected handler {handler!r}; expected {_HANDLER!r}")
    if handler == _HANDLER and matched is False:
        return _fail("dispatch.matched is false; do not invent a handler")

    action = _as_dict(envelope.get("action"))
    form_value = _as_dict(action.get("form_value"))
    value = _as_dict(action.get("value"))
    action_name = str(value.get("action") or action.get("action_id") or "").strip()
    if action_name and action_name != _ACTION_SUBMIT:
        return _fail(f"unexpected action {action_name!r}")

    business = _as_dict(envelope.get("business_context"))
    open_id = str(business.get("open_id") or "").strip()
    name = str(business.get("name") or "").strip() or open_id
    attempt_raw = business.get("attempt", 1)
    attempt = 1
    if isinstance(attempt_raw, bool):
        attempt = 1
    elif isinstance(attempt_raw, int):
        attempt = max(1, attempt_raw)
    elif isinstance(attempt_raw, float):
        attempt = max(1, int(attempt_raw))
    elif isinstance(attempt_raw, str) and attempt_raw.strip():
        with suppress(ValueError):
            attempt = max(1, int(attempt_raw.strip()))

    source = _as_dict(envelope.get("source"))
    operator = str(source.get("operator_open_id") or source.get("open_id") or "").strip()
    if not operator:
        op = _as_dict(action.get("operator"))
        operator = str(op.get("open_id") or "").strip()
    if not open_id:
        open_id = operator
    if not open_id:
        return _fail("cannot resolve employee open_id from business_context / action")

    cfg = await _load_config()
    ok, reasons = _validate_form(cfg, form_value)
    if ok:
        return await _notify_pass(cfg, open_id=open_id, name=name)

    reason_text = "; ".join(reasons) if reasons else "validation failed"
    send_result = await handbook_onboarding_send_welcome(
        open_id=open_id,
        name=name,
        fail_reason=reason_text,
        attempt=attempt + 1,
    )
    return json.dumps(
        {
            "ok": False,
            "passed": False,
            "fail_reasons": reasons,
            "resent_card": True,
            "send_result": _parse_json_object(send_result) or send_result,
        },
        ensure_ascii=False,
    )


def _build_card(
    cfg: dict[str, Any],
    *,
    open_id: str,
    name: str,
    fail_reason: str,
    attempt: int,
) -> dict[str, Any]:
    company = str(cfg.get("company_name") or "Company").strip()
    title = str(cfg.get("welcome_title") or "Please confirm policies").strip()
    if attempt > 1:
        title = f"Retry: {title}"
    intro = str(cfg.get("welcome_intro") or "").strip()
    links_raw = cfg.get("handbook_links")
    links: list[Any] = links_raw if isinstance(links_raw, list) else []
    link_lines: list[str] = []
    for item in links:
        if not isinstance(item, dict):
            continue
        t = str(item.get("title") or "Doc").strip()
        u = str(item.get("url") or "").strip()
        if u:
            link_lines.append(f"- [{t}]({u})")
        elif t:
            link_lines.append(f"- {t}")
    md_parts = [
        f"**{name}**, welcome to **{company}**.",
        intro,
    ]
    if link_lines:
        md_parts.append("### Please read\n" + "\n".join(link_lines))
    if fail_reason:
        prefix = str(cfg.get("fail_employee_prefix") or "Not passed: ").strip()
        md_parts.insert(1, f"{prefix}{fail_reason}")
    md_parts.append(f"_Confirm card attempt {attempt} (each card is single-use)_")

    placeholder_ack = str(cfg.get("acked_placeholder") or "Have you read the policies?").strip()
    placeholder_text = str(cfg.get("confirm_input_placeholder") or "Type: I have read and agree").strip()
    submit_label = str(cfg.get("submit_button_label") or "Submit").strip()
    yes_label = str(cfg.get("acked_yes_label") or "Yes, I have read the policies").strip()
    no_label = str(cfg.get("acked_no_label") or "No").strip()

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title[:60]},
            "template": "orange" if fail_reason else "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "\n\n".join(p for p in md_parts if p)},
            },
            {
                "tag": "form",
                "name": "handbook_ack_form",
                "elements": [
                    {
                        "tag": "select_static",
                        "name": "acked",
                        "placeholder": {"tag": "plain_text", "content": placeholder_ack},
                        "options": [
                            {
                                "text": {"tag": "plain_text", "content": yes_label},
                                "value": "true",
                            },
                            {
                                "text": {"tag": "plain_text", "content": no_label},
                                "value": "false",
                            },
                        ],
                    },
                    {
                        "tag": "input",
                        "name": "confirm_text",
                        "placeholder": {"tag": "plain_text", "content": placeholder_text},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": submit_label},
                        "type": "primary",
                        "name": "submit",
                        "action_type": "form_submit",
                        "value": {
                            "action": _ACTION_SUBMIT,
                            "open_id": open_id,
                            "attempt": str(attempt),
                        },
                    },
                ],
            },
        ],
    }


def _validate_form(cfg: dict[str, Any], form_value: dict[str, Any]) -> tuple[bool, list[str]]:
    rules = cfg.get("required_form_fields")
    if not isinstance(rules, list) or not rules:
        return True, []
    reasons: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        field = str(rule.get("name") or "").strip()
        if not field:
            continue
        raw = form_value.get(field)
        text = "" if raw is None else str(raw).strip()
        fail_msg = str(rule.get("fail_message") or f"field {field} failed validation").strip()
        if "equals" in rule:
            expected = str(rule.get("equals") if rule.get("equals") is not None else "").strip()
            if text.casefold() != expected.casefold():
                reasons.append(fail_msg)
                continue
        if "contains" in rule:
            needle = str(rule.get("contains") if rule.get("contains") is not None else "").strip()
            if needle and needle not in text:
                reasons.append(fail_msg)
                continue
        need_nonempty = rule.get("required") is True or ("equals" not in rule and "contains" not in rule and not text)
        if need_nonempty and not text:
            reasons.append(fail_msg)
    return (len(reasons) == 0), reasons


async def _notify_pass(cfg: dict[str, Any], *, open_id: str, name: str) -> str:
    emp_text = str(cfg.get("pass_employee_text") or "Policy confirmation passed. Thank you.").strip()
    emp_result = await feishu_message_send(open_id, emp_text, "open_id")

    hr_id = os.environ.get("HAITUN_HANDBOOK_HR_NOTIFY_ID", "").strip() or str(cfg.get("hr_notify_id") or "").strip()
    hr_type = (
        os.environ.get("HAITUN_HANDBOOK_HR_NOTIFY_ID_TYPE", "").strip()
        or str(cfg.get("hr_notify_id_type") or "open_id").strip()
        or "open_id"
    )
    hr_result: Any = None
    if hr_id:
        tmpl = str(cfg.get("pass_hr_text_template") or "New hire {name} ({open_id}) passed policy confirmation.")
        hr_text = tmpl.format(name=name or open_id, open_id=open_id)
        hr_result = _parse_json_object(await feishu_message_send(hr_id, hr_text, hr_type)) or hr_text

    return json.dumps(
        {
            "ok": True,
            "passed": True,
            "employee_notify": _parse_json_object(emp_result) or emp_result,
            "hr_notify": hr_result,
            "hr_skipped": not bool(hr_id),
        },
        ensure_ascii=False,
    )


async def _load_config() -> dict[str, Any]:
    path = _paths.resolve_agent() / _DEFAULT_CONFIG
    try:
        text = await path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _as_dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _parse_json_object(raw: str | Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _fail(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)
