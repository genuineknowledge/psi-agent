"""Handle a score-button click on a TODO review card: show the selection, submit nothing.

Dispatched by the card's ``action_handlers`` map. Clicking a 1-5 score button only
*selects* — this tool rebuilds the card so it shows 「已选: N 分」 and the 「提交」
button now carries that score. No ledger or wiki data is written here; the mentor
still has to press 「提交」 (handled by the ``company-todo-review`` skill), which
records score and comment together. Score buttons stay live so the pick can be
changed before submitting.
"""

from __future__ import annotations

import json

import _review_card_impl as _review


async def feishu_review_card_select(card_action_json: str = "", user_key: str = "") -> str:
    """Update a review card to show the selected score, without submitting it.

    Args:
        card_action_json: The ``<feishu_card_action>`` JSON (injected by Session).
        user_key: The clicker's open_id.
    """
    outcome = await _review._handle_score_select(card_action_json=card_action_json, user_key=user_key)
    return json.dumps(outcome, ensure_ascii=False, default=str)
