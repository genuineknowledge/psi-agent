"""Handle the comment input's callback on a TODO review card: write the comment to the ledger.

Dispatched by the card's ``action_handlers`` map (``review_input`` -> this tool).
The card 2.0 submit button cannot carry the input's value (no form_container
support on this app), so the typed comment rides on the input's own callback —
this tool writes ``mentor评语`` straight to the ledger row (bot identity; the
app is a ledger collaborator), so the mentor report always shows the latest
comment. The score itself is written separately on 「提交」 by the
``company-todo-review`` skill.
"""

from __future__ import annotations

import json

import _review_card_impl as _review


async def feishu_review_input(card_action_json: str = "", user_key: str = "") -> str:
    """Write the typed review comment to the ledger row.

    Args:
        card_action_json: The ``<feishu_card_action>`` JSON (injected by Session).
        user_key: The clicker's open_id.
    """
    outcome = await _review._handle_review_input(card_action_json=card_action_json, user_key=user_key)
    return json.dumps(outcome, ensure_ascii=False, default=str)
