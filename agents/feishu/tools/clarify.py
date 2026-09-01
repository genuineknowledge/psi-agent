"""Clarify tool — ask the user a question before proceeding.

Formats a clarification question (multiple-choice or open-ended) into a block
the agent shows to the user, then the agent must END THE TURN and wait: this
runtime has no blocking-input primitive, so the user's reply necessarily
arrives as the NEXT message (history is preserved, so the agent resumes where
it paused). The tool never blocks and never invents an answer — it only builds
the question text.
"""

from __future__ import annotations

_MAX_OPTIONS = 4
_OTHER_LABEL = "Other — type your own answer"
_REPLY_HINT = "回复序号即可, 或直接说你想要的."


async def clarify(
    question: str,
    options: list[str] | None = None,
    recommended: int = 0,
    default: str = "",
) -> str:
    """Ask the user a question when you need clarification, feedback, or a decision before proceeding.

    Use ONLY when you genuinely cannot proceed correctly without the user's
    input: the request is ambiguous, the work has forked into mutually
    exclusive approaches, or a destructive / hard-to-reverse action needs
    sign-off. Do NOT use it for anything you can resolve yourself from the
    files, tools, or a sensible default — discover the answer instead of asking.

    Two modes:

    * **Multiple choice** — pass up to 4 ``options``. A 5th "Other" line is
      appended automatically so the user can type their own answer. The user
      replies with a number (or free text, treated as "Other").
    * **Open-ended** — omit ``options`` (or pass an empty list) to ask a single
      free-text question, optionally offering a ``default``.

    IMPORTANT: this tool only BUILDS the question. After calling it you MUST
    output the returned text to the user and END THE TURN — do not call more
    tools and do not guess past the question. The user's answer will arrive as
    the next message.

    Args:
        question: The single, focused question to ask. State what you need
            decided and why it blocks you. Keep it to one line where possible.
        options: Up to 4 mutually-exclusive, self-explanatory choices for
            multiple-choice mode. Omit or leave empty for an open-ended
            question. More than 4 are rejected — regroup instead.
        recommended: 1-based index of the option you'd recommend; it is marked
            ``(recommended)``. 0 (default) means no recommendation. Ignored in
            open-ended mode.
        default: A sensible default answer to offer in open-ended mode
            ("say 'ok' to accept"). Ignored when ``options`` are given.

    Returns:
        A formatted question block to show the user verbatim, or an ``[Error]``
        string if the arguments are invalid.
    """
    question = question.strip()
    if not question:
        return "[Error] `question` must not be empty."

    opts = [o.strip() for o in (options or []) if o.strip()]

    if not opts:
        # Open-ended mode.
        block = question
        default = default.strip()
        if default:
            block += f"\n(如果没有特别要求, 我会默认 {default} — 直接说 '可以' 即可.)"
        return block

    if len(opts) > _MAX_OPTIONS:
        return (
            f"[Error] At most {_MAX_OPTIONS} options are allowed (got {len(opts)}). "
            "Regroup into fewer, mutually-exclusive choices."
        )

    if not 0 <= recommended <= len(opts):
        return f"[Error] `recommended` must be between 0 and {len(opts)} (got {recommended})."

    lines = [question, ""]
    for i, opt in enumerate(opts, start=1):
        marker = " (recommended)" if i == recommended else ""
        lines.append(f"  {i}. {opt}{marker}")
    lines.append(f"  {len(opts) + 1}. {_OTHER_LABEL}")
    lines.append("")
    lines.append(_REPLY_HINT)
    return "\n".join(lines)
