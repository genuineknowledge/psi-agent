"""Topic-aware learner profile engine for the Haitun learning coach."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, TypedDict

import _background_process_registry as _bg
import anyio
import yaml

PROFILE_SLUG = "_profile"
DEFAULT_DIMENSIONS = {"depth": 0.5, "goal": 0.5, "familiarity": 0.5}
EMA_ALPHA = 0.35

_SIGNALS = {
    "depth_high": r"(为什么|原理|底层|源码|机制|证明|推导|详细|深入|内部|实现细节|数学)",
    "depth_low": r"(简单|大致|概览|框架|一句话|就行|够了|别说太细|不用深入|简短)",
    "goal_decision": r"(选型|项目|投资|公司|产品|决策|风险|对比|哪个更好|成本|落地|生产环境)",
    "goal_interest": r"(好奇|了解一下|想了解|业余|随便|兴趣|好玩|探索|是什么)",
    "familiarity_high": r"(我知道|我懂|我理解|我之前用过|我的背景|我来自|资深|实践过|实现过)",
    "familiarity_low": r"(我不懂|没学过|新手|小白|这是什么|是什么|通俗|打个比方|零基础)",
}

_KNOWN_TOPICS = (
    "过拟合",
    "欠拟合",
    "机器学习",
    "深度学习",
    "神经网络",
    "大语言模型",
    "提示词工程",
    "Python",
    "JavaScript",
    "TypeScript",
    "数据库",
    "操作系统",
    "计算机网络",
    "量子计算",
    "投资",
    "产品设计",
)

_STOP_WORDS = {
    "什么",
    "怎么",
    "如何",
    "为什么",
    "一下",
    "可以",
    "需要",
    "问题",
    "区别",
    "简单",
    "详细",
    "了解",
    "想了解",
    "不用",
    "深入",
    "它和",
    "不用太深入",
    "简单说就行",
}

_FOLLOWUP = re.compile(r"^(那|那么|它|这个|继续|再说|还有|不用|简单|详细|举例|为什么|怎么)")


class TopicProfile(TypedDict):
    label: str
    keywords: list[str]
    dimensions: dict[str, float]
    turns: int
    last_seen: str
    signals: dict[str, int]


def _profile_path(workspace: anyio.Path) -> anyio.Path:
    return workspace / "wiki" / f"{PROFILE_SLUG}.md"


def _parse_page(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _ema(current: float, target: float) -> float:
    return _clamp(current + EMA_ALPHA * (target - current))


def _extract_keywords(text: str) -> list[str]:
    found: list[str] = []
    lowered = text.lower()
    for topic in _KNOWN_TOPICS:
        if topic.lower() in lowered:
            found.append(topic)
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_+#.-]{1,30}|[\u4e00-\u9fff]{2,12}", text)
    for token in tokens:
        cleaned = token.strip(",.!?:;() ")
        if cleaned and cleaned not in _STOP_WORDS and cleaned not in found:
            found.append(cleaned)
    return found[:8]


class UserProfile:
    def __init__(self, workspace: anyio.Path):
        self.workspace = workspace
        self.topics: dict[str, TopicProfile] = {}
        self.last_topic_key = ""

    async def load(self) -> None:
        path = _profile_path(self.workspace)
        if not await path.exists():
            return
        try:
            meta = _parse_page(await path.read_text(encoding="utf-8"))
        except OSError:
            return

        raw_topics = meta.get("topics")
        if isinstance(raw_topics, dict):
            for key, raw in raw_topics.items():
                if not isinstance(key, str) or not isinstance(raw, dict):
                    continue
                dimensions = raw.get("dimensions", {})
                self.topics[key] = TopicProfile(
                    label=str(raw.get("label", key)),
                    keywords=[str(item) for item in raw.get("keywords", []) if isinstance(item, str)],
                    dimensions={
                        name: _clamp(float(dimensions.get(name, default)))
                        for name, default in DEFAULT_DIMENSIONS.items()
                    }
                    if isinstance(dimensions, dict)
                    else dict(DEFAULT_DIMENSIONS),
                    turns=max(0, int(raw.get("turns", 0))),
                    last_seen=str(raw.get("last_seen", "")),
                    signals={
                        name: int(count)
                        for name, count in raw.get("signals", {}).items()
                        if isinstance(name, str) and isinstance(count, int)
                    }
                    if isinstance(raw.get("signals"), dict)
                    else {},
                )
            if self.topics:
                self._normalize_topic_keys()
                self.last_topic_key = max(self.topics, key=lambda item: self.topics[item]["last_seen"])
            return

        # One-time migration from the original global profile + raw history.
        history = meta.get("history")
        if isinstance(history, list):
            user_text = ""
            for item in history:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                text = item.get("text")
                if not isinstance(text, str):
                    continue
                if role == "user":
                    user_text = text
                elif role == "agent" and user_text:
                    self.update(user_text, text)
                    user_text = ""

    def resolve_topic(self, user_msg: str) -> str:
        keywords = _extract_keywords(user_msg)
        known_matches = [topic for topic in _KNOWN_TOPICS if topic.lower() in user_msg.lower()]
        if known_matches:
            known_key = known_matches[0].lower()
            if known_key in self.topics:
                return known_key
            return known_key
        if self.topics and keywords:
            best_key = ""
            best_score = 0
            lowered = user_msg.lower()
            for key, topic in self.topics.items():
                score = sum(2 for word in topic["keywords"] if word.lower() in lowered)
                score += sum(1 for word in keywords if word in topic["keywords"])
                if score > best_score:
                    best_key = key
                    best_score = score
            if best_key:
                return best_key

        if self.last_topic_key and (not known_matches and (_FOLLOWUP.match(user_msg.strip()) or not keywords)):
            return self.last_topic_key

        label = keywords[0] if keywords else "通用交流"
        slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", label.lower()).strip("-")
        return slug or "general"

    def _normalize_topic_keys(self) -> None:
        normalized: dict[str, TopicProfile] = {}
        for key, topic in self.topics.items():
            known = next(
                (
                    candidate
                    for candidate in _KNOWN_TOPICS
                    if candidate in topic["keywords"] or candidate == topic["label"]
                ),
                None,
            )
            target_key = known.lower() if known else key
            if known:
                topic["label"] = known
            existing = normalized.get(target_key)
            if existing is None:
                normalized[target_key] = topic
                continue
            total_turns = existing["turns"] + topic["turns"]
            for name in DEFAULT_DIMENSIONS:
                weighted = existing["dimensions"][name] * existing["turns"] + topic["dimensions"][name] * topic["turns"]
                existing["dimensions"][name] = weighted / total_turns if total_turns else 0.5
            existing["turns"] = total_turns
            existing["keywords"] = list(dict.fromkeys([*existing["keywords"], *topic["keywords"]]))[:12]
            existing["last_seen"] = max(existing["last_seen"], topic["last_seen"])
            for signal, count in topic["signals"].items():
                existing["signals"][signal] = existing["signals"].get(signal, 0) + count
        self.topics = normalized

    def get_topic(self, user_msg: str) -> tuple[str, TopicProfile]:
        key = self.resolve_topic(user_msg)
        if key not in self.topics:
            keywords = _extract_keywords(user_msg)
            self.topics[key] = TopicProfile(
                label=keywords[0] if keywords else "通用交流",
                keywords=keywords,
                dimensions=dict(DEFAULT_DIMENSIONS),
                turns=0,
                last_seen="",
                signals={},
            )
        return key, self.topics[key]

    def update(self, user_msg: str, _agent_msg: str) -> str:
        key, topic = self.get_topic(user_msg)
        dimensions = topic["dimensions"]
        signals = topic["signals"]
        targets = {
            "depth": ("depth_high", 0.9, "depth_low", 0.2),
            "goal": ("goal_decision", 0.9, "goal_interest", 0.25),
            "familiarity": ("familiarity_high", 0.9, "familiarity_low", 0.2),
        }
        for dimension, (high_name, high_target, low_name, low_target) in targets.items():
            if re.search(_SIGNALS[low_name], user_msg, re.IGNORECASE):
                dimensions[dimension] = _ema(dimensions[dimension], low_target)
                signals[low_name] = signals.get(low_name, 0) + 1
            elif re.search(_SIGNALS[high_name], user_msg, re.IGNORECASE):
                dimensions[dimension] = _ema(dimensions[dimension], high_target)
                signals[high_name] = signals.get(high_name, 0) + 1

        for keyword in _extract_keywords(user_msg):
            if keyword not in topic["keywords"]:
                topic["keywords"].append(keyword)
        topic["keywords"] = topic["keywords"][:12]
        topic["turns"] += 1
        topic["last_seen"] = datetime.now(UTC).isoformat()
        self.last_topic_key = key
        return key

    def teaching_hint(self, topic: TopicProfile) -> str:
        dimensions = topic["dimensions"]
        hints: list[str] = []
        if dimensions["depth"] < 0.45:
            hints.append("先给一句话结论和类比, 控制细节数量")
        elif dimensions["depth"] > 0.65:
            hints.append("深入原理、推导、边界条件和实现细节")
        else:
            hints.append("先给框架, 再提供可选的深入层次")
        if dimensions["goal"] > 0.65:
            hints.append("突出风险、成本、对比、适用场景和落地建议")
        elif dimensions["goal"] < 0.4:
            hints.append("以理解概念和激发兴趣为主, 避免过早进入选型")
        if dimensions["familiarity"] < 0.4:
            hints.append("解释术语并使用生活化例子, 不预设背景知识")
        elif dimensions["familiarity"] > 0.65:
            hints.append("跳过基础定义, 直接讨论反例、边界和前沿")
        return "; ".join(hints)

    async def save(self) -> None:
        self._normalize_topic_keys()
        ordered = dict(sorted(self.topics.items(), key=lambda item: item[1]["last_seen"], reverse=True))
        meta = {"title": "用户学习画像", "slug": PROFILE_SLUG, "version": 2, "topics": ordered}
        sections = ["# 按知识点划分的学习画像"]
        for topic in ordered.values():
            dimensions = topic["dimensions"]
            sections.extend(
                [
                    "",
                    f"## {topic['label']}",
                    "",
                    f"- 关键词: {', '.join(topic['keywords'])}",
                    f"- 对话轮次: {topic['turns']}",
                    f"- 深度: {dimensions['depth']:.2f} (框架性←→系统性)",
                    f"- 目标: {dimensions['goal']:.2f} (兴趣←→决策)",
                    f"- 熟悉度: {dimensions['familiarity']:.2f} (新手←→专家)",
                    f"- 回答策略: {self.teaching_hint(topic)}",
                ]
            )
        page = f"---\n{yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)}---\n\n{'\n'.join(sections)}\n"
        path = _profile_path(self.workspace)
        await path.parent.mkdir(parents=True, exist_ok=True)
        await path.write_text(page, encoding="utf-8")


_cache: dict[str, UserProfile] = {}


async def get_profile(workspace_raw: str = "") -> UserProfile:
    ws = _bg.resolve_workspace(workspace_raw)
    key = str(ws)
    if key not in _cache:
        _cache[key] = UserProfile(ws)
        await _cache[key].load()
    return _cache[key]
