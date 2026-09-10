# ruff: noqa: RUF001, RUF002, RUF003  # 中文全角标点是刻意排版
"""场景① 的「修改内容」提交处理。

用户点了候选确认卡上的「改」后，会私聊收到一张修改表单卡；填完点
「确认修改并记入」即触发本工具：按新文字把该条候选落为已记入（走记录管道），
并把原候选确认卡那一行原地刷成「✅ 已记入(修改后)」。

纯回调工具：只吃 card_action_json，不自己发卡。
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


async def feishu_pn_confirm_edit(card_action_json: str = "", user_key: str = "") -> str:
    """处理「修改内容」表单卡的提交（场景① 记录确认的修改入口）。

    Args:
        card_action_json: <feishu_card_action> payload（处理点击时传）。
        user_key: 点击者 open_id（Session 注入）。

    Returns:
        JSON：ok / action / batch_id / row_index / text / commit / error。
    """
    act = P.parse_action(card_action_json)
    action = str(act.get("action") or "")
    if action != P._ACTION_EDIT_SUBMIT:
        return json.dumps({"ok": False, "error": "unrecognized action"}, ensure_ascii=False)
    batch_id = str(act.get("batch_id") or "").strip()
    try:
        idx = int(act.get("row_index"))
    except (TypeError, ValueError):
        return json.dumps({"ok": False, "error": "no row_index in value"}, ensure_ascii=False)
    if not batch_id:
        return json.dumps({"ok": False, "error": "no batch_id in value"}, ensure_ascii=False)
    form = act.get("_form_value") or {}
    if not isinstance(form, dict):
        return json.dumps({"ok": False, "error": "no form_value in payload"}, ensure_ascii=False)
    raw_text = str(form.get("pn_edit_text") or "").strip()
    if not raw_text:
        return json.dumps({"ok": False, "error": "修改后的内容不能为空"}, ensure_ascii=False)
    if len(raw_text) > P.MAX_TEXT:
        return json.dumps(
            {"ok": False, "error": f"修改后内容超长（{len(raw_text)} > {P.MAX_TEXT} 字上限），请精简后重新提交"},
            ensure_ascii=False,
        )
    text = raw_text
    batch = await P.load_batch(batch_id)
    if not batch:
        return json.dumps({"ok": False, "error": f"batch {batch_id} not found"}, ensure_ascii=False)
    rows = batch.get("rows") or []
    if not (0 <= idx < len(rows)):
        return json.dumps({"ok": False, "error": f"row {idx} out of range"}, ensure_ascii=False)
    row = rows[idx]
    if row["status"] in P._FINAL_ROW:
        return json.dumps({"ok": True, "unchanged": True, "status": row["status"]}, ensure_ascii=False)

    op = act.get("_operator") or user_key or ""
    row["edited_text"] = text
    # 先落账：成功才标 kept_edited（台账失败已自动回退本地；双失败则保持原状可重试）
    commit = await _pn_commit(batch, row, op)
    if not commit.get("ok"):
        return json.dumps(
            {"ok": False, "error": f"record failed (nothing persisted): {commit.get('error')}"},
            ensure_ascii=False,
        )
    row["status"] = "kept_edited"
    row["decided_at"] = P._now()
    await P.save_batch(batch)
    out: dict[str, Any] = {
        "ok": True,
        "action": action,
        "batch_id": batch_id,
        "row_index": idx,
        "text": text,
        "commit": commit,
    }
    if commit.get("fallback"):
        out["warn"] = f"ledger append failed, record kept locally: {commit.get('reason')}"
    # 重建目标是原候选确认卡(不是表单卡); 表单卡已被飞书单次消费。
    target = str(batch.get("message_id") or "") or act.get("_message_id")
    if target:
        try:
            res = await _core.edit_card_impl(
                target, json.dumps(P.render_confirm_card(batch), ensure_ascii=False), user_key
            )
        except Exception as e:
            out["warn"] = (out.get("warn") + " / " if out.get("warn") else "") + f"card rebuild failed: {e!r}"
        else:
            if not res.get("ok"):
                out["warn"] = (out.get("warn") + " / " if out.get("warn") else "") + (
                    f"card rebuild failed: {res.get('message') or res}"
                )
    return json.dumps(out, ensure_ascii=False, default=str)


async def _pn_commit(batch: dict, row: dict, user_key: str) -> dict:
    """与 feishu_pn_confirm_card 同一条记录管道（避免跨模块循环 import）。

    先弹出普通 import 缓存里的旧版 confirm_card（进程内只热载 hash 模块，普通
    模块名会滞留旧代码 —— 比如 prefer=tenant 的写入路径），保证每次拿到磁盘
    上的最新实现。
    """
    import sys as _sys  # noqa: PLC0415

    _sys.modules.pop("feishu_pn_confirm_card", None)
    import feishu_pn_confirm_card  # noqa: PLC0415

    return await feishu_pn_confirm_card._commit_to_pipeline(batch, row, user_key)
