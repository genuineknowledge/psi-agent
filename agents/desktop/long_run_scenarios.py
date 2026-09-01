"""Deterministic multi-domain scenario catalogue for long-run stability tests."""

from __future__ import annotations

from typing import NamedTuple


class StabilityTurn(NamedTuple):
    index: int
    event_id: str
    domain: str
    topic: str
    depth: str
    intent: str
    question: str
    fault: str
    duplicate: bool


_DOMAINS = (
    ("corporate-law", "股东协议与公司治理"),
    ("software-engineering", "CI/CD 与软件交付"),
    ("agent-systems", "Agent 架构与工具调用"),
    ("startup-finance", "科技公司投融资"),
    ("machine-learning", "机器学习与模型评估"),
    ("product-management", "产品决策与路线图"),
    ("cybersecurity", "网络安全与数据合规"),
    ("history-economics", "技术史与宏观经济"),
    ("cross-domain-governance", "AI 治理与法律工程"),
    ("mixed-review", "跨领域复习与决策"),
)

_FAULTS = {
    201: "network_timeout",
    203: "invalid_json",
    205: "child_exit",
    207: "cache_corruption",
    209: "network_disconnect",
    211: "invalid_advice",
    213: "persistence_once",
    215: "child_exit",
    217: "network_timeout",
    219: "invalid_json",
    221: "network_disconnect",
    223: "cache_corruption",
    225: "child_exit",
    227: "invalid_advice",
    229: "persistence_once",
    231: "network_timeout",
    233: "network_disconnect",
    235: "child_exit",
    237: "invalid_json",
    239: "cache_corruption",
    241: "network_timeout",
    243: "invalid_advice",
    245: "child_exit",
    247: "network_disconnect",
    249: "persistence_once",
}


def registration_profile() -> dict[str, object]:
    return {
        "user_id": "stability-user-001",
        "profile_id": "cross-domain-professional",
        "age_range": "30-39",
        "occupation": "科技公司法务与管理人员",
        "decision_role": "中高层决策支持",
        "strong_domains": ["corporate-law", "startup-finance"],
        "new_domains": ["agent-systems", "software-engineering", "machine-learning"],
        "response_preferences": ["结论和框架优先", "突出风险和行动项", "技术术语需要解释"],
        "priority": "conversation_over_registration",
    }


def _question(topic: str, phase: int, depth: str, intent: str) -> str:
    if depth == "simple":
        return (
            topic + "\u8bf7\u7b80\u5355\u89e3\u91ca, \u4e0d\u8981\u6df1\u5165; "
            "\u7ed9\u4e00\u4e2a\u751f\u6d3b\u5316\u4f8b\u5b50."
        )
    if depth == "deep":
        return (
            "\u8bf7\u6df1\u5165\u5206\u6790"
            + topic
            + f"\u7684\u539f\u7406\u3001\u8fb9\u754c\u3001\u53cd\u4f8b\u548c\u843d\u5730\u98ce\u9669, "
            f"\u6211\u719f\u6089\u57fa\u7840\u6982\u5ff5, \u76ee\u6807\u662f{intent}."
        )
    return (
        "\u8bf7\u7ed9\u51fa"
        + topic
        + f"\u7684\u6846\u67b6\u3001\u5173\u952e\u6982\u5ff5\u548c\u9002\u7528\u573a\u666f, "
        f"\u5f53\u524d\u9636\u6bb5\u662f{phase}, \u76ee\u6807\u662f{intent}."
    )


def build_turns() -> list[StabilityTurn]:
    turns: list[StabilityTurn] = []
    for stage, (domain, topic) in enumerate(_DOMAINS):
        for offset in range(25):
            index = stage * 25 + offset + 1
            if offset < 8:
                depth = "balanced"
            elif offset < 14:
                depth = "deep"
            elif offset < 18:
                depth = "simple"
            else:
                depth = "deep"
            intent = ("explain", "compare", "decide", "execute", "plan")[offset % 5]
            duplicate = offset in {6, 16}
            question = _question(topic, offset + 1, depth, intent)
            if duplicate and turns:
                question = turns[-1].question
            turns.append(
                StabilityTurn(
                    index=index,
                    event_id=f"deterministic-{index:03d}",
                    domain=domain,
                    topic=topic,
                    depth=depth,
                    intent=intent,
                    question=question,
                    fault=_FAULTS.get(index, "none"),
                    duplicate=duplicate,
                )
            )
    return turns
