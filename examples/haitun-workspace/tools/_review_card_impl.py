"""Review-card sender for the TODO delivery loop.

When an executor ticks a TODO card row complete (``feishu_todo_card_tick``), that
tool hands the row's ledger wiring to this module, which fetches the ledger row,
finds the todo's mentor, and sends the mentor a private review card: five small
1-5 score buttons, a comment input submitted via its own confirm button, and a
「打回重做」button.

Score clicks, comment confirmations, and rejects all map to **tools**
(``feishu_review_card_select`` / ``feishu_review_input`` / ``feishu_review_reject``)
so the deterministic direct-dispatch in ``session/agent.py`` handles them in
seconds without an LLM turn: the score tool writes 打分, the comment tool writes
评语, and the reject tool rolls the delivery back (task uncompleted + ledger
进行中) — all three rebuild the card in place. The wiki write-back for
score/comment/reject is covered by ``company-todo-audit`` step 0 (ledger → wiki sweep).

Feishu's action ids are single-use **forever** per card, and the Channel dedups
callbacks by ``(message_id, action)`` — so every rebuild bumps a round counter
embedded in each callback's ``action`` name (``review_score_r0`` → ``review_score_r1``
→ …), exactly like the todo card's tick/untick rounds. That is what keeps the
same card operable across many score picks and comment edits; the handlers map
pre-registers every round up to ``_MAX_ROUNDS``.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import _feishu_api_impl as _api
import _feishu_impl as _f


# 每张评价卡最多支持的重建轮数(分数/评语/打回各预注册 _MAX_ROUNDS 个 action)。
# 点一次分或确认一次评语消耗一轮;20 轮对单条 todo 的评价往返绰绰有余。
_MAX_ROUNDS = 20

# 测试模式:设了该环境变量时,评价卡发给该 open_id(测试者本人)代替真实 mentor,
# 严禁把评价卡发到真实 mentor 手上。正式运行(海豚一号服务器)不设此变量 → 发真 mentor。
_TEST_RECEIVE_ID = os.environ.get("PSI_REVIEW_CARD_TEST_RECEIVE_ID", "").strip()


def _resolve_ledger_ids(value: dict[str, Any]) -> tuple[str, str, str]:
    """Extract (record_id, app_token, table_id) from a clicked row's value.

    Same resolution order as ``_todo_card_impl._sync_ledger_status``: direct fields
    first, then the row entry inside the self-contained ``card_state`` blob.
    """
    from _todo_card_impl import _parse_card_state  # local import avoids import cycles

    record_id = str(value.get("ledger_record_id") or "").strip()
    app_token = str(value.get("ledger_app_token") or "").strip()
    table_id = str(value.get("ledger_table_id") or "").strip()
    if not (record_id and app_token and table_id):
        state_json = value.get("card_state")
        if isinstance(state_json, str) and state_json.strip():
            state = _parse_card_state(state_json)
            if state is not None:
                app_token = app_token or str(state.get("ledger_app_token") or "").strip()
                table_id = table_id or str(state.get("ledger_table_id") or "").strip()
                if not record_id:
                    index_raw = value.get("todo_index")
                    rows = state.get("rows")
                    if isinstance(index_raw, int) and isinstance(rows, list) and 0 <= index_raw < len(rows):
                        row = rows[index_raw]
                        if isinstance(row, dict):
                            record_id = str(row.get("ledger_record_id") or "").strip()
    return record_id, app_token, table_id


def _person_ids(field_value: Any) -> list[str]:
    """Person field values arrive as a list of dicts; extract open_ids/ids."""
    ids: list[str] = []
    if not isinstance(field_value, list):
        return ids
    for entry in field_value:
        if not isinstance(entry, dict):
            continue
        for key in ("open_id", "id", "user_id"):
            raw = entry.get(key)
            if isinstance(raw, str) and raw.strip():
                ids.append(raw.strip())
                break
    return ids


def _cell_text(field_value: Any) -> str:
    if isinstance(field_value, str):
        return field_value
    if isinstance(field_value, list):
        parts: list[str] = []
        for entry in field_value:
            if isinstance(entry, dict):
                parts.append(str(entry.get("text") or ""))
            else:
                parts.append(str(entry))
        return "".join(parts)
    return str(field_value or "")


async def _fetch_ledger_row(
    app_token: str, table_id: str, record_id: str, user_key: str
) -> dict[str, Any]:
    """Read one ledger row; return {ok, fields...} (never raises).

    Read with the **tenant (bot) identity**: the app is a ledger collaborator
    (added manually, once per mentor base), so the bot can read without any
    personal UAT — a ``prefer="user"`` read inside the deterministic direct
    dispatch would hit the identity-choice prompt, which has no LLM turn to
    answer it and hangs the whole request.
    """
    try:
        res = await _api.call_api_impl(
            "GET",
            "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id",
            paths_json=json.dumps(
                {"app_token": app_token, "table_id": table_id, "record_id": record_id},
                ensure_ascii=False,
            ),
            prefer="tenant",
            user_key=user_key,
        )
    except Exception as e:
        return {"ok": False, "error": f"{e!r}"}
    if not res.get("ok"):
        return res
    record = res.get("data", {}).get("record", {}) if isinstance(res.get("data"), dict) else {}
    fields = record.get("fields", {}) if isinstance(record, dict) else {}
    return {"ok": True, "fields": fields}


def _round_of(action: str) -> int:
    """Parse the round number out of an action name like ``review_score_r3``."""
    if not isinstance(action, str):
        return 0
    _, _, r = action.rpartition("_r")
    try:
        return int(r)
    except ValueError:
        return 0



def _card_comment_value(payload: dict[str, Any]) -> str:
    """Recover the comment input's current text from the click-time card snapshot.

    When the card was built with an initial ``value`` on the input, Feishu echoes
    that value back inside the callback's ``card`` blob — use it to re-seat the
    text after a rebuild, so a typed comment survives score clicks and confirmations.
    """
    card = payload.get("card")
    if not isinstance(card, dict):
        return ""
    body = card.get("body")
    elements = body.get("elements", []) if isinstance(body, dict) else []
    if not isinstance(elements, list):
        return ""
    for el in elements:
        if isinstance(el, dict) and el.get("tag") == "input":
            raw = el.get("value")
            if isinstance(raw, str):
                return raw.strip()
            if isinstance(raw, dict):
                return str(raw.get("content") or raw.get("text") or "").strip()
            return ""
    return ""



async def _handle_score_select(card_action_json: str, user_key: str = "") -> dict[str, Any]:
    """Handle a score-button click: write the score to the ledger AND rebuild the card.

    The click carries the real score in ``value.score`` — this tool writes it to
    the ledger's ``mentor打分`` column (bot/tenant identity: the app is a ledger
    collaborator, no personal UAT involved, so this never hangs in the direct
    dispatch) and rebuilds the card with the chosen button highlighted (✓ N分,
    single-selection). The rebuild bumps the round so the next click is a fresh
    action id — clicking another score overwrites the previous one.
    """
    try:
        payload = json.loads(card_action_json) if isinstance(card_action_json, str) else card_action_json
    except ValueError as e:
        return {"ok": False, "error": f"invalid card_action_json: {e!r}"}
    # 回调 payload 里按钮 value 在 payload["action"]["value"](与 _todo_card_impl._action_value 一致)
    action = payload.get("action") if isinstance(payload, dict) else None
    value = action.get("value") if isinstance(action, dict) else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if not isinstance(value, dict):
        return {"ok": False, "error": "no value in payload"}
    message_id = str(payload.get("message_id") or "").strip()
    if not message_id:
        return {"ok": False, "error": "no message_id in payload"}

    record_id = str(value.get("record_id") or "").strip()
    title = str(value.get("title") or "").strip() or "该待办"
    owner_name = str(value.get("owner_name") or "").strip()
    owner_open_id = str(value.get("owner_open_id") or "").strip()
    cycle_date = str(value.get("cycle_date") or "").strip()
    task_guid = str(value.get("task_guid") or "").strip()
    # 台账坐标在函数开头统一解析(写打分与重建卡片都要用;2.0 回调 value 可能不全,
    # 缺失时回退到当前测试表)。
    app_token = str(value.get("ledger_app_token") or "").strip() or "C6sQbhhj1a5BRkslxjOcY2PPnYc"
    table_id = str(value.get("ledger_table_id") or "").strip() or "tblyPdcD9qzvAMGU"
    score_raw = value.get("score")
    selected_score = int(score_raw) if isinstance(score_raw, int) and 1 <= score_raw <= 5 else 0
    round_raw = value.get("round")
    round_ = int(round_raw) if isinstance(round_raw, int) else _round_of(str(value.get("action") or ""))

    ledger_result: dict[str, Any] = {"ok": True, "skipped": "no score"}
    if selected_score:
        # 打分落账:点分即打分。纯 tenant(bot) 身份,应用是台账协作者,
        # 直调环境不碰 UAT、不会挂起。
        try:
            from _feishu.bitable import _as_field_map, _build_update_record_request
            import _feishu_impl as _fcore

            ledger_result = await _fcore._invoke(
                _build_update_record_request(
                    app_token, table_id, record_id, _as_field_map({"mentor打分": selected_score})
                ),
                prefer="tenant",
            )
        except Exception as e:
            ledger_result = {"ok": False, "error": f"{e!r}"}

    try:
        # 重建走通用 DSL 模板:点分时把输入框当前文本带回,不丢已写评语。
        card, _ = _render_review_card(
            record_id=record_id,
            title=title,
            owner_name=owner_name,
            owner_open_id=owner_open_id,
            cycle_date=cycle_date,
            task_guid=task_guid,
            selected_score=selected_score,
            comment_value=_card_comment_value(payload),
            ledger_app_token=app_token,
            ledger_table_id=table_id,
        )
    except RuntimeError as e:
        return {"ok": False, "error": f"{e}"}
    try:
        res = await _f.edit_card_impl(message_id, json.dumps(card, ensure_ascii=False), user_key)
    except Exception as e:
        return {"ok": False, "error": f"{e!r}"}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("message") or res}
    return {
        "ok": True,
        "selected_score": selected_score,
        "card_updated": True,
        "ledger": {"ok": ledger_result.get("ok"), "error": ledger_result.get("error") or ledger_result.get("message") or ""},
    }


async def _handle_review_input(card_action_json: str, user_key: str = "") -> dict[str, Any]:
    """Handle the comment input's own callback: write the typed comment to the ledger.

    The comment rides on the input's own ``behaviors.callback`` (the input
    carries a ``confirm`` field, whose button is the only way an input's text
    reaches the server): the payload carries ``action.input_value`` with the
    current text. Writing it to the ledger here (bot identity, the app is a
    collaborator) means the report always reflects the latest comment; the
    score was already written on the score click (点分即打分). The rebuild
    bumps the round so the comment can be edited and re-confirmed again.
    """
    try:
        payload = json.loads(card_action_json) if isinstance(card_action_json, str) else card_action_json
    except ValueError as e:
        return {"ok": False, "error": f"invalid card_action_json: {e!r}"}
    action = payload.get("action") if isinstance(payload, dict) else None
    value = action.get("value") if isinstance(action, dict) else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if not isinstance(value, dict):
        return {"ok": False, "error": "no value in payload"}
    record_id = str(value.get("record_id") or "").strip()
    app_token = str(value.get("ledger_app_token") or "").strip()
    table_id = str(value.get("ledger_table_id") or "").strip()
    # 2.0 回调 payload 里 base 信息可能不全(只有 value 里的 record_id);
    # app_token/table_id 缺失时回退到当前测试表(固定值)。
    if not app_token:
        app_token = "C6sQbhhj1a5BRkslxjOcY2PPnYc"
    if not table_id:
        table_id = "tblyPdcD9qzvAMGU"
    if not record_id:
        return {"ok": False, "error": "no record_id in value"}
    comment = str((action.get("input_value") or "") if isinstance(action, dict) else "").strip()
    round_raw = value.get("round")
    round_ = int(round_raw) if isinstance(round_raw, int) else _round_of(str(value.get("action") or ""))

    ledger_result: dict[str, Any] = {"ok": True, "skipped": "empty comment"}
    if comment:
        try:
            # 直调短路里必须用纯 tenant(bot) 身份:专用 impl 硬编码 prefer="user" 会去
            # 解析 UAT,在无 LLM 回合的直调环境里挂起导致整个请求被取消。应用已是
            # 台账协作者,tenant 写不被拒。
            from _feishu.bitable import _as_field_map, _build_update_record_request

            ledger_result = await _core_invoke_update(
                _build_update_record_request(
                    app_token, table_id, record_id, _as_field_map({"mentor评语": comment})
                ),
                prefer="tenant",
            )
        except Exception as e:
            return {"ok": False, "error": f"{e!r}"}
        if not ledger_result.get("ok"):
            return {"ok": False, "error": ledger_result.get("message") or ledger_result}

    # 评语确认后飞书把整卡消费成「已提交」样式——原位重建评价卡,恢复按钮
    # (高亮分从 value.score 取;0 时显示未打分,台账里的分不受影响)。轮次 +1
    # 让下一次确认是全新 action,否则被 Channel 的 (message_id, action) 去重拦截。
    card_result: dict[str, Any] = {"ok": True, "skipped": "no rebuild info"}
    message_id = str(payload.get("message_id") or "").strip()
    title = str(value.get("title") or "").strip() or "该待办"
    owner_name = str(value.get("owner_name") or "").strip()
    owner_open_id = str(value.get("owner_open_id") or "").strip()
    cycle_date = str(value.get("cycle_date") or "").strip()
    task_guid = str(value.get("task_guid") or "").strip()
    score_raw = value.get("score")
    selected_score = int(score_raw) if isinstance(score_raw, int) and 1 <= score_raw <= 5 else 0
    kept_comment = comment or _card_comment_value(payload)
    if message_id:
        try:
            # 评语确认后重建:走通用 DSL 模板,把本次提交的评语带回输入框。
            rebuilt, _ = _render_review_card(
                record_id=record_id,
                title=title,
                owner_name=owner_name,
                owner_open_id=owner_open_id,
                cycle_date=cycle_date,
                task_guid=task_guid,
                selected_score=selected_score,
                comment_value=kept_comment,
                ledger_app_token=app_token,
                ledger_table_id=table_id,
            )
        except RuntimeError as e:
            card_result = {"ok": False, "error": f"{e}"}
        else:
            try:
                card_result = await _f.edit_card_impl(message_id, json.dumps(rebuilt, ensure_ascii=False), user_key)
            except Exception as e:
                card_result = {"ok": False, "error": f"{e!r}"}
    return {
        "ok": True,
        "record_id": record_id,
        "comment": kept_comment[:80],
        "card_rebuilt": card_result.get("ok"),
        "ledger": {"ok": ledger_result.get("ok"), "error": ledger_result.get("error") or ledger_result.get("message") or ""},
    }


def _find_todo_card(record_id: str) -> tuple[str, dict[str, Any], int, dict[str, Any]] | None:
    """Locate the newest TODO card whose ``card_state`` contains ``record_id``.

    Returns ``(message_id, row_value, row_index, state)`` — the card message id, the
    clicked-row-shaped ``value`` (with the self-contained ``card_state`` embedded),
    the row's index in ``state["rows"]``, and the parsed state — or ``None``.

    Scans the Channel's card-snapshot dir (same machine): every sent card lands a
    ``<message_id>.json`` there, and each TODO-card row button carries the full
    ``card_state`` blob, so the review card can find its sibling TODO card through
    the shared ``ledger_record_id`` — no wiring at send time needed.
    """
    import os

    from _todo_card_impl import _parse_card_state

    appdata = os.environ.get("PSI_APPDATA", "").strip()
    if not appdata:
        appdata = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Haitun")
    snap_dir = os.path.join(appdata, "feishu-card-snapshots")
    if not os.path.isdir(snap_dir):
        return None
    candidates: list[tuple[float, str, dict[str, Any], int, dict[str, Any]]] = []
    try:
        entries = os.listdir(snap_dir)
    except OSError:
        return None
    for name in entries:
        if not name.endswith(".json"):
            continue
        path = os.path.join(snap_dir, name)
        try:
            snap = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(snap, dict):
            continue
        card = snap.get("card")
        if not isinstance(card, dict):
            continue
        elements = card.get("elements")
        if not isinstance(elements, list):
            continue
        for el in elements:
            if not isinstance(el, dict) or el.get("tag") != "action":
                continue
            v = el.get("value")
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    continue
            if not isinstance(v, dict):
                continue
            state_json = v.get("card_state")
            if not isinstance(state_json, str):
                continue
            state = _parse_card_state(state_json)
            if state is None:
                continue
            for index, row in enumerate(state.get("rows") or []):
                if isinstance(row, dict) and str(row.get("ledger_record_id") or "") == record_id:
                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        mtime = 0.0
                    candidates.append((mtime, name[:-5], v, index, state))
                    break
    if not candidates:
        return None
    _, message_id, value, index, state = max(candidates, key=lambda c: c[0])
    return message_id, value, index, state


async def _handle_review_reject(card_action_json: str, user_key: str = "") -> dict[str, Any]:
    """Handle 「打回重做」: roll the task back to incomplete AND the ledger to 进行中.

    Rejecting a delivery must mirror what the executor's own undo does — the
    linked Feishu task loses its completion (``completed_at`` cleared), the
    ledger row returns to 进行中, and the executor's next tick sends a fresh
    review card. All three writes are mechanical, so this runs as a direct
    tool (seconds, no LLM turn); wiki notes are covered by company-todo-audit.
    """
    try:
        payload = json.loads(card_action_json) if isinstance(card_action_json, str) else card_action_json
    except ValueError as e:
        return {"ok": False, "error": f"invalid card_action_json: {e!r}"}
    action = payload.get("action") if isinstance(payload, dict) else None
    value = action.get("value") if isinstance(action, dict) else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if not isinstance(value, dict):
        return {"ok": False, "error": "no value in payload"}
    record_id = str(value.get("record_id") or "").strip()
    task_guid = str(value.get("task_guid") or "").strip()
    round_raw = value.get("round")
    round_ = int(round_raw) if isinstance(round_raw, int) else _round_of(str(value.get("action") or ""))

    outcomes: dict[str, Any] = {}
    # 1) 撤销飞书任务的完成状态 —— completed_at 清空,和「标记完成」互逆。
    if task_guid:
        try:
            res = await _api.call_api_impl(
                "PATCH",
                "/open-apis/task/v2/tasks/:task_guid",
                body_json=json.dumps(
                    {"task": {"completed_at": None}, "update_fields": ["completed_at"]},
                    ensure_ascii=False,
                ),
                paths_json=json.dumps({"task_guid": task_guid}, ensure_ascii=False),
                prefer="tenant",
                user_key=user_key,
            )
            outcomes["task_uncompleted"] = res.get("ok", False)
            if not res.get("ok"):
                outcomes["task_error"] = res.get("error") or res.get("message") or str(res)
        except Exception as e:
            outcomes["task_uncompleted"] = False
            outcomes["task_error"] = f"{e!r}"
    else:
        outcomes["task_uncompleted"] = False
        outcomes["task_error"] = "no task_guid in value"

    # 2) 台账状态回「进行中」(打分/评语字段保留)。
    app_token = str(value.get("ledger_app_token") or "").strip() or "C6sQbhhj1a5BRkslxjOcY2PPnYc"
    table_id = str(value.get("ledger_table_id") or "").strip() or "tblyPdcD9qzvAMGU"
    if record_id:
        try:
            from _feishu.bitable import _as_field_map, _build_update_record_request

            ledger_result = await _core_invoke_update(
                _build_update_record_request(
                    app_token, table_id, record_id, _as_field_map({"状态": "进行中"})
                ),
                prefer="tenant",
            )
            outcomes["ledger"] = {
                "ok": ledger_result.get("ok"),
                "error": ledger_result.get("error") or ledger_result.get("message") or "",
            }
        except Exception as e:
            outcomes["ledger"] = {"ok": False, "error": f"{e!r}"}
    else:
        outcomes["ledger"] = {"ok": False, "error": "no record_id in value"}

    # 3) 重建评价卡,标注「已打回重做」,按钮保留(轮次 +1 保持可操作)。
    message_id = str(payload.get("message_id") or "").strip()
    title = str(value.get("title") or "").strip() or "该待办"
    owner_name = str(value.get("owner_name") or "").strip()
    owner_open_id = str(value.get("owner_open_id") or "").strip()
    cycle_date = str(value.get("cycle_date") or "").strip()
    score_raw = value.get("score")
    selected_score = int(score_raw) if isinstance(score_raw, int) and 1 <= score_raw <= 5 else 0
    card_result: dict[str, Any] = {"ok": True, "skipped": "no rebuild info"}
    if message_id:
        try:
            # 打回重建:走通用 DSL 模板,note 渲染成「状态:已打回重做…」信息行。
            rebuilt, _ = _render_review_card(
                record_id=record_id,
                title=title,
                owner_name=owner_name,
                owner_open_id=owner_open_id,
                cycle_date=cycle_date,
                task_guid=task_guid,
                selected_score=selected_score,
                comment_value=_card_comment_value(payload),
                ledger_app_token=app_token,
                ledger_table_id=table_id,
                note="已打回重做——任务已回到进行中,执行人重新完成后会再发一张新的评价卡。",
            )
        except RuntimeError as e:
            card_result = {"ok": False, "error": f"{e}"}
        else:
            try:
                card_result = await _f.edit_card_impl(message_id, json.dumps(rebuilt, ensure_ascii=False), user_key)
            except Exception as e:
                card_result = {"ok": False, "error": f"{e!r}"}
    outcomes["card_rebuilt"] = card_result.get("ok")

    # 4) 同步执行人的 TODO 卡:把该行翻回未完成(等价于执行人点了一次「撤销」),
    #    行轮次 +1 生成未消费的「标记完成」按钮——重做后再点即再次触发评价卡。
    outcomes["todo_card"] = {"ok": True, "skipped": "no matching todo card"}
    todo_located = _find_todo_card(record_id) if record_id else None
    if todo_located is not None:
        todo_message_id, todo_value, todo_index, todo_state = todo_located
        rows = todo_state.get("rows") or []
        row = rows[todo_index] if isinstance(rows, list) and 0 <= todo_index < len(rows) else None
        if isinstance(row, dict) and row.get("done"):
            row_round = int(row.get("round") or 0)
            try:
                from _todo_card_impl import _prepare_row_transition

                status, new_card = _prepare_row_transition(
                    payload={},
                    value=todo_value,
                    index=todo_index,
                    new_done=False,
                    new_round=row_round + 1,
                    fallback_title=str(row.get("title") or ""),
                    fallback_task_guid=str(row.get("task_guid") or ""),
                )
                if new_card is not None:
                    edit_res = await _f.edit_card_impl(
                        todo_message_id, json.dumps(new_card, ensure_ascii=False), user_key
                    )
                    outcomes["todo_card"] = {
                        "ok": bool(edit_res.get("ok")),
                        "message_id": todo_message_id,
                        "row": todo_index,
                        "error": (edit_res.get("message") or "") if not edit_res.get("ok") else "",
                    }
                else:
                    outcomes["todo_card"] = {"ok": False, "error": f"rebuild skipped: {status}"}
            except Exception as e:
                outcomes["todo_card"] = {"ok": False, "error": f"{e!r}"}
        else:
            outcomes["todo_card"] = {"ok": True, "skipped": "todo row already reopened"}

    outcomes["ok"] = bool(outcomes.get("task_uncompleted")) and bool(outcomes.get("ledger", {}).get("ok"))
    outcomes["record_id"] = record_id
    return outcomes


def _render_review_card(
    *,
    record_id: str,
    title: str,
    owner_name: str,
    owner_open_id: str,
    cycle_date: str,
    task_guid: str,
    selected_score: int,
    comment_value: str,
    ledger_app_token: str = "",
    ledger_table_id: str = "",
    note: str = "",
) -> tuple[dict[str, Any], dict[str, str]]:
    """Render the review card through the card-dsl template (发卡与重建共用).

    One template serves both the send path (``_send_review_card``) and every
    rebuild (score pick / comment confirm / reject) — values fill the card
    body, context fills the callback values. Returns ``(card, handlers)``.
    """
    from _card_dsl import render_template

    rendered = render_template(
        "review-card",
        values_json=json.dumps(
            {
                "owner_name": owner_name,
                "title": title or "该待办",
                "delivered_at": time.strftime("%Y-%m-%d %H:%M"),
                "record_id": record_id,
                "selected_score": selected_score,
                "note": note,
            },
            ensure_ascii=False,
        ),
        context_json=json.dumps(
            {
                "owner_name": owner_name,
                "owner_open_id": owner_open_id,
                "cycle_date": cycle_date,
                "task_guid": task_guid,
                "ledger_app_token": ledger_app_token,
                "ledger_table_id": ledger_table_id,
                "comment_value": comment_value,
            },
            ensure_ascii=False,
        ),
    )
    if not rendered.get("ok"):
        raise RuntimeError(rendered.get("error") or "dsl render failed")
    return rendered["card"], rendered["handlers"]


async def _core_invoke_update(req: Any, prefer: str) -> dict[str, Any]:
    """Invoke a bitable update request with an explicit identity preference."""
    import _feishu_impl as _fcore

    return await _fcore._invoke(req, prefer=prefer)


async def _send_review_card(
    value: dict[str, Any], title: str, task_guid: str, user_key: str
) -> dict[str, Any]:
    """Send the mentor a review card after a tick. Best-effort; never raises.

    Skips quietly when the row has no ledger wiring or the ledger row has no
    mentor (nothing to notify). Reads the row with the clicker's user identity
    so the read also works when the bot is not a ledger collaborator.
    """
    record_id, app_token, table_id = _resolve_ledger_ids(value)
    if not (record_id and app_token and table_id):
        return {"ok": True, "skipped": "no ledger wiring"}

    row = await _fetch_ledger_row(app_token, table_id, record_id, user_key)
    if not row.get("ok"):
        return {"ok": False, "skipped": "ledger read failed", "error": row.get("error") or row.get("message")}

    fields = row["fields"]
    mentor_ids = _person_ids(fields.get("mentor"))
    owner_ids = _person_ids(fields.get("负责人"))
    owner_name = ""
    for entry in fields.get("负责人") or []:
        if isinstance(entry, dict) and entry.get("name"):
            owner_name = str(entry["name"])
            break
    if not owner_name:
        owner_name = str(user_key or "该成员")
    cycle_date_raw = fields.get("周期日期")
    cycle_date = ""
    if isinstance(cycle_date_raw, (int, float)) and cycle_date_raw:
        # Bitable date fields arrive as epoch milliseconds.
        import datetime

        try:
            cycle_date = datetime.datetime.fromtimestamp(int(cycle_date_raw) / 1000).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            cycle_date = ""
    if not mentor_ids:
        return {"ok": True, "skipped": "no mentor on ledger row"}
    mentor_open_id = mentor_ids[0]
    owner_open_id = owner_ids[0] if owner_ids else ""
    receive_id = _TEST_RECEIVE_ID or mentor_open_id
    test_override = bool(_TEST_RECEIVE_ID)

    # 卡片由通用 DSL 模板生成(card-dsl 技能):业务只填数据不写结构,
    # 发卡与重建共用同一个模板渲染入口,不经过 LLM,渲染零出错。
    try:
        card, handlers = _render_review_card(
            record_id=record_id,
            title=title or "该待办",
            owner_name=owner_name,
            owner_open_id=owner_open_id,
            cycle_date=cycle_date,
            task_guid=task_guid,
            selected_score=0,
            # 重发卡时把台账已有评语作为输入框初始值,mentor 能看到之前写过什么。
            comment_value=_cell_text(fields.get("mentor评语")),
            ledger_app_token=app_token,
            ledger_table_id=table_id,
        )
    except RuntimeError as e:
        return {"ok": False, "error": f"{e}"}
    business_context = {
        "kind": "company_todo_review",
        "record_id": record_id,
        "task_guid": task_guid,
        "owner_open_id": owner_open_id,
        "cycle_date": cycle_date,
        "title": title,
    }
    try:
        res = await _f.send_card_impl(
            receive_id=receive_id,
            card_json=json.dumps(card, ensure_ascii=False),
            receive_id_type="open_id",
            user_key=user_key,
            business_context_json=json.dumps(business_context, ensure_ascii=False),
            action_handlers_json=json.dumps(handlers, ensure_ascii=False),
            # 关键:multi_use=True 让每个按钮独立消费。默认 false 时,点任意一个按钮
            # 整卡作废(飞书会把其余按钮/表单全部移除),分数选了就没法写评语提交。
            multi_use=True,
        )
    except Exception as e:
        return {"ok": False, "error": f"{e!r}"}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("message") or res}
    return {
        "ok": True,
        "mentor_open_id": mentor_open_id,
        "receive_id": receive_id,
        "test_override": test_override,
        "record_id": record_id,
    }
