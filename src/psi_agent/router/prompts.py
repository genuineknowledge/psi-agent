from __future__ import annotations

from .models import Upstream

ROUTING_SYSTEM_PROMPT = """\
Select the single candidate whose description best matches the conversation.
{candidates}
Return JSON only: {{"candidate":0,"reason":"brief explanation"}}.
"""


def build_routing_messages(context: str, targets: tuple[Upstream, ...]) -> list[dict[str, str]]:
    candidates = "\n".join(f"Candidate {index}: {target.description}" for index, target in enumerate(targets))
    system = ROUTING_SYSTEM_PROMPT.format(candidates=candidates).strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": context}]
