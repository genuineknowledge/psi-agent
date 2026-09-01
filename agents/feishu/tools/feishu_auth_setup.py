"""Feishu authorization SETUP: diagnose the environment and tell the user how to configure it.

``feishu_auth.py`` runs the authorization flow. These tools answer the questions that
come *before* it — the ones a user actually asks:

- "What do I put in the redirect URL field?" → ``feishu_auth_redirect_url``
- "Why does it keep asking me to copy a code?" → ``feishu_auth_env_check``
- "I'm on a server / on my laptop, what do I need to set?" → ``feishu_auth_setup_guide``

The information was always there (``PSI_OAUTH_CALLBACK_BASE``, loopback port
availability, an explicit ``PSI_FEISHU_REDIRECT_URI``) but nothing exposed it, so the
agent had to guess. ``mode="manual"`` in particular hides at least three unrelated
causes with three different fixes; these tools name the actual one.

All three are read-only: they inspect configuration and report. Nothing here changes
settings or touches tokens, and secrets are never echoed — the app secret is reported
only as present/absent, because the result gets sent straight into a chat window.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
import _oauth_setup as _setup


async def feishu_auth_env_check(probe: bool = True) -> str:
    """Check whether Feishu authorization can skip the code-copying, and what's missing.

    Use this when the user asks why they had to paste a code, whether authorization is
    set up correctly, or what still needs configuring. Also worth running before
    walking a user through setup, so the advice matches their actual environment.

    Reports the deployment shape (local machine vs server), which callback channel is
    in effect, the redirect URI that must be registered in the Feishu console, and —
    when automatic receiving is unavailable — the specific blockers with a fix for
    each. A configured-but-dead callback base is the nastiest failure mode (the
    variable is set, so reading config alone looks fine while every user is silently
    forced back to copying codes), which is what the reachability probe catches.

    Args:
        probe: Whether to make one HTTP request to the configured callback base to
            confirm it is actually reachable. The request goes to this deployment's
            own address and carries no code or state. Set False to stay
            purely local (no network), at the cost of missing dead-address cases.
    """
    return _f.dumps_result(await _setup.env_check_impl(probe))


async def feishu_auth_redirect_url(probe: bool = True) -> str:
    """Tell the user exactly which redirect URL to register in the Feishu console.

    Call this whenever the user is filling in the redirect URL field (Feishu console →
    Security settings), or asks what address to use. Returns the URL computed for the
    current environment, the alternative candidate for the other deployment shape, and
    the click-path to register it.

    The value must match what the authorize step and the token exchange both send;
    Feishu rejects any mismatch — including a differing port or trailing slash — with
    error 20071 before the user ever sees a consent page.

    Args:
        probe: Whether to also verify the callback base is reachable (see
            ``feishu_auth_env_check``).
    """
    return _f.dumps_result(await _setup.redirect_url_impl(probe))


async def feishu_auth_setup_guide(target: str = "") -> str:
    """Give the concrete steps to make copy-free Feishu authorization work.

    Use this after ``feishu_auth_env_check`` reports blockers, or when the user asks
    how to set authorization up. The steps differ by deployment shape and the advice
    is close to opposite between them: on a local machine the loopback channel needs
    no configuration at all, while on a server the user approves on their own
    computer or phone, so loopback can never come back and a browser-reachable
    callback base is required.

    Server guidance deliberately includes not exposing the Gateway port wholesale:
    ``/sessions`` and ``/chat/completions`` can drive the agent directly, and the
    ``/oauth/*`` endpoints have no authentication of their own.

    Args:
        target: ``"local"``, ``"intranet"``, or ``"public"``. Empty (the default)
            picks the one matching the detected environment. Pass a value explicitly
            when the user is planning a setup they don't have yet, e.g. asking on a
            laptop how to configure the server.
    """
    return _f.dumps_result(await _setup.setup_guide_impl(target))
