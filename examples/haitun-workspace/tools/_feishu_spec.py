"""Endpoint knowledge as data — parse the ``feishu-*`` skills' endpoint tables.

The point of moving endpoint knowledge out of Python is that a new endpoint should
cost a table row, not a tool. But a Markdown table read by the model is only a
*suggestion*: Feishu's worst failures are silent (a bare ``!A1`` range writes nothing
and still returns success, a mismatched Bitable column is dropped without error), and
no amount of prose stops a model from filling a wrong value.

So each skill carries two views of the same fact:

* the Markdown table — what the model reads to pick an endpoint;
* a fenced ``rules`` block — the same constraints as YAML, enforced here *before* the
  request goes out.

One file, two consumers. Drift between them is a documentation bug, not a silent data
loss, because the rules block is the one that executes.

Rule fields, all optional except ``endpoint``:

    endpoint      "GET /open-apis/contact/v3/users/:user_id" — matched by method+prefix
    token         tenant | user | tenant_then_user — the strategy this endpoint needs
    prefer_tool   name of a dedicated tool; set ``hard: true`` to refuse outright
    why           shown with prefer_tool, explains what hand-building gets wrong
    required      body/query field names that must be present
    fields        per-field: pattern / forbid / max / min / choices / default /
                  requires / max_items / min_items / in / on_fail

``max``/``min`` are **numeric bounds**, not length limits: they coerce with ``float()``
and give up quietly on anything that is not a number (see :func:`_check_field`), so
``max: 255`` on a *title* checks nothing at all and reads as if it does. A length cap on
a string is a ``pattern`` — ``'^[\\s\\S]{1,255}$'`` — with ``[\\s\\S]`` rather than ``.``
so a value containing a newline is judged on its length like any other. Three rules
carried the silent no-op spelling before this was noticed.
    pitfalls      free text surfaced on failure — never enforced, only explained
    paginate      true, or a mapping — follow ``page_token`` until ``has_more`` is false
    confirm       a token the caller must echo before an irreversible call goes out

A field name in ``required``/``fields`` may be qualified with its bucket —
``query.type`` / ``body.type`` — for the endpoints that send two *different* fields
under one name. The drive permission endpoints do exactly that (file type in the
query, member kind in the body), and since ``fields`` is keyed by name, the plain
spelling can only describe one of them while the other goes unchecked.

``paginate`` is what lets a table row replace a hand-written tool. Feishu's paging
protocol is uniform (``page_token`` out, ``has_more`` + ``page_token`` back), so
18 of the 23 paging loops in ``_feishu_impl`` differ only in which key holds the
items and what page size they ask for. Both are declarable:

    paginate: {items: items, page_size: 100, max_pages: 50}

``items`` defaults to ``items`` (19 of 23 endpoints); ``tasks``,
``instance_code_list``, ``grouplist`` and ``memberlist`` are the ones that differ.

``confirm`` is the other capability a table row has to carry, and for a sharper
reason. The dedicated tools guarded their irreversible calls — resigning a user,
deleting a department, deleting a user group — behind a gate the caller had to clear.
Deleting such a tool in favour of a table row would quietly remove the gate and leave
only prose behind, which is a downgrade no amount of documentation makes up for. So
the gate moves into the rule:

    confirm: DELETE_DEPT

The value names the operation ("what is being confirmed"); it is **not** a password
the model may echo. Enforcement lives in ``_feishu_api_impl._confirm_refusal``, which
sends a one-time 6-digit code to the *user* and requires it back — a constant written
in this file would be readable by the model that is asking to use it, and a gate whose
key is printed next to the lock stops nobody. Resigning a user has no undo beyond
``/resurrect``, deleting a group silently strips the permission subject from every
document and approval that referenced it, and a dissolved chat takes all of its
messages and files with it.

A rule matched by *prefix* rather than exactly is downgraded to advice before it is
returned, because prefix matching and refusal compose badly. Feishu hangs unrelated
operations under a collection URI: ``POST /im/v1/messages`` sends a message, while
``POST /im/v1/messages/:message_id/urgent_app`` marks an existing one urgent, and
``POST /im/v1/chats`` requires ``name`` where ``POST /im/v1/chats/:chat_id/members``
has no such field. Left to inherit, the parent's ``required``/``confirm``/``hard``
would make every such child unreachable — refused for a field it does not take, or
told to echo a confirm token belonging to a different irreversible call. ``paginate``
is inherited no more than those: a detail endpoint nested under a collection returns
one object and never pages, so borrowing the collection's loop answers it with an
empty list under ``ok: true``. So a rule enforces only its own endpoint, and lends the
subtree nothing but its token strategy and pitfalls. See :meth:`Rule.as_advice`.
"""

from __future__ import annotations

import copy
import functools
import pathlib
import re
from typing import Any

import yaml

_RULES_BLOCK = re.compile(r"^```rules\s*$(.*?)^```\s*$", re.M | re.S)
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


class Rule:
    """One endpoint's enforceable contract, as loaded from a skill's rules block."""

    __slots__ = (
        "_segments",
        "confirm",
        "endpoint",
        "fields",
        "method",
        "paginate",
        "pitfalls",
        "prefer_hard",
        "prefer_tool",
        "required",
        "source",
        "token",
        "uri",
        "why",
    )

    def __init__(self, raw: dict[str, Any], source: str = "") -> None:
        endpoint = str(raw.get("endpoint", "")).strip()
        self.endpoint = endpoint
        self.method, self.uri = _split_endpoint(endpoint)
        self._segments = [p for p in self.uri.split("/") if p]
        self.token = str(raw.get("token", "") or "").strip()
        self.confirm = str(raw.get("confirm", "") or "").strip()
        tool = raw.get("prefer_tool")
        self.prefer_tool = str(tool).strip() if tool else ""
        self.prefer_hard = bool(raw.get("hard", False))
        self.why = str(raw.get("why", "") or "").strip()
        self.required = [str(x) for x in (raw.get("required") or [])]
        self.fields = dict(raw.get("fields") or {})
        pit = raw.get("pitfalls") or []
        self.pitfalls = [str(p) for p in (pit if isinstance(pit, list) else [pit])]
        self.source = source
        self.paginate = _paginate_spec(raw.get("paginate"))

    def matches(self, method: str, uri: str) -> bool:
        """Whether this rule governs ``method uri``.

        Matching is segment-wise, and by prefix rather than equality, because both
        properties are load-bearing:

        * ``:placeholder`` segments stand for whatever id the caller substituted, so
          the table can be written the way the endpoint is documented
          (``/users/:user_id``) and still match ``/users/ou_abc``;
        * a rule written for ``/values`` must also catch ``/values/Sheet1!A1``, which
          is the shape that silently drops data.

        Comparing whole segments — rather than raw string prefixes — is what keeps
        ``/x/batch`` from also claiming ``/x/batch_v2``.

        A prefix match is not the same claim as an exact one, though — see
        :meth:`governs_exactly` and :meth:`as_advice`.
        """
        if self.method and self.method != method:
            return False
        if not self._segments:
            return False
        parts = [p for p in uri.split("/") if p]
        if len(parts) < len(self._segments):
            return False
        return all(mine.startswith(":") or mine == theirs for mine, theirs in zip(self._segments, parts, strict=False))

    def governs_exactly(self, uri: str) -> bool:
        """Whether ``uri`` is this rule's own endpoint rather than one nested under it."""
        return len([p for p in uri.split("/") if p]) == len(self._segments)

    def as_advice(self) -> Rule:
        """This rule with everything that can *block* a call stripped out.

        What a rule inherited by prefix may still say, and what it may not, differ in
        kind. ``token`` and ``pitfalls`` describe the whole subtree — bitable writes
        want a user token wherever they appear. But ``required``, ``confirm``,
        ``hard`` and field ``default``\\ s describe one operation's payload, and Feishu
        hangs unrelated operations under a collection URI: ``POST /im/v1/chats``
        creates a group and requires ``name``, while ``POST /im/v1/chats/:id/members``
        adds people to one and has no ``name`` at all. Inheriting the payload half
        makes the child unreachable — refused for a missing field it does not take, or
        told to echo a confirm token meant for a different, irreversible call.

        So an inherited rule keeps its advice and drops its authority. An endpoint that
        needs enforcement gets its own row, which wins on specificity anyway.

        ``paginate`` drops too, and for the same reason as ``required`` — it describes one
        operation's *response*, not the subtree's. A collection and the detail endpoint
        nested under it are exactly the pair this goes wrong for: ``GET /calendar/v4/
        calendars`` returns a paged ``calendar_list``, while ``GET .../calendars/
        :calendar_id`` returns one ``calendar`` object and never pages. Inheriting the
        loop makes the detail read follow a ``page_token`` that isn't there and answer
        with an empty ``calendar_list`` — a wrong answer wearing ``ok: true``, which is
        the failure mode this whole module exists to prevent. Feishu nests detail under
        collection routinely (``/im/v1/chats/:chat_id``, ``/drive/v1/files/:file_token/
        versions/:version_id``, ``/bitable/v1/apps/:app_token/tables/:table_id/views``),
        so the loop has to be declared per endpoint, never assumed.
        """
        quiet = copy.copy(self)
        quiet.required = []
        quiet.confirm = ""
        quiet.prefer_hard = False
        quiet.prefer_tool = ""
        quiet.why = ""
        quiet.paginate = None
        # ``fields`` stays: its checks only fire on a field the caller actually sent, so
        # they cannot strand a child. Its ``default``\\s must go — those would inject a
        # body field the child endpoint never declared.
        quiet.fields = {
            name: {k: v for k, v in spec.items() if k != "default"} if isinstance(spec, dict) else spec
            for name, spec in self.fields.items()
        }
        return quiet

    @property
    def specificity(self) -> int:
        """How strong a claim this rule makes, so the closest one wins a tie.

        Depth first, and a literal segment outranks a placeholder at the same depth.
        """
        return sum(1 if seg.startswith(":") else 2 for seg in self._segments)

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"<Rule {self.endpoint!r} from {self.source}>"


#: Guards a declarative paging loop against running forever. Feishu returns
#: ``has_more`` honestly, but a table typo (wrong ``items`` key on an endpoint that
#: keeps echoing a token) must fail loudly rather than spin. 200 pages at the usual
#: page size is far past any real roster.
_MAX_PAGES = 200


def _paginate_spec(raw: Any) -> dict[str, Any] | None:
    """Normalize the ``paginate`` field: ``true`` or a mapping → a settings dict.

    Returns None when paging is off, so the send path can test it as a plain flag.
    """
    if not raw:
        return None
    spec = raw if isinstance(raw, dict) else {}
    try:
        page_size = int(spec.get("page_size", 100))
    except TypeError, ValueError:
        page_size = 100
    try:
        max_pages = min(int(spec.get("max_pages", _MAX_PAGES)), _MAX_PAGES)
    except TypeError, ValueError:
        max_pages = _MAX_PAGES
    return {
        "items": str(spec.get("items", "items")),
        "page_size": page_size,
        "max_pages": max(1, max_pages),
        "param": str(spec.get("param", "page_size")),
    }


def _split_endpoint(endpoint: str) -> tuple[str, str]:
    """``"GET /open-apis/x"`` → ``("GET", "/open-apis/x")``; a bare path keeps method empty."""
    parts = endpoint.split(None, 1)
    if len(parts) == 2 and parts[0].upper() in _METHODS:
        return parts[0].upper(), parts[1].strip()
    return "", endpoint.strip()


def parse_rules(text: str, source: str = "") -> list[Rule]:
    """Every rule in the fenced ``rules`` blocks of one Markdown document.

    A malformed block is skipped rather than raising: a typo in one skill must not
    take down every other endpoint's validation. The block is YAML — either a list of
    rule mappings or a single mapping.
    """
    rules: list[Rule] = []
    for match in _RULES_BLOCK.finditer(text):
        try:
            loaded = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            continue
        if isinstance(loaded, dict):
            loaded = [loaded]
        if not isinstance(loaded, list):
            continue
        for item in loaded:
            if isinstance(item, dict) and str(item.get("endpoint", "")).strip():
                rules.append(Rule(item, source=source))
    return rules


def load_rules(skills_dir: str | pathlib.Path) -> list[Rule]:
    """All rules from ``<skills_dir>/*/SKILL.md``, most specific URI first."""
    root = pathlib.Path(skills_dir)
    found: list[Rule] = []
    if not root.is_dir():
        return found
    for skill in sorted(root.glob("*/SKILL.md")):
        try:
            text = skill.read_text(encoding="utf-8")
        except OSError:
            continue
        if "```rules" not in text:
            continue
        found.extend(parse_rules(text, source=skill.parent.name))
    found.sort(key=lambda r: -r.specificity)
    return found


@functools.lru_cache(maxsize=8)
def _cached(skills_dir: str) -> tuple[Rule, ...]:
    return tuple(load_rules(skills_dir))


def rules_for(skills_dir: str | pathlib.Path, method: str, uri: str) -> Rule | None:
    """The most specific rule governing ``method uri``, or None.

    A rule reached by prefix comes back as advice only (:meth:`Rule.as_advice`), so an
    endpoint without its own row can still pick up the subtree's token strategy but can
    never be refused by a constraint written for its parent.
    """
    for rule in _cached(str(skills_dir)):
        if rule.matches((method or "").upper(), uri or ""):
            return rule if rule.governs_exactly(uri or "") else rule.as_advice()
    return None


def reset_cache() -> None:
    """Drop the parsed-rules cache — for tests and for skill hot-reload."""
    _cached.cache_clear()


#: The three buckets a field can be pinned to, for ``in:`` and for ``bucket.name`` keys.
_BUCKETS = ("body", "query", "paths")


def _split_field(key: str, spec: Any = None) -> tuple[str, str]:
    """A field key as ``(bucket, name)``, where an empty bucket means "look everywhere".

    Two spellings pin a field down, because two different problems need it. ``in: query``
    says where a uniquely-named field rides. A ``body.type`` *key* goes further: it lets
    one rule constrain two same-named fields separately, which the drive permission
    endpoints require — they send a file type in the query and a member kind in the body,
    both called ``type``, and ``fields`` is keyed by name so one entry cannot describe
    both. Without the qualified spelling the body's ``type`` is undeclarable and its
    ``choices`` silently unenforced.
    """
    head, _, rest = key.partition(".")
    if rest and head in _BUCKETS:
        return head, rest
    where = spec.get("in", "") if isinstance(spec, dict) else ""
    where = str(where).strip()
    return (where if where in _BUCKETS else ""), key


def _present(
    name: str,
    body: dict[str, Any],
    query: dict[str, Any],
    paths: dict[str, Any],
    where: str = "",
) -> tuple[bool, Any]:
    """Look a field up across all three argument buckets, or in just one.

    A rule names a field once; whether it rides in the body, the query string, or a
    path placeholder is usually the endpoint's business, not the rule author's. ``where``
    is for the endpoints where that is false — see :func:`_split_field`.
    """
    buckets = {"body": (body,), "query": (query,), "paths": (paths,)}.get(where, (body, query, paths))
    for bucket in buckets:
        if name in bucket:
            return True, bucket[name]
    return False, None


def _among(value: Any, choices: Any) -> bool:
    """Is ``value`` one of ``choices``, compared the way the wire will see it?

    Query values are stringified on the way out (``False`` becomes ``"false"``), but
    validation runs *before* that — it has to, or a refusal would come too late to stop
    the request. So a rule spelling a boolean flag ``["true", "false"]``, which is what
    Feishu actually accepts, would refuse a caller who passed a real JSON ``false``.
    Comparing the stringified form too keeps both spellings working without weakening
    anything: a value outside the list is still refused.
    """
    if value in choices:
        return True
    as_sent = _as_query_text(value)
    return any(as_sent == _as_query_text(choice) for choice in choices)


def _as_query_text(value: Any) -> str:
    """One value as it will appear in the query string."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _check_field(name: str, spec: Any, value: Any) -> str | None:
    """One field against its constraints; returns the violation text or None.

    Values arrive as whatever JSON produced, so numeric bounds coerce and give up
    quietly when the value isn't a number — a type mismatch is Feishu's error to
    report, with its own message. What must not pass silently is a value that *looks*
    valid and loses data.
    """
    if not isinstance(spec, dict):
        return None
    if (pattern := spec.get("pattern")) and isinstance(value, str) and not re.search(str(pattern), value):
        return spec.get("on_fail") or f"{name}={value!r} 不符合要求的格式 ({pattern})"
    # Some of Feishu's rules are about what a value may *not* contain — a department
    # name with a slash returns 43029 — and those cannot be written as a positive
    # pattern without enumerating every legal string.
    if (forbid := spec.get("forbid")) and isinstance(value, str) and re.search(str(forbid), value):
        return spec.get("on_fail") or f"{name}={value!r} 含有不允许的内容 ({forbid})"
    if (choices := spec.get("choices")) and not _among(value, choices):
        return spec.get("on_fail") or f"{name}={value!r} 不在允许取值 {list(choices)} 内"
    for bound, cmp, label in (("max", lambda a, b: a > b, "上限"), ("min", lambda a, b: a < b, "下限")):
        limit = spec.get(bound)
        if limit is None:
            continue
        try:
            if cmp(float(value), float(limit)):
                return spec.get("on_fail") or f"{name}={value!r} 超出{label} {limit}"
        except TypeError, ValueError:
            pass
    if (length := spec.get("max_items")) is not None and isinstance(value, (list, tuple)):
        try:
            if len(value) > int(length):
                return spec.get("on_fail") or f"{name} 有 {len(value)} 项, 超出上限 {length}"
        except TypeError, ValueError:
            pass
    # ``min``/``max`` above coerce with float() and give up on a list, so an *empty
    # array* slips past both. That is not a hypothetical: Feishu's task PATCH reads
    # ``update_fields`` to decide what to change, and an empty one means "change
    # nothing" — answered with code 0. A cap has a floor to match.
    if (least := spec.get("min_items")) is not None and isinstance(value, (list, tuple)):
        try:
            if len(value) < int(least):
                return spec.get("on_fail") or f"{name} 只有 {len(value)} 项, 少于下限 {least}"
        except TypeError, ValueError:
            pass
    return None


def _misplaced(
    name: str,
    where: str,
    body: dict[str, Any],
    query: dict[str, Any],
    paths: dict[str, Any],
) -> str:
    """The bucket *name* was actually passed in, when the rule pins it elsewhere.

    Empty string means it is not sitting in the wrong bucket. A pinned field that is
    simply absent is not misplaced — that is what ``required`` is for.
    """
    for bucket, holder in (("body", body), ("query", query), ("paths", paths)):
        if bucket != where and name in holder:
            return bucket
    return ""


def validate(
    rule: Rule | None,
    body: dict[str, Any],
    query: dict[str, Any],
    paths: dict[str, Any],
) -> list[str]:
    """Every way this call violates ``rule``. Empty list means send it.

    All violations are collected rather than short-circuiting: a caller who got two
    fields wrong should learn both in one round trip, not discover the second only
    after fixing the first.
    """
    if rule is None:
        return []
    problems: list[str] = []
    # Names the ``required`` pass already complained about being in the wrong bucket, so
    # the ``fields`` pass below does not say the same thing a second time — a field is
    # routinely both required and constrained, and one mistake should read as one line.
    misplaced_said: set[str] = set()
    for key in rule.required:
        where, name = _split_field(key, rule.fields.get(key))
        if _present(name, body, query, paths, where)[0]:
            continue
        # Say which bucket it belongs in rather than "missing": a required field pinned
        # to the query and passed in the body is present, just unreachable, and being
        # told it is missing sends the caller looking for the wrong mistake.
        if where and (wrong := _misplaced(name, where, body, query, paths)):
            problems.append(f"必填字段 {name} 放在 {wrong} 里了, 这个端点要求它在 {where} 里")
            misplaced_said.add(name)
        else:
            problems.append(f"缺少必填字段 {name}")
    for key, spec in rule.fields.items():
        where, name = _split_field(key, spec)
        found, value = _present(name, body, query, paths, where)
        # A field pinned to one bucket but passed in another used to be invisible here:
        # the lookup was scoped to the declared bucket, found nothing, and fell through
        # to ``continue`` as if it were absent — so the request went out with the field
        # in the wrong place and Feishu answered 99992402 (field validation failed).
        # The table already knows where it belongs, which is exactly enough to say so.
        if (
            not found
            and where
            and name not in misplaced_said
            and (wrong := _misplaced(name, where, body, query, paths))
        ):
            problems.append(f"{name} 放在 {wrong} 里了, 这个端点要求它在 {where} 里")
            continue
        if not found:
            continue
        if isinstance(spec, dict) and (need := spec.get("requires")):
            for other in need if isinstance(need, list) else [need]:
                other_where, other_name = _split_field(str(other), rule.fields.get(str(other)))
                if not _present(other_name, body, query, paths, other_where)[0]:
                    problems.append(f"给了 {name} 就必须同时给 {other_name}")
        if (violation := _check_field(name, spec, value)) is not None:
            problems.append(violation)
    return problems


def defaults_for(rule: Rule | None) -> dict[str, dict[str, Any]]:
    """Field defaults declared by ``rule``, split by which bucket they belong to.

    Only ``query`` and ``body`` get defaults; a missing path placeholder is already a
    hard error upstream and guessing one would send the request somewhere else.
    """
    out: dict[str, dict[str, Any]] = {"query": {}, "body": {}}
    if rule is None:
        return out
    for key, spec in rule.fields.items():
        if isinstance(spec, dict) and "default" in spec:
            bucket, name = _split_field(key, spec)
            out.setdefault(bucket if bucket in out else "query", {})[name] = spec["default"]
    return out
