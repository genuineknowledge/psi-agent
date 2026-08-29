"""Handle one tick on a TODO-list card: mark the linked Feishu task complete.

Dispatched by the card's ``action_handlers`` map, one row at a time. The row's visual
state (``● ~~已完成~~``) is already applied by the Channel before this runs, so this tool
only has to move the *authoritative* state — the Feishu task — and report what happened.
"""

from __future__ import annotations

import json
import time
from typing import Any

import _feishu_api_impl as _api
import _feishu_impl as _f
import _review_card_impl as _review
import _todo_card_impl as _impl
import anyio


def _parse_action(card_action_json: str) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(card_action_json, str) or not card_action_json.strip():
        return None, "[Error] card_action_json is required (pass the <feishu_card_action> payload)"
    try:
        payload = json.loads(card_action_json)
    except ValueError as exc:
        return None, f"[Error] card_action_json is not valid JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "[Error] card_action_json must be a JSON object"
    return payload, ""


def _action_value(payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action")
    value = action.get("value") if isinstance(action, dict) else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


async def feishu_todo_card_tick(card_action_json: str = "", user_key: str = "") -> str:
    """Mark the Feishu task behind one ticked TODO row complete.

    The handler for ``feishu_todo_card_send`` rows. Session injects the
    ``<feishu_card_action>`` payload as ``card_action_json``; the clicked row's
    ``task_guid`` and title come from its ``value``.

    Completion is written as ``task.completed_at`` = now in **milliseconds** with
    ``update_fields: ["completed_at"]`` — without ``update_fields`` Feishu returns success
    and changes nothing. A row carrying no ``task_guid`` is reported as ticked-only, since
    there is no task to move; that is not an error.

    The Channel already gave the row an instant generic "consumed" look before this runs.
    This tool then edits the card a second time (``feishu_message_edit_card``, via
    ``_todo_card_impl._apply_row_transition``) to replace that with the real
    struck-through row **and a 「撤销」button** for the next round — unless this row has
    used up all ``_todo_card_impl._UNDO_ROUNDS``, in which case it locks in as done with
    no further button. After the tick, it also sends the row's mentor a **review card**
    (1-5 score buttons + comment form + 「打回重做」) via ``_review_card_impl`` — the
    result is reported in ``review_card`` (``skipped`` when the row has no mentor or
    no ledger wiring). Do not announce the click in chat; reply only if the task update
    failed.

    Fast clicking is coalesced by the Channel: if the payload arrives wrapped in
    ``<feishu_card_action_batch>``, call this tool once per ``<feishu_card_action>`` inside
    it (skipping one silently loses that task's completion), then send at most one summary
    reply for the whole batch.

    Args:
        card_action_json: The ``<feishu_card_action>`` JSON (injected by Session).
        user_key: The clicker's open_id. Pass it so the task is completed as that person
            when the bot's own token is not a task member.
    """
    payload, error = _parse_action(card_action_json)
    if payload is None:
        return error
    value = _action_value(payload)
    task_guid = str(value.get("task_guid") or "").strip()
    title = str(value.get("todo_title") or "").strip() or "该待办"
    index_raw = value.get("todo_index")
    index = int(index_raw) if isinstance(index_raw, int) else 0
    action_id = str(value.get("action") or "")
    round_ = _impl._round_of(action_id)

    task_updated = False
    task_result: dict[str, Any] | None = None
    message_id = str(payload.get("message_id") or "")
    card_edit_status = "skipped_no_message_id"

    # 构造重建后的卡片(纯本地),再与任务 PATCH / 台账更新**并行**发出 ——
    # 三个飞书 API 串行时每次点击要等 1.5~2s,并行后只等最慢的那一个。
    new_card: dict[str, Any] | None = None
    if message_id:
        card_edit_status, new_card = _impl._prepare_row_transition(
            payload=payload,
            value=value,
            index=index,
            new_done=True,
            new_round=round_,
            fallback_title=title,
            fallback_task_guid=task_guid,
        )

    async def _safe(coro: Any) -> dict[str, Any]:
        try:
            return await coro
        except Exception as e:
            return {"ok": False, "error": f"{e!r}"}

    async def _patch_task() -> dict[str, Any]:
        return await _api.call_api_impl(
            "PATCH",
            "/open-apis/task/v2/tasks/:task_guid",
            body_json=json.dumps(
                {"task": {"completed_at": str(int(time.time() * 1000))}, "update_fields": ["completed_at"]},
                ensure_ascii=False,
            ),
            paths_json=json.dumps({"task_guid": task_guid}, ensure_ascii=False),
            prefer="tenant",
            user_key=user_key,
        )

    async def _edit_card() -> dict[str, Any]:
        if new_card is None:
            return {"ok": False, "skipped": True}
        return await _f.edit_card_impl(message_id, json.dumps(new_card, ensure_ascii=False), user_key)

    # jobs 顺序: [ledger, edit_card, (task)] — outcomes 按此序解包
    jobs: list[Any] = [_impl._sync_ledger_status(value, "已交付", user_key=user_key), _edit_card()]
    if task_guid:
        jobs.append(_patch_task())
    outcomes: list[Any] = [None] * len(jobs)

    async def _run(idx: int, coro: Any) -> None:
        outcomes[idx] = await _safe(coro)

    async with anyio.create_task_group() as tg:
        for i, job in enumerate(jobs):
            tg.start_soon(_run, i, job)

    if task_guid:
        task_result = dict(outcomes[2])
        task_updated = bool(task_result.get("ok"))
    else:
        # 行内无 task_guid:按契约返回 task_updated=false 并说明,不是错误。
        task_result = {}
        task_updated = False
    # new_card 为 None 时保留 _prepare_row_transition 返回的 skipped_* 状态,
    # 不要用 edit_failed 覆盖(那是「有卡片但发失败」才有的状态)。
    if new_card is not None:
        edit_outcome = outcomes[1]
        card_edit_status = "ok" if edit_outcome.get("ok") else f"edit_failed: {edit_outcome}"

    # 交付后向 mentor 发评价卡(1-5 分按钮 + 评语表单 + 打回重做)——查台账行拿
    # mentor 再发,行无 mentor 或未接台账时静默跳过。发卡失败不阻塞 tick 本身。
    review_outcome = await _safe(
        _review._send_review_card(value=value, title=title, task_guid=task_guid, user_key=user_key)
    )

    # 旧串行 PATCH 已删除:任务状态只由上面并行块的 _patch_task 写一次,
    # 否则有 task_guid 的行会重复 PATCH、无 task_guid 的行会拿空路径打飞书报错。
    result = {**task_result, "task_updated": task_updated, "card_edit": card_edit_status, "review_card": review_outcome}
    return json.dumps(result, ensure_ascii=False, default=str)
