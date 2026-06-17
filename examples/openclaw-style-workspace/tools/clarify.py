"""Clarify tool — surface a clarifying question to the user."""

from __future__ import annotations


async def clarify(question: str, options: list[str] | None = None) -> str:
    """Return a formatted clarifying question for the user to answer.

    Since psi-agent does not have an interactive prompt mechanism at the tool
    level, this tool formats the question and options as a reply that the
    agent should present to the user and wait for their response.

    Args:
        question: The clarifying question to ask.
        options: Optional list of choices to present to the user.

    Returns:
        A formatted question string ready to present to the user.
    """
    if not question.strip():
        return "[Error] 'question' must not be empty."

    lines = [f"**Clarification needed:** {question.strip()}"]
    if options:
        lines.append("")
        for i, opt in enumerate(options, 1):
            lines.append(f"{i}. {opt}")

    return "\n".join(lines)
