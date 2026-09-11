"""Stop a tool from being re-called forever, and tell the model why.

`feishu_docs_search` was once called 305 times in a row with reworded keywords
until the upstream answered HTTP 402.  Nothing in the loop was broken: every
call was well-formed, every result was an honest "no matches", and the model
did the only thing it could with that -- try different wording.  The missing
piece was never a cap on calls; it was that **an empty result and an exhausted
search look identical to the model**.

So the fix is a statement, not a silence.  Past the threshold the call is not
dispatched, and the string that takes its place says so: which tool, how many
attempts, that this particular call did **not** run, and what to do instead.
A blank result here would be actively worse than no limit at all -- the model
would read it as "still nothing" and reword once more, which is the exact loop
being closed.  This is the same reasoning
`history_display.truncate_tool_result` already records for truncation: a cut
that does not announce itself gets answered from partial data.

Three counters, because the incidents had three shapes:

- **Consecutive futility, keyed by tool name.**  Rewording defeats any
  argument-keyed counter -- that is what "换词调 305 次" means.  Only the tool
  name is stable across the retries, so that is the key.
- **Verbatim repetition, keyed by tool name and arguments.**  Re-issuing a call
  that already ran cannot produce a new answer; here the arguments are the
  point, and the count is kept whether or not the result was productive.
- **Call-surface errors, keyed to the turn.**  ``Tool not found`` / illegal
  JSON args / ``unexpected keyword`` while *inventing* ``feishu_*`` /
  ``browser_*`` names resets every per-tool streak; a turn-level counter is
  what stops that family of idle loops.

**Scope is one turn.**  The tracker is created per `run()` and dies with it, so
counters cannot leak between unrelated questions -- and a user who follows up
with "try again" gets a real attempt rather than a refusal inherited from
earlier.  What this does *not* cover is a model that spreads the same futile
search across many turns; that volume is `max_tool_rounds`' account, not this
module's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

UNPRODUCTIVE_LIMIT = 4
"""Consecutive empty / failed results from one tool before its next call is refused.

Above the useful range of retrying and well below a runaway.  Rewording a query
two or three times is ordinary research behaviour and stays untouched; the
observed pathology ran to 305.  The limit counts *consecutive* futility, so a
tool that starts producing results gets a clean slate (see
:meth:`ToolCallConvergence.record`).
"""

REPEAT_LIMIT = 5
"""Identical (tool, arguments) attempts allowed before the next one is refused.

Higher than a bare 1–3, deliberately: a repeat is not always pointless.  Tools
here poll external state (a Feishu document that is being edited, a background
process that is still running, meeting transcripts still generating), so a
handful of identical calls can legitimately return something new.  Five is
enough headroom for that; a sixth identical call is treated as a stuck process
or a model that is not reading prior results.
"""

CALL_SURFACE_ERROR_LIMIT = 2
"""Turn-level cap on *call-surface* failures (wrong tool name / invalid args).

Unlike :data:`UNPRODUCTIVE_LIMIT`, this counter is **not** keyed by tool name:
inventing ``feishu_user_get`` then ``feishu_user_search`` would otherwise reset
every streak.  Two such failures in one turn are enough evidence the model is
guessing names or parameters; further calls are refused until it re-reads the
live ``tools`` list / schema (see :data:`CALL_SURFACE_NOTICE`).
"""

REFUSAL_PREFIX = "[本次调用未执行]"
"""Sentinel opening refusal notices, so a notice can be recognized as one.

Serves the same purpose as ``history_display._TRUNCATION_MARKER``: a string this
module produced must be identifiable when it comes back, because it travels as
an ordinary tool result and is indistinguishable from one by shape.  Two
consumers -- :func:`is_refusal_notice` (which keeps refusals out of the
counters) and operators grepping histories for refused calls.
"""

UNPRODUCTIVE_NOTICE = (
    f"{REFUSAL_PREFIX} 工具 {{name}} 已经连续 {{count}} 次没有返回可用结果"
    "(空结果或调用失败), 这一次的调用没有真正发出, 所以下面没有任何新数据。"
    "请注意: 这不等于「又搜了一次还是没有」—— 换个关键词再试同一个工具不会有新结果。"
    "请改用别的工具或别的信息来源, 或者直接告诉用户没有查到, 需要更具体的线索。"
)
"""Sent in place of a refused call after repeated futility.

Every clause is load-bearing.  "未执行" and "没有真正发出" keep the model from
reading this as another empty hit; "这不等于又搜了一次还是没有" names the wrong
inference explicitly, because that inference is what produced 305 calls; and the
last sentence gives the two exits that exist -- change source, or report back --
so the model is not left to invent a third by rewording again.
"""

REPEAT_NOTICE = (
    f"{REFUSAL_PREFIX} 同一个查询 {{name}} 带完全相同的参数已经调用过 {{count}} 次, "
    "这一次没有真正发出。相同参数不会得到不同结果, 请查看上面已有的返回, "
    "或者换一组参数、换一个工具。"
)
"""Sent in place of a refused verbatim repeat.

Points at the earlier results rather than describing them: they are still in the
history the model is reading, so restating them here would spend budget to say
something twice.
"""

CALL_SURFACE_NOTICE = (
    f"{REFUSAL_PREFIX} 本回合已连续 {{count}} 次因**工具名不存在或参数非法**失败"
    "（Tool not found / 参数不是合法 JSON 对象 / unexpected keyword 等）。"
    "这一次的调用没有真正发出。"
    "请立刻停止换名或微调参数重试："
    "重新对照本回合请求里的 tools 列表与目标工具的参数 schema（或 skill 参数表），"
    "确认确切工具名与参数名后再调；"
    "飞书能力不要发明 feishu_*（多数走 feishu_api）；"
    "browser MCP 表内名走 browser_call；"
    "subagent 只用 subagent_plan / subagent_wait / subagent_chat。"
    "若仍不确定，用 tool_search（如有）或改用文档写明的入口，不要继续连打。"
)
"""Turn-level stop after guessed tool names / bad parameters.

Must not read as another empty search hit: it names the failure class and the
recovery action (re-scan the live tool list), which is what stops prefix-family
invention loops that per-tool futility cannot see.
"""

_EMPTY_JSON_HINTS = ("items", "data", "results", "records", "files", "entities", "matches")
"""Keys whose empty list marks a structurally empty payload.

Tools here answer in JSON, so "no matches" usually arrives as a populated
envelope wrapping an empty collection -- `{"ok": true, "items": []}` is not an
empty string and would pass any length check.
"""

_NO_RESULT_MARKERS = (
    "未找到",
    "没有找到",
    "没有查到",
    "查询无结果",
    "无匹配",
    "no results",
    "no matches",
    "not found",
)
"""Prose forms of the same thing, for tools that answer in text rather than JSON.

Matched case-insensitively against the whole result.  Kept short on purpose: a
loose marker list would classify a real answer that merely *mentions* one of
these phrases as futile, and refusing a working tool is worse than allowing one
extra retry.
"""

_CALL_SURFACE_PARAM_MARKERS = (
    "unexpected keyword",
    "required keyword",
    "missing",
    "got an unexpected",
    "takes no arguments",
    "required positional",
    "arguments must be",
)


def _args_key(args: dict[str, Any]) -> str:
    """Order-independent identity for an argument set.

    `sort_keys` because the model emits JSON objects whose key order varies
    between otherwise identical calls; without it, the same query reordered
    would count as a fresh attempt and the repeat counter would never fire.
    """
    try:
        return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        # Unserializable arguments are rare and never worth failing a turn over;
        # falling back to ``repr`` keeps the key stable within the process.
        return repr(sorted(args.items(), key=lambda kv: kv[0]))


def is_refusal_notice(result: str) -> bool:
    """Whether this string is one of this module's own notices."""
    return result.lstrip().startswith(REFUSAL_PREFIX)


def is_call_surface_error(result: str) -> bool:
    """Whether the failure is a wrong tool name or illegal / mismatched args.

    Distinct from "the tool ran and found nothing": recovery is re-reading the
    live tool list / schema, not rewording a query.  Matched against the strings
    ``SessionAgent`` actually writes for not-found, empty name, bad JSON args,
    and common ``TypeError`` shapes from ``func(**args)``.
    """
    text = result.strip()
    if not text:
        return False
    lowered = text.casefold()
    if "tool '" in lowered and "not found" in lowered:
        return True
    if "empty tool call name" in lowered:
        return True
    if "arguments must be" in lowered:
        return True
    if lowered.startswith("error executing tool"):
        return any(marker in lowered for marker in _CALL_SURFACE_PARAM_MARKERS)
    return False


def is_unproductive_result(result: str) -> bool:
    """Whether a tool result carries no usable data.

    Deliberately covers both "it worked and found nothing" and "it did not
    work": from the caller's side they are the same event -- another attempt
    that moved nothing forward -- and the runaway is driven by the retrying,
    not by which of the two happened.
    """
    text = result.strip()
    if not text:
        return True
    lowered = text.casefold()
    if lowered.startswith("error"):
        return True
    if any(marker in lowered for marker in _NO_RESULT_MARKERS):
        return True
    return _is_empty_json_payload(text)


def _is_empty_json_payload(text: str) -> bool:
    """True for JSON that parses to an empty container or an empty envelope."""
    if not text.startswith(("{", "[")):
        return False
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False
    if isinstance(parsed, list):
        return not parsed
    if not isinstance(parsed, dict):
        return False
    # ``total: 0`` is checked before the hint keys: a tool can report a count
    # without echoing the (empty) collection at all.
    if parsed.get("total") == 0 or parsed.get("count") == 0:
        return True
    for key in _EMPTY_JSON_HINTS:
        if key in parsed:
            value = parsed[key]
            if isinstance(value, (list, dict)) and not value:
                return True
    return False


@dataclass
class ToolCallConvergence:
    """Per-turn memory of what has already been tried, and to what effect."""

    unproductive_limit: int = UNPRODUCTIVE_LIMIT
    repeat_limit: int = REPEAT_LIMIT
    call_surface_error_limit: int = CALL_SURFACE_ERROR_LIMIT
    _unproductive: dict[str, int] = field(default_factory=dict)
    _attempts: dict[tuple[str, str], int] = field(default_factory=dict)
    _call_surface_errors: int = 0

    def refusal_for(self, name: str, args: dict[str, Any]) -> str | None:
        """The notice to return instead of dispatching, or ``None`` to dispatch.

        Order: call-surface (turn-level) → futility → verbatim repeat.  Call-surface
        is checked even when ``name`` is empty so a blank tool call after two
        not-found failures still gets the recovery notice.
        """
        if self._call_surface_errors >= self.call_surface_error_limit:
            logger.warning(
                f"Refusing tool call ({name!r}): {self._call_surface_errors} "
                "call-surface errors this turn"
            )
            return CALL_SURFACE_NOTICE.format(count=self._call_surface_errors)
        if not name:
            return None
        futile = self._unproductive.get(name, 0)
        if futile >= self.unproductive_limit:
            logger.warning(f"Refusing tool call ({name!r}): {futile} consecutive unproductive results")
            return UNPRODUCTIVE_NOTICE.format(name=name, count=futile)
        attempts = self._attempts.get((name, _args_key(args)), 0)
        if attempts >= self.repeat_limit:
            logger.warning(f"Refusing tool call ({name!r}): identical arguments already tried {attempts} times")
            return REPEAT_NOTICE.format(name=name, count=attempts)
        return None

    def record(self, name: str, args: dict[str, Any], result: str) -> None:
        """Fold one outcome into the counters.

        Call for every finished slot (including argument-parse failures that
        never dispatched).  Only *dispatched* productive/unproductive paths
        update the per-tool futility and repeat maps; call-surface errors always
        bump the turn-level counter.

        Refusals are never recorded (see :func:`is_refusal_notice`).
        """
        if is_refusal_notice(result):
            return
        if is_call_surface_error(result):
            self._call_surface_errors += 1
            # Still count toward per-name futility when we have a name: same wrong
            # tool called four times should trip that gate too.
            if name:
                self._unproductive[name] = self._unproductive.get(name, 0) + 1
                key = (name, _args_key(args))
                self._attempts[key] = self._attempts.get(key, 0) + 1
            return
        if not name:
            return
        key = (name, _args_key(args))
        self._attempts[key] = self._attempts.get(key, 0) + 1
        if is_unproductive_result(result):
            self._unproductive[name] = self._unproductive.get(name, 0) + 1
        else:
            # A productive result proves the tool and this query shape work, so
            # the futility streak is over.  The counter measures a *streak*, not
            # lifetime volume -- lifetime volume is bounded by max_tool_rounds.
            self._unproductive.pop(name, None)
