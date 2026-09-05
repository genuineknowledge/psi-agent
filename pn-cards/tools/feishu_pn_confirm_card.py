# ruff: noqa: RUF002, RUF003  # 中文全角标点是刻意排版
"""场景① 候选确认卡（会议纪要候选链路 P0）。

一张 multi_use 卡列出从会议纪要抽出的「像行为事实」候选，逐行三个动作：
确认记录（走记录管道入库）/ 不做记录 / 修改内容（私聊补一张修改表单卡，
改完确认即按新文字入库）。确认一条结一条、其余仍可点；重复点击被 Channel
逐 action 消费拒绝，工具内再做终态幂等保护，双保险。

工具一卡两用：
- **发卡**（传 receive_id + candidates_json）：建批次 → 落状态 → 渲染 schema 2.0
  卡片发出去。ledger_app_token/table_id/fields_json 可选：给了就把确认记录追
  加到真实台账（fields_json = 列名→值的模板，值里可用占位符 {text} {note}
  {source} {meeting_date} {person_name}）；不给则走本地记录管道（pn-state/
  records.json，正式台账定位后替换 commit_record 即可）。
- **处理点击**（传 card_action_json）：pn_keep_<i> / pn_drop_<i> / pn_edit_<i>，
  更新批次状态、原地重建整卡；pn_edit 额外给本人发一张修改表单卡。
"""

from __future__ import annotations

import json
from typing import Any

import _feishu_impl as _core


def _fresh_core():
    """每次(重)载本文件时按磁盘路径现取 _pn_impl，绕开下划线共享模块的进程内
    import 缓存（工具框架只按文件 hash 热载顶层工具，_ 前缀 core 不会刷新）。
    """
    import importlib.util as _ilu  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415

    _src = _Path(__file__).resolve().parent / "_pn_impl.py"
    _spec = _ilu.spec_from_file_location("_pn_impl_fresh", _src)
    _m = _ilu.module_from_spec(_spec)
    if _spec.loader is None:
        raise RuntimeError(f"cannot load {_src}")
    _spec.loader.exec_module(_m)
    return _m


P = _fresh_core()

_MAX_CANDIDATES = P.MAX_CANDIDATES


async def _rebuild_batch_card(batch: dict, message_id: str, user_key: str) -> dict[str, Any]:
    """按批次当前状态重建整卡并 edit 原卡。"""
    try:
        res = await _core.edit_card_impl(
            message_id, json.dumps(P.render_confirm_card(batch), ensure_ascii=False), user_key
        )
    except Exception as e:
        return {"ok": False, "error": f"{e!r}"}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("message") or res}
    return {"ok": True}


def _normalize_candidates(raw: Any) -> tuple[list[dict], str]:
    if not isinstance(raw, list):
        return [], "candidates_json must be a JSON array"
    if not raw:
        return [], "no candidates to confirm"
    if len(raw) > _MAX_CANDIDATES:
        return [], f"too many candidates ({len(raw)}); cap is {_MAX_CANDIDATES}, split into batches"
    out: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            text = item
            note = ""
        elif isinstance(item, dict):
            text = item.get("text")
            note = item.get("note") or ""
        else:
            return [], "each candidate must be a string or {text, note}"
        text = P._clamp(text)
        if not text:
            return [], "candidate text is required and must be non-empty"
        out.append({"text": text, "note": str(note or "")})
    return out, ""


def _commit_payload(batch: dict, text: str, note: str) -> dict:
    """确认入库的字段（本地记录管道）。ledger 模板用 {text} 等占位符。"""
    return {
        "text": text,
        "note": note,
        "source": batch.get("source_label") or "",
        "meeting_date": batch.get("meeting_date") or "",
        "person_name": batch.get("person_name") or "",
    }


async def _commit_to_pipeline(batch: dict, row: dict, user_key: str) -> dict:
    """记录管道: 本地 records.json；若发卡时给了 ledger 坐标则追加真实台账。

    台账写失败时**自动回退本地暂存**并如实标注（ledger=False + fallback=True +
    reason）——绝不让「确认了但哪里都没记上」发生。只有本地也写失败才 ok=False。
    """
    payload = _commit_payload(batch, row.get("edited_text") or row["text"], row.get("note") or "")
    ledger = batch.get("ledger") or {}
    app_token = str(ledger.get("app_token") or "").strip()
    table_id = str(ledger.get("table_id") or "").strip()
    if app_token and table_id:
        try:
            import _feishu_api_impl as _api  # noqa: PLC0415

            fields_json = ledger.get("fields_json") or "{}"
            try:
                fields = json.loads(fields_json) if isinstance(fields_json, str) else {}
            except ValueError:
                fields = {}
            if not isinstance(fields, dict):
                fields = {}
            if not fields:
                fields = {
                    "记录": "{text}",
                    "来源": "{source}",
                    "日期": "{meeting_date}",
                    "对象": "{person_name}",
                    "状态": "待复核",
                }
            values = {k: str(v).format(**payload) for k, v in fields.items()}
            res = await _api.call_api_impl(
                "POST",
                "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records",
                paths_json=json.dumps({"app_token": app_token, "table_id": table_id}, ensure_ascii=False),
                body_json=json.dumps({"fields": values}, ensure_ascii=False),
                prefer="user",  # 台账是用户身份建的库：写入走点击者 user token
                # （回归：prefer=tenant 先拿机器人 token 写用户库 → 99991668
                #  Invalid access token，还不会触发回退，确认被静默吞成 fallback）
                user_key=user_key,
            )
            if not res.get("ok"):
                raise RuntimeError(res.get("message") or res)
            data = res.get("data") or {}
            record = data.get("record") or {}
            return {"ok": True, "ledger": True, "record_id": str(record.get("record_id") or "")}
        except Exception as e:  # 台账失败 → 回退本地，保证记录不丢
            try:
                result = await P.commit_record(
                    {"kind": "positive_negative", "stage": "recorded", "by": "candidate_confirm",
                     "ledger_fallback": True, "_ledger_error": str(e), **payload}
                )
                return {**result, "ledger": False, "fallback": True, "reason": f"{e!r}"}
            except Exception as e2:
                return {"ok": False, "error": f"ledger and local both failed: {e2!r}"}
    try:
        result = await P.commit_record(
            {"kind": "positive_negative", "stage": "recorded", "by": "candidate_confirm", **payload}
        )
        return {**result, "ledger": False}
    except Exception as e:
        return {"ok": False, "error": f"{e!r}"}


async def _handle_click(card_action_json: str, user_key: str = "") -> str:
    act = P.parse_action(card_action_json)
    action = str(act.get("action") or "")
    idx = P.row_index_of(action)
    if idx is None:
        return json.dumps({"ok": False, "error": "unrecognized confirm action"}, ensure_ascii=False)
    batch_id = str(act.get("batch_id") or "").strip() or str(act.get("pn_id") or "").strip()
    if not batch_id:
        return json.dumps({"ok": False, "error": "no batch_id in value"}, ensure_ascii=False)
    batch = await P.load_batch(batch_id)
    if not batch:
        return json.dumps({"ok": False, "error": f"batch {batch_id} not found"}, ensure_ascii=False)
    rows = batch.get("rows") or []
    if not (0 <= idx < len(rows)):
        return json.dumps({"ok": False, "error": f"row {idx} out of range"}, ensure_ascii=False)
    row = rows[idx]
    op = act.get("_operator") or user_key or ""
    msg_id = act.get("_message_id") or ""
    if row["status"] in P._FINAL_ROW:
        return json.dumps({"ok": True, "unchanged": True, "status": row["status"]}, ensure_ascii=False)

    out: dict[str, Any] = {"ok": True, "action": action, "row": idx}
    kind = action.rsplit("_", 1)[0]
    if kind == "pn_keep":
        # 先落账：成功才标 kept；台账/本地双失败则整单报错、行保持 pending（可重发卡重试）
        commit = await _commit_to_pipeline(batch, row, op)
        if not commit.get("ok"):
            return json.dumps(
                {"ok": False, "error": f"record failed (nothing persisted): {commit.get('error')}"},
                ensure_ascii=False,
            )
        row["status"] = "kept"
        row["decided_at"] = P._now()
        out["commit"] = commit
        if commit.get("fallback"):
            out["warn"] = f"ledger append failed, record kept locally: {commit.get('reason')}"
    elif kind == "pn_drop":
        row["status"] = "dropped"
        row["decided_at"] = P._now()
    elif kind == "pn_edit":
        if row["status"] != "pending":
            return json.dumps({"ok": True, "unchanged": True, "status": row["status"]}, ensure_ascii=False)
        row["status"] = "editing"
        row["decided_at"] = P._now()
        out["next_card"] = "edit_form"
    else:
        return json.dumps({"ok": False, "error": "unrecognized action kind"}, ensure_ascii=False)

    await P.save_batch(batch)
    # 先重建原卡（把已点的行刷成终态）
    target = msg_id or str(batch.get("message_id") or "")
    rebuilt: dict[str, Any] = {"ok": True, "skipped": "no card to rebuild"}
    if target:
        rebuilt = await _rebuild_batch_card(batch, target, op)
    if rebuilt.get("ok") is False:
        return json.dumps({**out, "ok": True, "warn": f"card rebuild failed: {rebuilt.get('error')}"},
                          ensure_ascii=False, default=str)
    # pn_edit 额外给本人发修改表单卡
    if kind == "pn_edit":
        person = str(act.get("person_open_id") or batch.get("person_open_id") or "").strip()
        card = P.render_confirm_edit_card(batch, idx)
        try:
            res = await _core.send_card_impl(
                receive_id=person,
                card_json=json.dumps(card, ensure_ascii=False),
                receive_id_type="open_id",
                user_key=op,
                business_context_json=json.dumps(
                    {"kind": "pn_confirm_edit", "batch_id": batch_id, "row_index": idx},
                    ensure_ascii=False,
                ),
                action_handlers_json=json.dumps(
                    {P._ACTION_EDIT_SUBMIT: "feishu_pn_confirm_edit"}, ensure_ascii=False
                ),
                multi_use=False,
            )
        except Exception as e:
            return json.dumps({**out, "ok": True, "warn": f"edit card send failed: {e!r}"},
                              ensure_ascii=False, default=str)
        if not res.get("ok"):
            return json.dumps({**out, "ok": True, "warn": f"edit card send failed: {res.get('message') or res}"},
                              ensure_ascii=False, default=str)
        out["edit_form_message_id"] = res.get("message_id", "")
    return json.dumps(out, ensure_ascii=False, default=str)


async def feishu_pn_confirm_card(
    receive_id: str = "",
    candidates_json: str = "",
    person_name: str = "",
    source_label: str = "会议纪要",
    meeting_date: str = "",
    ledger_app_token: str = "",
    ledger_table_id: str = "",
    ledger_fields_json: str = "{}",
    receive_id_type: str = "open_id",
    user_key: str = "",
    card_action_json: str = "",
) -> str:
    """正负面「判断卡」场景①——会议纪要候选确认（发卡 / 处理点击两用）。

    **发卡**：把从会议纪要抽出的候选批量确认。candidates_json 是数组，元素为
    字符串或 ``{"text": 行为事实, "note": 补充}``（最多 10 条）。收卡人逐行点
    确认记录（入库）/ 不做记录 / 修改内容（私聊修改表单，改完按新文字入库）。
    只发给本人、只整理参会者自己的候选。ledger_app_token/table_id/fields_json
    可选：给了则确认时把记录追加进真实台账（fields_json 为 ``{列名: 值模板}``，
    支持 {text}/{source}/{meeting_date}/{person_name} 占位符，缺省给常见列模板）；
    不给则写本地记录管道（pn-state/records.json）。

    **处理点击**（传 card_action_json）：action 为 pn_keep_<行号> / pn_drop_<行号>
    / pn_edit_<行号>。每行动作只生效一次（Channel 逐 action 消费 + 工具终态幂等），
    原地重建整卡，未点的行按钮保留。

    Args:
        receive_id: 收卡人 open_id（发卡时必填）。
        candidates_json: 候选数组 JSON（发卡时必填）。
        person_name: 本人姓名（卡面称呼）。
        source_label: 来源标签，默认「会议纪要」。
        meeting_date: 会议日期（如 09-03 晨会）。
        ledger_app_token: 可选真实台账 base app_token。
        ledger_table_id: 可选真实台账 table_id。
        ledger_fields_json: 可选台账列名→值模板 JSON。
        receive_id_type: 收件人 id 类型（默认 open_id）。
        user_key: 调用者 open_id。
        card_action_json: 点击回调 payload（处理点击时传）。

    Returns:
        JSON：ok / action(sent|pn_keep_<i>|...) / message_id / batch_id / counts /
        commit（确认入库结果）/ warn / error。
    """
    if card_action_json.strip():
        return await _handle_click(card_action_json, user_key)
    if not receive_id.strip():
        return json.dumps({"ok": False, "error": "receive_id is required"}, ensure_ascii=False)
    try:
        raw = json.loads(candidates_json) if candidates_json.strip() else None
    except ValueError as e:
        return json.dumps({"ok": False, "error": f"candidates_json is not valid JSON: {e!r}"},
                          ensure_ascii=False)
    candidates, err = _normalize_candidates(raw)
    if err:
        return json.dumps({"ok": False, "error": err}, ensure_ascii=False)
    ledger = {}
    if ledger_app_token.strip() or ledger_table_id.strip():
        if not (ledger_app_token.strip() and ledger_table_id.strip()):
            return json.dumps({"ok": False, "error": "ledger needs both app_token and table_id"},
                              ensure_ascii=False)
        ledger = {
            "app_token": ledger_app_token.strip(),
            "table_id": ledger_table_id.strip(),
            "fields_json": ledger_fields_json.strip() or "{}",
        }
    batch = P._build_candidate_batch(
        person_open_id=receive_id.strip(),
        person_name=(person_name or "你").strip(),
        source_label=source_label.strip() or "会议纪要",
        meeting_date=meeting_date.strip(),
        candidates=candidates,
        ledger=ledger,
    )
    await P.save_batch(batch)
    handlers = P.confirm_card_handlers(batch)
    if not handlers:
        return json.dumps({"ok": False, "error": "nothing to confirm"}, ensure_ascii=False)
    res = await _core.send_card_impl(
        receive_id=receive_id.strip(),
        card_json=json.dumps(P.render_confirm_card(batch), ensure_ascii=False),
        receive_id_type=receive_id_type.strip() or "open_id",
        user_key=user_key,
        business_context_json=json.dumps(
            {"kind": "pn_confirm", "batch_id": batch["batch_id"], "person": person_name or ""},
            ensure_ascii=False,
        ),
        action_handlers_json=json.dumps(handlers, ensure_ascii=False),
        multi_use=True,
    )
    if not res.get("ok"):
        return json.dumps({"ok": False, "error": res.get("message") or res.get("error") or res},
                          ensure_ascii=False, default=str)
    batch["message_id"] = res.get("message_id", "")
    await P.save_batch(batch)
    return json.dumps(
        {
            "ok": True,
            "action": "sent",
            "message_id": res.get("message_id", ""),
            "batch_id": batch["batch_id"],
            "counts": {"candidates": len(batch["rows"]), "pending": len(batch["rows"])},
            "person": person_name or "",
        },
        ensure_ascii=False,
        default=str,
    )
