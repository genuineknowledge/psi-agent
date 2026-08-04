"""Deterministic tool used to verify Router tool-call continuity."""


async def routing_probe(value: str) -> str:
    """Return a deterministic token that can only come from this test tool.

    Args:
        value: Short label to include in the returned verification token.

    Returns:
        A deterministic verification token for the supplied label.
    """

    return f"ROUTING_TOOL_OK::{value.upper()}::7391"
