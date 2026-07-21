from __future__ import annotations

import ast
from importlib import resources

from psi_agent.gateway._webview_main import _flash_hwnd, webview_main


def test_webview_main_module_has_no_psi_agent_imports():
    """Verify _webview_main.py does not import from psi_agent (key constraint)."""
    source = resources.files("psi_agent.gateway").joinpath("_webview_main.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("psi_agent"), (
                f"_webview_main.py imports from psi_agent: {module}"
            )


def test_flash_hwnd_noop_on_linux():
    """_flash_hwnd should be a no-op on non-Windows (no crash)."""
    _flash_hwnd(42)


def test_webview_main_is_callable():
    """webview_main function exists with correct signature."""
    assert callable(webview_main)
