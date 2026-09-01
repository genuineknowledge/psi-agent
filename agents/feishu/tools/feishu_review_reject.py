"""Handle 「打回重做」 on a TODO review card: roll the delivery back.

Dispatched by the card's ``action_handlers`` map (``review_reject_rN`` -> this tool).
Rejecting a delivery mirrors the executor's own undo: the linked Feishu task loses
its completion (``completed_at`` cleared), the ledger row returns to 进行中, and the
review card is rebuilt in place with a 「已打回重做」 note. All three writes are
mechanical, so this runs as a direct tool (seconds, no LLM turn). When the executor
re-completes the task, ``feishu_todo_card_tick`` fires again and sends a fresh
review card.
"""

from __future__ import annotations

import json

import _review_card_impl as _review


async def feishu_review_reject(card_action_json: str = "", user_key: str = "") -> str:
    """Reject a delivered todo: uncomplete the task, reset ledger status, rebuild the card.

    Args:
        card_action_json: The ``<feishu_card_action>`` JSON (injected by Session).
        user_key: The clicker's open_id.
    """
    outcome = await _review._handle_review_reject(card_action_json=card_action_json, user_key=user_key)
    return json.dumps(outcome, ensure_ascii=False, default=str)
