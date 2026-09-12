"""Batch private-message send — one tool call loops through every item.

15:00 检测的「逐人私聊」是 O(人数) 的工具调用量,模型一轮发不完会自己收尾、
后半的人收不到提示。本实现把发送循环下沉成代码:调用方把名单一次性传进来,
循环发完全部才返回,失败的有账可查。
"""

from __future__ import annotations

from typing import Any

import _runtime_paths as _paths

from _feishu.message import send_message_impl


def _apply_states(lines: list[str], sent: set[str], failed: dict[str, str]) -> list[str]:
    """纯函数:按姓名把状态行改标(已发 / 失败:原因),姓名不在结果里的保持原样。

    每行格式 ``姓名|摘要|状态``。"""
    out: list[str] = []
    for line in lines:
        parts = line.split("|")
        name = parts[0].strip() if parts else ""
        if name in sent:
            out.append(f"{name}|{parts[1] if len(parts) > 1 else ''}|已发")
        elif name in failed:
            out.append(f"{name}|{parts[1] if len(parts) > 1 else ''}|失败:{failed[name]}")
        else:
            out.append(line)
    return out


async def _read_state_file(pending_file: str) -> list[str]:
    path = _paths.resolve_under(_paths.workspace_dir(), pending_file)
    try:
        return (await path.read_text(encoding="utf-8")).splitlines()
    except OSError:
        return []


async def _write_state_file(pending_file: str, lines: list[str]) -> None:
    path = _paths.resolve_under(_paths.workspace_dir(), pending_file)
    await path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


async def pm_batch_send_impl(items: list[dict[str, Any]], pending_file: str = "", user_key: str = "") -> dict[str, Any]:
    """Loop-send every item; returns sent / failed lists (no partial stop)."""
    sent: list[str] = []
    failed: list[dict[str, str]] = []
    for it in items:
        name = str(it.get("name", "")).strip()
        open_id = str(it.get("open_id", "")).strip()
        text = str(it.get("text", "")).strip()
        if not open_id or not text:
            failed.append({"name": name or open_id or "(匿名)", "reason": "缺 open_id 或 text"})
            continue
        r = await send_message_impl(open_id, text, "open_id", "")
        if r.get("ok"):
            sent.append(name or open_id)
        else:
            failed.append({"name": name or open_id, "reason": str(r.get("message", "unknown"))[:100]})
    if pending_file:
        try:
            lines = await _read_state_file(pending_file)
            await _write_state_file(
                pending_file,
                _apply_states(lines, set(sent), {f["name"]: f["reason"] for f in failed}),
            )
        except OSError:
            pass  # 状态文件写失败不影响发送结果,照实返回
    return {
        "ok": True,
        "total": len(items),
        "sent": sent,
        "sent_count": len(sent),
        "failed": failed,
        "failed_count": len(failed),
    }
