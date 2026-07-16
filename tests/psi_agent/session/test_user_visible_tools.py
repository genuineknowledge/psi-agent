from __future__ import annotations

from psi_agent.session.user_visible_tools import (
    USER_VISIBLE_RESULT_TOOLS,
    surface_tool_result_text,
)


def test_clarify_is_registered() -> None:
    assert "clarify" in USER_VISIBLE_RESULT_TOOLS


def test_surface_clarify_success() -> None:
    block = "选哪个?\n\n  1. A\n  2. B\n"
    assert surface_tool_result_text("clarify", block) == block


def test_surface_skips_errors_and_unknown_tools() -> None:
    assert surface_tool_result_text("clarify", "[Error] bad args") is None
    assert surface_tool_result_text("clarify", "   ") is None
    assert surface_tool_result_text("get_weather", "sunny") is None
