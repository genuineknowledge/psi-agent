"""Compile a generic card DSL (XML) declaration into Feishu card 2.0 JSON.

This is the tool face of the card rendering engine (``_card_dsl``). Business code
declares a card in the small XML DSL — elements ``card`` / ``info`` / ``score`` /
``comment`` / ``action-row`` / ``button`` / ``list`` (with ``row`` children for
multi-row todo cards) — and this tool returns the compiled Feishu card JSON plus
the action-handler map. The engine absorbs the Feishu protocol restrictions
(button single-use → round naming, input value → confirm, unsupported components
→ not in the vocabulary), so the XML never mentions a Feishu concept. Cards
bearing ``<list>`` compile to the legacy multi-row todo shape and their callbacks
dispatch to the existing ``feishu_todo_card_tick``/``_untick`` tools. See the
``card-dsl`` skill for the vocabulary, field rules, the semantic color mapping,
and worked examples.

The returned JSON is what ``feishu_message_send_card`` consumes: pass
``json.dumps(result["card"])`` as ``card_json`` and
``json.dumps(result["handlers"])`` as ``action_handlers_json`` (with
``multi_use=True``). Business actions (ledger writes etc.) stay in the existing
direct-dispatch tools; this tool only compiles cards.

Example (review card, ~10 lines of XML):

    <card title="TODO 评价" template="blue">
      <info label="执行人" value="黄子建"/>
      <score min="1" max="5" rounds="20" bind-record="recXXX"/>
      <comment placeholder="写点评语" bind-record="recXXX"/>
      <action-row>
        <button text="打回重做" type="reject" action="review_reject"/>
      </action-row>
    </card>

Args:
    template: Optional fixed-card template name ("review-card" / "todo-card",
        from ``skills/card-dsl/templates/``). When given, ``card_xml`` is ignored
        and ``values_json`` fills the template's ``{key}`` placeholders — the
        preferred path for fixed card types: fill data only, never hand-write
        structure. When empty, ``card_xml`` is the full DSL declaration.
    card_xml: The card DSL declaration (XML string) when no template is used.
    values_json: Template placeholder values — e.g. review-card:
        ``{"owner_name": "黄子建", "title": "...", "delivered_at": "...",
        "record_id": "recX", "selected_score": 0}``; todo-card:
        ``{"title": "今日 TODO", "rows": [{"title": "...", "task_guid": "...",
        "detail": "...", "bind_record": "recX", "done": false}]}``.
    context_json: Optional JSON object of business facts injected into every
        callback value — e.g. ``{"owner_name": "黄子建", "task_guid": "..."}``.
        ``bind-record`` on an element overrides the context's ``record_id`` for
        that element. Use ``comment_value`` to pre-fill the comment input.
        For todo cards, ``ledger_app_token``/``ledger_table_id`` ride here.
    round_: Current action round (0-based). Bump by 1 on every rebuild so the
        card stays operable — Feishu action ids are single-use per card.
    handler_overrides_json: Optional JSON object mapping extra action names to
        direct-dispatch tools, e.g. ``{"review_reject": "feishu_review_reject"}``
        for actions outside the built-in map.
"""

from __future__ import annotations

import json

import _card_dsl


async def feishu_card_render(
    template: str = "",
    card_xml: str = "",
    values_json: str = "{}",
    context_json: str = "{}",
    round_: int = 0,
    handler_overrides_json: str = "{}",
) -> str:
    """Compile a card DSL template or XML declaration into Feishu card JSON + handlers."""
    if template.strip():
        outcome = _card_dsl.render_template(
            template_name=template,
            values_json=values_json,
            context_json=context_json,
            round_=round_,
            handler_overrides_json=handler_overrides_json,
        )
    else:
        outcome = _card_dsl.render_card(
            card_xml=card_xml,
            context_json=context_json,
            round_=round_,
            handler_overrides_json=handler_overrides_json,
        )
    return json.dumps(outcome, ensure_ascii=False)
