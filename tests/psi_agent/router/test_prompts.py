from __future__ import annotations

from psi_agent.router.models import Upstream
from psi_agent.router.prompts import build_routing_messages


def test_build_routing_messages_keeps_prompt_in_dedicated_module() -> None:
    targets = (
        Upstream("secret-model", "http://secret:7001", "simple tasks"),
        Upstream("other-model", "http://other:7002", "complex reasoning"),
    )
    messages = build_routing_messages("[USER]\nsolve this", targets)
    rendered = "\n".join(message["content"] for message in messages)
    assert "请选择与对话内容最匹配的唯一候选模型" in rendered
    assert "先判断最新用户任务是否与上文任务相关" in rendered
    assert "结合上文任务和最新用户任务判断整体任务类型" in rendered
    assert "忽略上文, 只根据最新用户任务判断" in rendered
    assert "仅返回 JSON" in rendered
    assert "简短说明选择理由" in rendered
    assert "候选模型 0: simple tasks" in rendered
    assert "候选模型 1: complex reasoning" in rendered
    assert "secret-model" not in rendered
    assert "http://secret" not in rendered
    assert messages[-1] == {"role": "user", "content": "[USER]\nsolve this"}
