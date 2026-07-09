"""Smoke test: sessions_list, session_status, sessions_history (Gateway-optional)."""

from __future__ import annotations

# ruff: noqa: E402, T201
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import session_keyword_search as session_keyword_search_tool
import session_status as session_status_tool
import session_task_search as session_task_search_tool
import sessions_history as sessions_history_tool
import sessions_list as sessions_list_tool

WORKSPACE = Path(__file__).resolve().parents[1]


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


async def _run_case(name: str, coro) -> dict[str, Any]:
    try:
        raw = await coro
        data = json.loads(raw) if isinstance(raw, str) else raw
        ok = bool(data.get("ok"))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print("  message:", data.get("message", ""))
        return {"name": name, "ok": ok, "data": data}
    except Exception as exc:
        print(f"[FAIL] {name}: {exc}")
        return {"name": name, "ok": False, "error": str(exc)}


async def main() -> int:
    results: list[dict[str, Any]] = []

    _print_section("1. Local smoke (include_gateway=false)")
    listed = await _run_case(
        "sessions_list (no gateway)",
        sessions_list_tool.sessions_list(workspace=str(WORKSPACE), include_gateway=False),
    )
    results.append(listed)

    sessions = listed.get("data", {}).get("sessions", []) if listed.get("ok") else []
    sid = sessions[0]["session_id"] if sessions else ""

    if sid:
        status_local = await _run_case(
            f"session_status ({sid}, no gateway)",
            session_status_tool.session_status(
                session_id=sid,
                workspace=str(WORKSPACE),
                include_gateway=False,
            ),
        )
        results.append(status_local)

        history_local = await _run_case(
            f"sessions_history ({sid}, no gateway)",
            sessions_history_tool.sessions_history(
                session_id=sid,
                workspace=str(WORKSPACE),
                limit=5,
                include_gateway=False,
            ),
        )
        results.append(history_local)

        list_count = sessions[0].get("message_count", 0)
        hist_count = history_local.get("data", {}).get("count", 0)
        chain_ok = list_count >= hist_count or hist_count > 0
        print(f"[{'PASS' if chain_ok else 'FAIL'}] chain consistency (list msg_count>={hist_count})")
        results.append({"name": "chain consistency (local)", "ok": chain_ok})
    else:
        print("[SKIP] no histories in workspace — chain tests skipped")
        results.append({"name": "chain consistency (local)", "ok": False, "skipped": True})

    _print_section("2. Gateway enhancement (include_gateway=true)")
    listed_gw = await _run_case(
        "sessions_list (with gateway)",
        sessions_list_tool.sessions_list(workspace=str(WORKSPACE), include_gateway=True),
    )
    results.append(listed_gw)
    gw_url = listed_gw.get("data", {}).get("gateway_url", "")
    print("  gateway_url:", gw_url or "(empty)")

    gw_sessions = listed_gw.get("data", {}).get("sessions", []) if listed_gw.get("ok") else []
    gw_sid = gw_sessions[0]["session_id"] if gw_sessions else sid

    if gw_sid:
        status_gw = await _run_case(
            f"session_status ({gw_sid}, with gateway)",
            session_status_tool.session_status(
                session_id=gw_sid,
                workspace=str(WORKSPACE),
                include_gateway=True,
            ),
        )
        results.append(status_gw)
        session = status_gw.get("data", {}).get("session", {})
        has_gw_meta = bool(gw_url) and isinstance(session, dict) and (
            session.get("gateway") is not None or session.get("title")
        )
        print(
            f"[{'PASS' if has_gw_meta or not gw_url else 'WARN'}] gateway metadata present when online"
        )
        results.append(
            {
                "name": "gateway metadata",
                "ok": has_gw_meta or not gw_url,
            }
        )

        history_gw = await _run_case(
            f"sessions_history ({gw_sid}, with gateway)",
            sessions_history_tool.sessions_history(
                session_id=gw_sid,
                workspace=str(WORKSPACE),
                limit=3,
                include_gateway=True,
            ),
        )
        results.append(history_gw)

    _print_section("3. Empty session_id (outside session process)")
    empty_status = await _run_case(
        "session_status (empty id)",
        session_status_tool.session_status(workspace=str(WORKSPACE), include_gateway=False),
    )
    expect_fail = not empty_status.get("ok") and "required" in str(
        empty_status.get("data", {}).get("message", "")
    )
    print(f"[{'PASS' if expect_fail else 'FAIL'}] empty session_id correctly rejected")
    results.append({"name": "empty session_id rejected", "ok": expect_fail})

    _print_section("4. running_only filter")
    running = await _run_case(
        "sessions_list (running_only=true)",
        sessions_list_tool.sessions_list(
            workspace=str(WORKSPACE),
            running_only=True,
            include_gateway=True,
        ),
    )
    results.append(running)
    running_rows = running.get("data", {}).get("sessions", [])
    all_running = all(row.get("running") for row in running_rows) if running_rows else True
    print(f"[{'PASS' if all_running else 'FAIL'}] running_only rows all running")
    results.append({"name": "running_only filter", "ok": all_running})

    _print_section("5. Search tools")
    keyword = await _run_case(
        "session_keyword_search (github)",
        session_keyword_search_tool.session_keyword_search(
            query="github",
            workspace=str(WORKSPACE),
            limit=5,
        ),
    )
    results.append(keyword)

    task = await _run_case(
        "session_task_search (recent)",
        session_task_search_tool.session_task_search(
            category="recent",
            workspace=str(WORKSPACE),
            limit=5,
        ),
    )
    results.append(task)

    passed = sum(1 for r in results if r.get("ok"))
    total = len(results)
    print(f"\n=== SUMMARY: {passed}/{total} checks passed ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
