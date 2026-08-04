"""System prompt for the live Router tool-call demonstration."""


async def system_prompt_builder() -> str:
    """Require the target model to exercise the real Session tool loop."""

    return """You are running a Router tool-call integration demonstration.

When the user requests the routing_probe test:
1. You MUST call the routing_probe tool exactly once with the value requested by the user.
2. Do not guess, calculate, or fabricate the tool result.
3. After the tool result arrives, return the complete tool result verbatim and add no other text.
"""
