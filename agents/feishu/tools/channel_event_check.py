"""Self-check agent-package channel events before trusting a trigger.

Answers the questions you cannot answer by reading files alone:

1. ``shape`` — what does the platform event actually look like once it reaches
   ``map_event``? Field layout lives in the ``lark_channel`` SDK model classes,
   not in docs, so sample events here are built from those real models.
2. ``probe`` — does my ``map.py`` return an envelope for that event, and if it
   returns nothing, which field paths did it have available?
3. ``list`` — which ``channel_events/`` definitions loaded, and are the mapper
   and my ``TRIGGER.md`` talking about the same event name?

Nothing here touches the live Channel: mappers are loaded and called in-process
on a sample payload, so a probe can never post a real event or message anyone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import _runtime_paths as _paths

# Aliased under a leading underscore: the loader treats every public async
# name in this module as a tool, and this helper's ``Path`` parameter is not a
# JSON-Schema type, so exposing it would fail signature conversion.
from psi_agent.channel._event_defs import load_channel_event_defs as _load_channel_event_defs
from psi_agent.channel._event_shapes import describe_shape, non_null_paths, plainify

# platform_event → (SDK module suffix, class name, sample event body).
# Bodies mirror Feishu's documented payloads; the SDK model then normalizes them
# the same way it does for a live delivery, so nesting cannot drift from reality.
_SAMPLES: dict[str, tuple[str, str, dict[str, Any]]] = {
    "im.message.receive_v1": (
        "p2_im_message_receive_v1",
        "P2ImMessageReceiveV1",
        {
            "sender": {
                "sender_id": {"open_id": "ou_sample_sender", "user_id": "uid_1"},
                "sender_type": "user",
                "tenant_key": "tk_sample",
            },
            "message": {
                "message_id": "om_sample_message",
                "chat_id": "oc_sample_chat",
                "chat_type": "group",
                "message_type": "text",
                "content": '{"text":"sample"}',
            },
        },
    ),
    "im.chat.member.user.added_v1": (
        "p2_im_chat_member_user_added_v1",
        "P2ImChatMemberUserAddedV1",
        {
            "chat_id": "oc_sample_chat",
            "name": "Sample Group",
            "operator_id": {"open_id": "ou_sample_operator"},
            "users": [
                {
                    "name": "Sample Newcomer",
                    "tenant_key": "tk_sample",
                    "user_id": {"open_id": "ou_sample_newcomer", "user_id": "uid_2"},
                }
            ],
        },
    ),
    "im.chat.member.user.deleted_v1": (
        "p2_im_chat_member_user_deleted_v1",
        "P2ImChatMemberUserDeletedV1",
        {
            "chat_id": "oc_sample_chat",
            "users": [{"name": "Sample Leaver", "user_id": {"open_id": "ou_sample_leaver"}}],
        },
    ),
    "im.message.recalled_v1": (
        "p2_im_message_recalled_v1",
        "P2ImMessageRecalledV1",
        {
            "message_id": "om_sample_message",
            "chat_id": "oc_sample_chat",
            "recall_time": "1754200000",
            "recall_type": "message_owner",
        },
    ),
    "im.message.reaction.created_v1": (
        "p2_im_message_reaction_created_v1",
        "P2ImMessageReactionCreatedV1",
        {
            "message_id": "om_sample_message",
            "reaction_type": {"emoji_type": "SMILE"},
            "operator_type": "user",
            "user_id": {"open_id": "ou_sample_reactor"},
        },
    ),
    "im.chat.updated_v1": (
        "p2_im_chat_updated_v1",
        "P2ImChatUpdatedV1",
        {
            "chat_id": "oc_sample_chat",
            "operator_id": {"open_id": "ou_sample_operator"},
            "after_change": {"name": "New Name"},
            "before_change": {"name": "Old Name"},
        },
    ),
    "im.chat.disbanded_v1": (
        "p2_im_chat_disbanded_v1",
        "P2ImChatDisbandedV1",
        {
            "chat_id": "oc_sample_chat",
            "operator_id": {"open_id": "ou_sample_operator"},
            "name": "Sample Group",
        },
    ),
    # Contact events have no P2 model in this SDK build — Channel delivers the
    # raw dict, so the sample is that dict.
    "contact.user.created_v3": (
        "",
        "",
        {
            "object": {
                "open_id": "ou_sample_hire",
                "user_id": "uid_3",
                "name": "Sample Hire",
                "department_ids": ["od_sample"],
            }
        },
    ),
    "contact.user.updated_v3": (
        "",
        "",
        {
            "object": {"open_id": "ou_sample_hire", "job_title": "New Title"},
            "old_object": {"open_id": "ou_sample_hire", "job_title": "Old Title"},
        },
    ),
}

_SAMPLE_EVENT_ID = "evt-sample-0001"


def _build_sample(platform_event: str, overrides: dict[str, Any] | None = None) -> tuple[Any, str]:
    """Build a sample delivery for *platform_event*, as the SDK would hand it over.

    Returns ``(raw_object, note)``. When the SDK ships a P2 model, the sample is
    an instance of that real class — that is the point: it guarantees the nesting
    an agent sees while probing is the nesting the live path produces.
    """
    module_suffix, class_name, body = _SAMPLES[platform_event]
    body = json.loads(json.dumps(body))  # deep copy — never mutate the table
    if overrides:
        body.update(overrides)
    envelope = {
        "schema": "2.0",
        "header": {
            "event_id": _SAMPLE_EVENT_ID,
            "event_type": platform_event,
            "token": "sample-token",
            "create_time": "1754200000000",
            "tenant_key": "tk_sample",
            "app_id": "cli_sample",
        },
        "event": body,
    }
    if not module_suffix:
        return envelope, "plain dict (no P2 model in this SDK build)"
    try:
        module = __import__(f"lark_channel.api.im.v1.model.{module_suffix}", fromlist=[class_name])
        cls = getattr(module, class_name)
        return cls(envelope), f"real SDK model {class_name}"
    except Exception as e:
        return envelope, f"plain dict fallback — SDK model unavailable ({e!r})"


def _raw_to_dict(raw: Any) -> dict[str, Any]:
    """Mirror of the live Channel normalization, via the shared ``plainify``."""
    if isinstance(raw, dict):
        return {str(k): plainify(v) for k, v in raw.items()}
    nested = getattr(raw, "event", None)
    if nested is not None:
        out: dict[str, Any] = {"event": plainify(nested)}
        for attr in ("header", "type", "schema", "ts", "uuid"):
            got = getattr(raw, attr, None)
            if got is not None:
                out[attr] = plainify(got)
        return out
    plain = plainify(raw)
    return plain if isinstance(plain, dict) else {"raw": repr(raw)}


def _event_body(raw_dict: dict[str, Any]) -> Any:
    body = raw_dict.get("event")
    return body if isinstance(body, dict) else raw_dict


def _render_shape(platform_event: str, raw_dict: dict[str, Any], note: str) -> str:
    body = _event_body(raw_dict)
    paths = non_null_paths(body)
    lines = [
        f"# {platform_event}",
        f"Sample built from: {note}",
        "",
        "## Shape as map_event sees it",
        f"event{{{describe_shape(body)}}}",
        "",
        "## Readable field paths (relative to `event`)",
    ]
    lines.extend(f"  {_as_subscript(path)}" for path in paths)
    lines += [
        "",
        "## Per-delivery id (for idempotency_key)",
        f"  raw['header']['event_id'] = {raw_dict.get('header', {}).get('event_id')!r}",
        "",
        "Read fields from the paths above verbatim. A path that is not listed",
        "resolves to None and is the usual reason a mapper returns [].",
    ]
    return "\n".join(lines)


def _as_subscript(path: str) -> str:
    """``users[0].user_id.open_id`` → ``event['users'][0]['user_id']['open_id']``.

    Rendered as literal Python subscripts so a mapper author can paste it, and
    so list levels stay visible — ``users`` is a list, and subscripting it with
    a string is its own silent failure.
    """
    out = "event"
    for part in path.split("."):
        name, _, indexes = part.partition("[")
        if name:
            out += f"[{name!r}]"
        if indexes:
            out += "[" + indexes
    return out


async def _load_defs() -> list[Any]:
    """Load the same definitions the live Channel loads, from the agent root."""
    agent_root = await _paths.resolve_agent().resolve()
    return await _load_channel_event_defs(Path(str(agent_root)), "feishu")


def _render_list(defs: list[Any]) -> str:
    if not defs:
        return "No channel_events/feishu definitions loaded. A trigger cannot fire without one."
    lines = ["# Loaded channel_events/feishu", ""]
    for edef in sorted(defs, key=lambda d: d.name):
        if edef.kind == "platform_map":
            probe = " (probe: platform_event known)" if edef.platform_event in _SAMPLES else " (no sample available)"
            filt = "  [filters: true — [] is normal for most deliveries]" if getattr(edef, "filters", False) else ""
            lines.append(f"- {edef.name}  ← {edef.platform_event}{probe}{filt}")
        else:
            lines.append(f"- {edef.name}  ({edef.kind}, produce.py)")
    lines += [
        "",
        "Your TRIGGER.md `event:` must equal one of the names on the left,",
        "otherwise Session receives the envelope and matches nothing.",
        "Use action='probe' with event=<name> to dry-run its mapper.",
    ]
    return "\n".join(lines)


def _render_probe(edef: Any, raw_dict: dict[str, Any], note: str, envelopes: Any, error: str) -> str:
    body = _event_body(raw_dict)
    lines = [
        f"# Probe {edef.name}  ← {edef.platform_event}",
        f"map.py: {edef.path / 'map.py'}",
        f"Sample built from: {note}",
        "",
    ]
    if error:
        lines += [
            "## Result: map_event RAISED",
            f"  {error}",
            "",
            "The live Channel logs this and drops the event; no trigger fires.",
        ]
    elif not isinstance(envelopes, list):
        lines += [
            "## Result: WRONG RETURN TYPE",
            f"  map_event returned {type(envelopes).__name__}, must be list[dict].",
        ]
    elif not envelopes:
        if getattr(edef, "filters", False):
            lines += [
                "## Result: EMPTY — but this event declares `filters: true`",
                "",
                "So [] may be correct: this mapper subscribes to a broad platform",
                "event and keeps only some deliveries. The sample below is a generic",
                "one, which the filter is entitled to reject. It was given:",
                f"  event{{{describe_shape(body)}}}",
                "",
                "Paths that actually hold a value:",
            ]
            lines.extend(f"  {_as_subscript(path)}" for path in non_null_paths(body))
            lines += [
                "",
                "To exercise the accepting branch, check which fields map.py requires",
                "and confirm the sample carries them — for a field-change filter the",
                "sample needs the changed field present in both `object` and",
                "`old_object`. If the filter rejects everything, that is the bug.",
            ]
        else:
            lines += [
                "## Result: EMPTY — no envelope, no trigger would fire",
                "",
                "The mapper read at least one field that does not exist. It was given:",
                f"  event{{{describe_shape(body)}}}",
                "",
                "Paths that actually hold a value:",
            ]
            lines.extend(f"  {_as_subscript(path)}" for path in non_null_paths(body))
            lines += [
                "",
                "Compare those against the paths in map.py. Note a filter that",
                "compares against a missing field (e.g. chat_type != 'group' when",
                "chat_type is None) also returns [] and looks identical to dedup.",
                "If returning [] is intended for most deliveries, declare",
                "`filters: true` in EVENT.yaml so the live log stops warning.",
            ]
    else:
        lines += [f"## Result: OK — {len(envelopes)} envelope(s)", ""]
        for index, env in enumerate(envelopes):
            if not isinstance(env, dict):
                lines.append(f"  [{index}] NOT A DICT: {type(env).__name__}")
                continue
            lines.append(f"  [{index}] " + json.dumps(env, ensure_ascii=False, indent=2, default=repr))
            key = env.get("idempotency_key")
            if isinstance(key, str) and key.strip():
                if _SAMPLE_EVENT_ID in key:
                    verdict = "unique per delivery — good"
                else:
                    verdict = "does NOT vary per delivery — the 2nd occurrence will be deduped away"
                lines.append(f"      idempotency_key: {verdict}")
            else:
                lines.append("      idempotency_key: omitted — Channel fills it from header.event_id")
        lines += [
            "",
            "A trigger fires when its TRIGGER.md `event:` equals the envelope's",
            f"`event` ({envelopes[0].get('event', edef.name)!r}) and every `filter:` key",
            "matches the payload above exactly.",
        ]
    return "\n".join(lines)


async def channel_event_check(action: str = "list", event: str = "", platform_event: str = "") -> str:
    """Inspect and dry-run agent-package channel events (read-only, no side effects).

    Use this before and after editing ``channel_events/<channel>/<dir>/map.py``
    to see whether a platform event maps to an envelope at all — a mapper that
    returns ``[]`` is otherwise indistinguishable from a deduped event in the
    logs.

    Actions:

    - ``list``: which definitions loaded, and which platform event each maps.
      Start here to confirm your ``TRIGGER.md`` ``event:`` name exists.
    - ``shape``: the field layout of a platform event as ``map_event`` receives
      it, plus every readable field path. Answers "where do I read chat_id
      from" — for ``im.message.receive_v1`` it is ``event['message']['chat_id']``,
      not ``event['chat_id']``.
    - ``probe``: run a definition's own ``map.py`` against a sample event and
      show the envelopes it produced, or — when it produced none — the shape and
      paths it actually had, so a wrong field path is visible by comparison.

    Args:
        action: list | shape | probe
        event: Catalog event name for ``probe`` (e.g. feishu.chat.member_added)
        platform_event: Feishu native type for ``shape`` (e.g. im.message.receive_v1)
    """
    act = action.strip().casefold() or "list"

    if act == "shape":
        target = platform_event.strip()
        if not target:
            return "[Error] shape requires platform_event=. Known: " + ", ".join(sorted(_SAMPLES)) + "."
        if target not in _SAMPLES:
            return f"[Error] No sample for {target!r}. Known: {', '.join(sorted(_SAMPLES))}."
        raw, note = _build_sample(target)
        return _render_shape(target, _raw_to_dict(raw), note)

    try:
        defs = await _load_defs()
    except Exception as e:
        return f"[Error] Cannot load channel_events/feishu: {e!r}"

    if act == "list":
        return _render_list(defs)

    if act == "probe":
        name = event.strip()
        if not name:
            return "[Error] probe requires event= (a name from action='list')."
        matches = [d for d in defs if d.name == name and d.kind == "platform_map"]
        if not matches:
            names = ", ".join(sorted(d.name for d in defs if d.kind == "platform_map")) or "(none)"
            return f"[Error] No platform_map event named {name!r}. Loaded: {names}."
        edef = matches[0]
        target = platform_event.strip() or edef.platform_event
        if target not in _SAMPLES:
            return (
                f"[Error] No sample event for {target!r} (declared by {name!r}). Known: {', '.join(sorted(_SAMPLES))}."
            )
        raw, note = _build_sample(target)
        raw_dict = _raw_to_dict(raw)
        envelopes: Any = []
        error = ""
        try:
            envelopes = edef.map_fn(raw_dict) if edef.map_fn else []
        except Exception as e:
            error = repr(e)
        return _render_probe(edef, raw_dict, note, envelopes, error)

    return f"[Error] Unknown action {action!r}: use list | shape | probe."
