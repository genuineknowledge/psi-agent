"""Single source of truth for the ``[SEND:]`` / ``[RECV:]`` marker regexes.

Lives at package top level (beside ``_appdata``) because both layers need the
*same* patterns while the dependency edge only runs one way: Channel must not
import Session, and Session/Gateway must not import Channel.

Three call sites parse these markers, and every one of them must agree:

1. ``channel._markers.SendMarkerScanner`` — **detects** a marker and uploads
   the file. This is the only code that actually delivers anything.
2. ``session.history_display.strip_transfer_markers`` — **hides** the marker
   from the chat bubble.
3. ``session.history_display.extract_send_paths`` — **projects** the marker as
   an attachment (Gateway ``/history``, silent-fire delivery).

A mismatch between them is silent and user-visible in both directions: a
pattern only (1) accepts uploads a file whose raw ``[SEND:…]`` text stays in
the transcript; a pattern only (2)/(3) accept hides a marker that was never
delivered, so the reply promises an attachment that does not exist.

Three properties are enforced by ``tests/psi_agent/test_transfer_markers.py``:

- **Newline-free path.** The path class excludes ``\\n`` (rather than using
  ``.`` and relying on its default no-newline behaviour) so the *same* class
  can be used for the strip pattern, where ``[^\\]]*`` would otherwise let a
  stray ``[SEND:`` swallow paragraphs of prose up to a far-away ``]``.
- **Empty path matches.** The quantifier is ``*``, not ``+``. With ``+``, the
  literal text ``[SEND:]`` cannot match at its own ``]`` and the engine runs
  on to the next ``]`` in the message — capturing the prose *and* a following
  real marker as one bogus path, which loses a genuine file. Matching the
  empty marker lets the caller discard it explicitly.
- **Case-insensitive keyword.** Models also write ``[Send:…]`` / ``[send:…]``;
  those are the same marker, so all three patterns carry ``re.IGNORECASE``.
"""

from __future__ import annotations

import re

# Padding tolerated inside the brackets: models write the marker as prose and
# routinely emit ``[ SEND:/tmp/a.docx ]``. Horizontal whitespace only — ``\s``
# would span newlines and reintroduce the run-away match described above.
_PAD = r"[ \t]*"

# Path body: anything except a closing bracket or a newline. See module docstring
# for why the quantifier is ``*`` and why ``\n`` is excluded.
_PATH = r"([^\]\n]*)"

SEND_RE = re.compile(rf"\[{_PAD}SEND{_PAD}:{_PATH}\]", re.IGNORECASE)
"""Capture group 1 = the raw path (caller must ``.strip()`` and drop if empty)."""

RECV_RE = re.compile(rf"\[{_PAD}RECV{_PAD}:{_PATH}\]", re.IGNORECASE)

TRANSFER_MARKER_RE = re.compile(rf"\[{_PAD}(?:SEND|RECV){_PAD}:[^\]\n]*\]", re.IGNORECASE)
"""Either direction, for display-time stripping. Deliberately the same shape as
``SEND_RE``/``RECV_RE`` so "stripped" and "detected" cannot drift apart."""


def send_paths(text: str) -> list[str]:
    """Return ``[SEND:…]`` paths in order, stripped; empty / whitespace skipped.

    The one place that turns marker text into paths, so callers cannot disagree
    about padding or about whether an empty marker counts.
    """
    if not isinstance(text, str) or not text:
        return []
    return [stripped for match in SEND_RE.finditer(text) if (stripped := match.group(1).strip())]
