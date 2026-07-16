from __future__ import annotations

from .models import Upstream

ROUTING_SYSTEM_PROMPT = """\
请选择与对话内容最匹配的唯一候选模型。
{candidates}
仅返回 JSON: {{"candidate":0,"reason":"简短说明选择理由"}}。
"""


def build_routing_messages(context: str, targets: tuple[Upstream, ...]) -> list[dict[str, str]]:
    candidates = "\n".join(f"候选模型 {index}: {target.description}" for index, target in enumerate(targets))
    system = ROUTING_SYSTEM_PROMPT.format(candidates=candidates).strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": context}]
