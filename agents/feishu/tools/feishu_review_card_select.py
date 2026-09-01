"""Handle a score-button click on a TODO review card: write the score to the ledger.

Dispatched by the card's ``action_handlers`` map. Clicking a 1-5 score button
**is** the scoring — there is no 「提交」 button on this card. The tool writes
``mentor打分`` to the ledger row immediately and rebuilds the card to highlight
「✓ N 分」; clicking again with another score overwrites it. The comment is
written separately by ``feishu_review_input``.
"""

from __future__ import annotations

import json

import _review_card_impl as _review


async def feishu_review_card_select(card_action_json: str = "", user_key: str = "") -> str:
    """Write the clicked score to the ledger row and highlight it on the card.

    Args:
        card_action_json: The ``<feishu_card_action>`` JSON (injected by Session).
        user_key: The clicker's open_id.
    """
    outcome = await _review._handle_score_select(card_action_json=card_action_json, user_key=user_key)
    return json.dumps(outcome, ensure_ascii=False, default=str)
