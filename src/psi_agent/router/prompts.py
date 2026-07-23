from __future__ import annotations

from .models import Upstream

ROUTING_SYSTEM_PROMPT = """\
请选择与对话内容最匹配的唯一候选模型。

判断规则:
1. 将对话上下文中最后一个 [USER] 区块视为最新用户任务。
2. 先判断最新用户任务是否与上文任务相关, 包括继续执行、补充要求、修改结果、追问、引用或依赖上文信息。
3. 如果相关, 结合上文任务和最新用户任务判断整体任务类型, 再选择最匹配的候选模型。
4. 如果不相关, 忽略上文, 只根据最新用户任务判断并选择候选模型。
5. 不要因为上文中出现过某类任务, 就把无关的新任务错误地归为同一类型。

{candidates}
仅返回 JSON: {{"candidate":0,"reason":"简短说明选择理由"}}。
"""


def build_routing_messages(context: str, targets: tuple[Upstream, ...]) -> list[dict[str, str]]:
    candidates = "\n".join(f"候选模型 {index}: {target.description}" for index, target in enumerate(targets))
    system = ROUTING_SYSTEM_PROMPT.format(candidates=candidates).strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": context}]
