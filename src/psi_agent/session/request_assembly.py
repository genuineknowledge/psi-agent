"""The one place that decides how big a request may be — and enforces it.

``session/agent.py`` assembles exactly one request body per model round, from
exactly two inputs: the projected history and the tool schemas.  Every token
this system spends flows through that spot, which is why the budget lives here
rather than anywhere else.

**What moved, and why it is not a new abstraction.**  ``max_context_tokens``
already existed; it was just enforced in the wrong place.  The AI layer
*observed* it (``ai/server.py``: compare ``usage.prompt_tokens`` against the
threshold, emit a signal) and the Session layer *reacted* to it after the fact
(``agent.py._maybe_compact``).  Reacting after the fact means the oversized
request was already sent — and if it was oversized enough to be rejected
outright, the stream never produced usage, so the signal never fired, so
nothing shrank, so the next turn rebuilt the same request.  That deadlock
survived restarts because the history was on disk; it took hand-editing a
history file in production to break it (2026-09-02).

:func:`RequestAssembler.build` closes that loop by construction: an oversized
request can no longer be *assembled*, so it can no longer be sent.

**Two levels of cost control, in strict priority order.**

1. **Elision** (here).  Drop the oldest, largest rows and leave a handle in
   their place.  Deterministic, always succeeds, pure local string work, zero
   upstream calls.  This is what makes the budget a guarantee.
2. **Compaction** (``agent.py._maybe_compact``).  An LLM summarizes the
   history, preserving meaning.  Best-effort, can fail, takes ~18s and holds
   the session lock.

Level 1 owns correctness; level 2 only improves quality.  A failed compaction
no longer means deadlock — it means this turn's elision reads a little rougher.

**Why elision must be hysteretic — see :attr:`SHRINK_TARGET_FRACTION`.**  This
is the constraint that makes a naive implementation *worse* than doing nothing.

**Why characters, not tokens.**  Tokenizing means a tokenizer dependency, and a
per-provider one at that.  Instead the budget is denominated in characters and
the conversion ratio is calibrated from the upstream's own arithmetic: every
successful turn reports ``usage.prompt_tokens`` for a request whose character
count we just measured, so the ratio for the next turn is a division.  A single
hardcoded ratio could not work — measured in this very repo, Chinese-dense
prompt text runs ~1.56 chars/token while ASCII JSON tool results run ~3.5-4,
a 2.6x spread.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from psi_agent.session.history_display import project_history_with_sources

DEFAULT_MAX_CONTEXT_TOKENS = 200_000
"""Fallback token ceiling, read from the same ``PSI_MAX_CONTEXT_TOKENS`` as the
AI layer: one budget observed in two places, not two budgets that can drift.
The AI layer keeps its own reading because it is the side that sees ``usage``;
this side is the one that can act before the fact.

The *number* deliberately differs from the AI layer's 100000 default, because
100000 is below this deployment's fixed overhead.  Measured: a 181218-character
production system prompt is about 117k tokens at the 1.56 chars/token rate of
Chinese prose — so with a 100k ceiling the prompt alone is over budget before a
single history row is added.  That is not a shrinkable condition: eliding every
row still leaves it over, and the same arithmetic is why one attendance task
compacted 50 times while its token count climbed monotonically to 400093.  A
default under the floor would make this module elide all history on every turn
for no benefit, so it sits above the floor instead.  Deployments that want the
old number set the env var; ``adopt_threshold`` additionally declines to adopt a
ceiling that would put the fixed overhead out of reach.
"""

MIN_ADOPTABLE_TOKENS = 150_000
"""Floor on ceilings learned from the AI layer, just above measured overhead.

``adopt_threshold`` exists so the two layers converge, but converging *down*
onto a number below the fixed overhead buys nothing: the request cannot be made
to fit, so every turn would elide everything and still report over budget.  Below
this floor the assembler keeps its own ceiling and says so, once.
"""

DEFAULT_CHARS_PER_TOKEN = 1.5
"""Ratio used before the first calibration lands.

Conservative means *low*, which is the counter-intuitive direction: the budget
is ``tokens x ratio`` characters, so a ratio below the truth under-fills the
request (safe) while a ratio above it overshoots the ceiling (the failure this
module exists to prevent).  1.5 sits just under the densest content measured
here (1.56 chars/token for Chinese prose), so the first turn cannot overshoot
no matter what the session contains.
"""

MIN_CHARS_PER_TOKEN = 1.0
MAX_CHARS_PER_TOKEN = 8.0
"""Clamp on calibrated ratios.

A single malformed ``usage`` (0 tokens for a large body, say) would otherwise
set a ratio that either freezes the session or blows the ceiling for every
later turn.  The band is wide enough to contain both extremes measured here
(1.56 and ~4.0) with room to spare, so a real ratio is never clipped.
"""

SHRINK_TARGET_FRACTION = 0.5
"""Once elision runs, shrink to this share of the budget — not to just-under it.

**This is the whole design, not a tuning knob.**  History is append-only, so
every turn leaves the prefix byte-identical and only grows the tail.  That is
why upstream prefix caching hits 99.7% here (measured against deepseek-v4-flash
on 2026-09-03: 19456 of 19519 prompt tokens served from cache on an unchanged
prefix; changing only the tail cost 2 extra tokens).

Any shrink, however, rewrites the prefix — dropped rows are *old* rows — and
takes the entire cache with it.  So an implementation that trims just enough to
fit pays a full cache miss on *every* turn, which is strictly more expensive
than never trimming at all.  Trimming to half the budget instead pays that miss
once and then lets many subsequent turns grow on one stable prefix.

Put plainly: don't throw out one suitcase item a day — clear out half the trunk
once.

``agent.py``'s ``COMPACTION_COOLDOWN_FRACTION`` is the same instinct applied to
compaction, but measured in upstream-reported tokens (only available after the
fact) rather than at the assembly point.  This constant is where that instinct
belongs, because this is the layer that can act before sending.
"""

_HANDLE_PREFIX = "[已省略"
"""Sentinel that makes elision idempotent.

Elision is re-applied on every turn (that is what makes it stick), so a row's
content is fed back through the same code path that produced it.  Without this
check the second pass would elide the handle itself, reporting an ever-shrinking
"original length" and losing the real one — the same decay
``_TRUNCATION_MARKER`` guards against in ``history_display``.
"""

ELISION_HANDLE_TEMPLATE = _HANDLE_PREFIX + " {chars} 字符{label}, 句柄 {handle}]"
"""Placeholder left where a row's content was.

The row is never *removed*: an OpenAI ``tool`` row must stay paired with the
``tool_calls`` entry that requested it, and dropping either half makes the
request malformed.  Equally important, the model must be told that something
was dropped — a silent cut leaves it answering from data it believes complete,
the same trap ``truncate_tool_result`` documents.  The handle names where the
original still lives, so elision is recoverable rather than destructive.

Deliberately terse, and that terseness is load-bearing rather than a style
choice.  The handle is paid **once per elided row**, so a sentence explaining
what a handle is and how to retrieve it — which the first draft of this module
carried, at ~220 chars — turns into tens of kilobytes on exactly the histories
that were already too big, and becomes an un-elidible floor of its own: a first
run against a deliberately tight budget could not get under it no matter how
many rows it elided.  That explanation belongs in the system prompt, where it is
paid once.  Here only the two facts that vary per row are worth the bytes: how
much was dropped, and the key to find it again.
"""

_ELIDIBLE_ROLES = frozenset({"tool", "user", "assistant"})
"""Roles elision may touch.  ``system`` is excluded on purpose.

The system prompt is not history — it is the instruction set, and gutting it
produces a model that has forgotten how to behave while still holding the
conversation.  It is also where compaction merges its summary, so eliding it
would discard exactly the text level 2 just paid an LLM call to produce.  When
the prompt alone exceeds the budget, elision reports that it could not comply
rather than pretending otherwise (see :meth:`RequestAssembler.build`).
"""

_MIN_ELIDIBLE_CHARS = 200
"""Don't bother eliding rows smaller than this.

The handle itself costs ~80 characters, so eliding a 100-char row saves almost
nothing while still invalidating the cache prefix and costing the model a piece
of context.  The floor keeps elision aimed at the rows that actually move the
number.
"""


@dataclass(frozen=True)
class AssembledRequest:
    """One request body, plus the accounting that proves it fits.

    ``chars`` is what the ratio calibration divides into ``prompt_tokens`` on
    the next turn, so it must be the size of the payload *as serialized for the
    wire* — not the sum of the message contents, which omits JSON structure,
    role keys, and the entire ``tools`` array.
    """

    body: dict[str, Any]
    chars: int
    budget_chars: int
    elided_rows: int
    """How many rows had their content replaced by a handle (0 = untouched)."""
    within_budget: bool
    """False only when the un-elidible floor (system prompt + tools) exceeds the
    budget.  Reported rather than raised: refusing to send would take a working
    session offline, and an overshoot the operator can see in the log is more
    useful than a hang."""


def payload_chars(body: dict[str, Any]) -> int:
    """Serialized size of a request body, in characters.

    ``separators`` matches what an HTTP client actually puts on the wire; the
    default ``json.dumps`` spacing would overstate every key by ~2 characters
    and quietly bias the calibrated ratio.  Mirrors ``log_tool_schema_size``
    for the same reason.
    """
    return len(json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str))


def resolve_max_context_tokens(explicit: int = -1) -> int:
    """Token ceiling for this session: explicit, else env, else the default.

    ``-1`` means "resolve for me" and ``0`` means "no ceiling", matching
    ``ai.Ai.max_context_tokens`` exactly — the two layers read the same env var
    with the same sentinels so an operator setting it once affects both.
    """
    if explicit >= 0:
        return explicit
    raw = os.environ.get("PSI_MAX_CONTEXT_TOKENS", "").strip()
    if not raw:
        return DEFAULT_MAX_CONTEXT_TOKENS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(f"Invalid PSI_MAX_CONTEXT_TOKENS={raw!r}, using {DEFAULT_MAX_CONTEXT_TOKENS}")
        return DEFAULT_MAX_CONTEXT_TOKENS
    return value if value >= 0 else DEFAULT_MAX_CONTEXT_TOKENS


@dataclass
class RequestAssembler:
    """Owns the budget for one session's requests.

    Stateful for two reasons, both essential rather than incidental:

    * the calibrated chars/token ratio carries from one turn to the next, and
    * hysteresis needs to remember that it already shrank
      (:attr:`_elided_row_ids`), or every turn would re-decide from scratch and
      re-invalidate the cache prefix — the exact failure
      :attr:`SHRINK_TARGET_FRACTION` exists to prevent.

    One instance per session, held by ``SessionAgent``.
    """

    max_context_tokens: int = -1
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN
    calibrated: bool = False
    """Whether ``chars_per_token`` came from a real ``usage`` report yet."""

    _turn_watermark: int | None = None
    """Index into the stored history at which the current turn's output begins.

    Set by ``agent.py`` right after this turn's user row lands, cleared in that
    turn's ``finally``.  ``None`` means no turn is in flight and nothing is
    exempt — the pre-existing behaviour, which is what non-turn callers get.

    Why a turn boundary rather than a time window: history rows carry no
    timestamp, and elapsed time is not the question anyway.  A 3-second turn is
    covered by any window but never had the problem; a runaway 49-round turn
    (which this deployment has produced) outruns any window worth setting.  The
    watermark asks the question that actually matters — "did this generator call
    produce this row?" — and gets an exact answer.

    See :meth:`begin_turn`.
    """

    _elided_row_ids: set[int] = field(default_factory=set)
    """Identity of rows already elided, by position-independent row identity.

    Keyed by ``id()`` of the stored dict, which is stable for as long as the
    ``Conversation`` holds it in memory — the same object the history list
    carries.  Sticky by design: a row elided last turn stays elided this turn,
    so the prefix the upstream cached remains valid instead of oscillating back
    and forth as the budget breathes.
    """

    def __post_init__(self) -> None:
        self.max_context_tokens = resolve_max_context_tokens(self.max_context_tokens)

    @property
    def budget_chars(self) -> int:
        """The character ceiling this turn.  ``0`` means unlimited."""
        if self.max_context_tokens <= 0:
            return 0
        return int(self.max_context_tokens * self.chars_per_token)

    def begin_turn(self, watermark: int) -> None:
        """Mark where this turn's own output starts in the stored history.

        ``watermark`` is ``len(conversation.messages)`` taken immediately after
        this turn's user row was added, so every row from that index onward was
        produced by the generator call now running.  Those rows are exempt from
        elision for the duration of the turn: they include the reply the user has
        not received yet, and eliding a reply before it is delivered makes the
        upstream believe nothing was said.

        Idempotent per turn, and deliberately last-write-wins: a re-entrant call
        can only move the boundary forward onto a newer turn, which is the
        correct answer if it ever happens.
        """
        self._turn_watermark = watermark

    def end_turn(self) -> None:
        """Drop the exemption.  Must run in the turn's ``finally``.

        The assembler is per *session* and outlives any single turn, while the
        watermark is per *turn*.  A turn that dies without clearing it — raised,
        cancelled, or out of tool rounds — would pin the exemption at its own
        index forever, and every later turn's elision range would be capped
        there.  The symptom is not a crash but a budget that slowly stops being
        enforceable, with nothing in the logs pointing at the cause, which is why
        this matters more than the happy path.
        """
        self._turn_watermark = None

    def calibrate(self, sent_chars: int, prompt_tokens: int) -> None:
        """Update the ratio from one completed turn's own numbers.

        Called with the character count we measured when assembling and the
        ``prompt_tokens`` the upstream reported for that same request, so the
        ratio is this session's real content measured by this provider's real
        tokenizer — no estimation, no dependency.

        Ignores unusable pairs (either side non-positive) and clamps the result:
        a single bad ``usage`` must not poison every later turn.
        """
        if sent_chars <= 0 or prompt_tokens <= 0:
            return
        ratio = sent_chars / prompt_tokens
        clamped = min(max(ratio, MIN_CHARS_PER_TOKEN), MAX_CHARS_PER_TOKEN)
        if clamped != ratio:
            logger.warning(
                f"Calibrated chars/token {ratio:.2f} out of band "
                f"[{MIN_CHARS_PER_TOKEN}, {MAX_CHARS_PER_TOKEN}], clamped to {clamped:.2f} "
                f"(sent_chars={sent_chars}, prompt_tokens={prompt_tokens})"
            )
        previous = self.chars_per_token
        self.chars_per_token = clamped
        self.calibrated = True
        logger.info(
            f"Budget ratio calibrated: {previous:.2f} -> {clamped:.2f} chars/token "
            f"({sent_chars} chars reported as {prompt_tokens} prompt tokens); "
            f"budget now {self.budget_chars} chars"
        )

    def adopt_threshold(self, threshold: int) -> None:
        """Take the ceiling the AI layer just reported, if it differs.

        Both layers read ``PSI_MAX_CONTEXT_TOKENS``, so normally they already
        agree and this is a no-op.  It matters for the deployment where only the
        AI side was configured (production sets it in the gateway's env): rather
        than silently enforcing a different budget than the one that will raise
        the compaction signal, this side defers to the number that is actually
        in force.
        """
        if threshold <= 0 or threshold == self.max_context_tokens:
            return
        if threshold < MIN_ADOPTABLE_TOKENS:
            logger.warning(
                f"Not adopting AI-layer context ceiling {threshold} tokens: it is below "
                f"{MIN_ADOPTABLE_TOKENS}, near or under this deployment's fixed overhead "
                f"(system prompt + tool schemas, measured ~117k tokens). Adopting it would make "
                f"every request unsatisfiable. Keeping {self.max_context_tokens}; raise "
                f"PSI_MAX_CONTEXT_TOKENS on the AI side to converge."
            )
            return
        logger.info(
            f"Adopting AI-layer context ceiling: {self.max_context_tokens} -> {threshold} tokens "
            f"(budget {int(threshold * self.chars_per_token)} chars)"
        )
        self.max_context_tokens = threshold

    def build(
        self,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        extra: dict[str, Any] | None = None,
    ) -> AssembledRequest:
        """Assemble one request body that fits the budget.

        Replaces the bare ``messages_for_ai(history)`` + dict-literal pair at
        the call site.  The projection still happens (via
        ``project_history_for_wire``); what is new is that the result is
        *measured* and, when necessary, *shrunk* before it can be sent.

        Order matters: rows already elided on an earlier turn are re-elided
        first (cache stability), and only then does fresh elision run, and only
        if the payload still exceeds the budget.
        """
        paired = project_history_with_sources(history)
        messages = [projected for projected, _ in paired]
        exempt = self._exempt_row_ids(history)
        elided = self._reapply_sticky_elisions(paired)

        body = self._compose(messages, tools, extra)
        budget = self.budget_chars
        chars = payload_chars(body)
        if budget <= 0 or chars <= budget:
            return AssembledRequest(
                body=body,
                chars=chars,
                budget_chars=budget,
                elided_rows=elided,
                within_budget=True,
            )

        # Over budget: shrink hard, once, rather than shaving to just-fit.
        target = int(budget * SHRINK_TARGET_FRACTION)
        logger.warning(
            f"Request over budget: {chars} chars > {budget} "
            f"(max_context_tokens={self.max_context_tokens}, {self.chars_per_token:.2f} chars/token). "
            f"Eliding oldest/largest rows down to {target} chars ({SHRINK_TARGET_FRACTION:.0%} of budget) "
            f"so the shrunk prefix can serve many later turns instead of one."
        )
        elided += self._elide_until(paired, tools, extra, target, exempt)

        body = self._compose(messages, tools, extra)
        chars = payload_chars(body)
        within = chars <= budget
        if not within:
            logger.error(
                f"Request still {chars} chars after eliding every elidible row (budget {budget}): "
                f"the un-elidible floor exceeds the budget — system prompt, tool schemas, the two "
                f"most recent rows, and one handle per elided row ({elided} of them). Elision cannot "
                f"fix this. Sending anyway — raise max_context_tokens or shrink the prompt / tool set."
            )
        else:
            logger.info(f"Request elided to {chars} chars, {elided} row(s) replaced by handles (budget {budget})")
        return AssembledRequest(
            body=body,
            chars=chars,
            budget_chars=budget,
            elided_rows=elided,
            within_budget=within,
        )

    @staticmethod
    def _compose(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        extra: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build the body in the shape the AI layer expects.

        ``messages`` / ``tools`` / ``stream`` are pinned last-write-wins against
        ``extra`` so a stray ``messages`` key in request params cannot displace
        the payload the budget was just computed over — the call site used to
        ``pop`` those three keys for exactly this reason.
        """
        body: dict[str, Any] = {"messages": messages, "tools": tools, "stream": True}
        if extra:
            body |= {k: v for k, v in extra.items() if k not in ("messages", "tools", "stream")}
        return body

    def _reapply_sticky_elisions(
        self,
        paired: list[tuple[dict[str, Any], dict[str, Any] | None]],
    ) -> int:
        """Re-elide every row that was elided on an earlier turn.

        This is the half of hysteresis that is easy to leave out and impossible
        to notice by reading a single turn: without it the *next* turn projects
        the full row again, the payload grows back, elision fires again, and the
        prefix changes every turn — which is precisely the "full cache miss
        every turn" outcome that makes trimming worse than not trimming.
        """
        count = 0
        for projected, source in paired:
            if source is None or id(source) not in self._elided_row_ids:
                continue
            content = projected.get("content")
            if not isinstance(content, str) or content.startswith(_HANDLE_PREFIX):
                continue
            projected["content"] = self._handle_for(projected, source, len(content))
            count += 1
        return count

    def _exempt_row_ids(self, history: list[dict[str, Any]]) -> frozenset[int]:
        """Identities of the stored rows this turn produced.

        Resolved from stored rows rather than from positions in the projected
        list, because the two lists do not share an index space: the projection
        drops invalid legacy assistant rows and deletes the whole span before the
        last ``compacted``.  Comparing a stored-history watermark against a
        projected position would therefore exempt the wrong rows — and it would
        do so silently, exempting some arbitrary older row while leaving the
        undelivered reply elidible, i.e. reintroducing the bug while looking
        fixed.  ``id()`` is the same key hysteresis already uses.
        """
        watermark = self._turn_watermark
        if watermark is None:
            return frozenset()
        return frozenset(id(row) for row in history[watermark:] if isinstance(row, dict))

    def _elide_until(
        self,
        paired: list[tuple[dict[str, Any], dict[str, Any] | None]],
        tools: list[dict[str, Any]],
        extra: dict[str, Any] | None,
        target_chars: int,
        exempt: frozenset[int] = frozenset(),
    ) -> int:
        """Elide oldest-largest-first until the payload fits ``target_chars``.

        "Oldest and largest" rather than purely largest: age is what makes a row
        safe to lose (the model has already acted on it, and recent turns carry
        the live intent), while size is what makes eliding it worth the cache
        invalidation.  Ordering by size within the older half gets both without
        needing a weighting constant nobody could justify.

        Re-measures the whole body after each elision instead of subtracting
        estimated savings: the handle has its own length, JSON escaping makes
        the delta content-dependent, and the number this returns has to be the
        real one — it is what the budget guarantee rests on.
        """
        candidates = self._elidible_candidates(paired, exempt)
        count = 0
        for projected, source in candidates:
            if payload_chars(self._compose([p for p, _ in paired], tools, extra)) <= target_chars:
                break
            content = projected.get("content")
            if not isinstance(content, str):
                continue
            projected["content"] = self._handle_for(projected, source, len(content))
            self._elided_row_ids.add(id(source))
            count += 1
        return count

    @staticmethod
    def _elidible_candidates(
        paired: list[tuple[dict[str, Any], dict[str, Any] | None]],
        exempt: frozenset[int] = frozenset(),
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Elidible rows, oldest-half-largest-first.

        Excluded, each for its own reason:

        * ``system`` — the instruction set, and where compaction's summary
          lands (see ``_ELIDIBLE_ROLES``).
        * the last two rows — the current user turn and the assistant/tool
          exchange in flight.  Eliding what the model is answering *right now*
          would break the turn rather than trim it.
        * anything in ``exempt`` — rows this turn produced (see
          :meth:`begin_turn`).  The last-two guard was the only protection these
          had, and it stops covering them as soon as a third row lands: in a
          multi-round turn the assistant row from round 1 sits well inside
          ``paired[:-2]`` by round 3, behind several tool results.  Its content
          is output the user has not received yet, ``[SEND:]`` markers included.
        * rows already handled, and rows below ``_MIN_ELIDIBLE_CHARS``.
        * rows with non-string content (multimodal block lists): there is no
          single place to put a handle, and mangling the blocks is worse than
          keeping them.
        """
        if len(paired) <= 2:
            return []
        eligible: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
        for position, (projected, source) in enumerate(paired[:-2]):
            if source is None:
                continue
            if id(source) in exempt:
                continue
            if projected.get("role") not in _ELIDIBLE_ROLES:
                continue
            content = projected.get("content")
            # ``_MIN_ELIDIBLE_CHARS`` is what keeps already-elided rows out of
            # this list: a handle is ~40 chars, far under the floor, so it can
            # never be selected and re-elided.  An explicit ``_HANDLE_PREFIX``
            # check here used to sit alongside this one; mutation review showed
            # deleting it kept every test green, because it never fired.  Left
            # as a comment instead of a dead branch — but note the coupling: if
            # the floor ever drops below the handle length, this needs the
            # explicit check back, and ``test_second_shrink_leaves_earlier_
            # handles_byte_identical`` is what will catch it.
            if not isinstance(content, str) or len(content) < _MIN_ELIDIBLE_CHARS:
                continue
            eligible.append((position, len(content), projected, source))

        # Oldest half first, biggest within it; then the newer half by size, so
        # a single enormous recent tool result is still reachable when the older
        # rows do not add up to the target.
        midpoint = max(len(eligible) // 2, 1)
        older = sorted(eligible[:midpoint], key=lambda e: -e[1])
        newer = sorted(eligible[midpoint:], key=lambda e: -e[1])
        return [(projected, source) for _, _, projected, source in older + newer]

    @staticmethod
    def _handle_for(projected: dict[str, Any], source: dict[str, Any], chars: int) -> str:
        """The placeholder text left in a row's place.

        Carries the original length and a handle so the model can tell the
        difference between "this was long and is retrievable" and "this was
        empty".  The handle is the tool call id where there is one (that is the
        identifier the model itself used to request the result) and otherwise
        the row's role plus its stored ordinal — enough to find the row in the
        session's history JSONL, which is where the original still is.
        """
        role = str(projected.get("role", "?"))
        call_id = source.get("tool_call_id")
        handle = str(call_id) if isinstance(call_id, str) and call_id else f"{role}#{id(source) % 1_000_000:06d}"
        name = source.get("name")
        label = f" ({name})" if isinstance(name, str) and name else ""
        return ELISION_HANDLE_TEMPLATE.format(kind=role, chars=chars, label=label, handle=handle)
