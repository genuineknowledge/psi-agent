"""Topic-aware learner profile engine for the Haitun learning coach."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from datetime import UTC, datetime
from typing import Any, TypedDict

import _background_process_registry as _bg
import anyio
import yaml

logger = logging.getLogger(__name__)

PROFILE_SLUG = "_profile"
GLOBAL_TOPIC_KEY = "__global__"
DEFAULT_DIMENSIONS = {"depth": 0.5, "goal": 0.5, "familiarity": 0.5}
EMA_ALPHA_LONG = 0.35
EMA_ALPHA_SHORT = 0.8
TOPIC_MERGE_SIMILARITY = 0.4
SHORT_TERM_WINDOW = 3  # 用于自适应权重计算的最近轮次数

_SIGNALS = {
    "depth_high": r"(为什么|原理|底层|源码|机制|证明|推导|详细|深入|内部|实现细节|数学|公式)",
    "depth_low": r"(简单|大致|概览|框架|一句话|就行|够了|别说太细|不用深入|简短)",
    "goal_decision": r"(选型|项目|投资|公司|产品|决策|风险|对比|哪个更好|成本|落地|生产环境|业务)",
    "goal_interest": r"(好奇|了解一下|想了解|业余|随便|兴趣|好玩|探索|是什么)",
    "familiarity_high": r"(我知道|我懂|我理解|我之前用过|我的背景|我来自|资深|实践过|实现过|做过|写过|用过)",
    "familiarity_low": r"(我不懂|没学过|新手|小白|这是什么|是什么|通俗|打个比方|零基础|完全没接触)",
    # 隐式熟悉度: 用户展示出独立解决问题的能力
    "implicit_familiarity": r"(SELECT .* FROM|我写出来了|我试过了|我跑了|结果是|我查了|我找到了)",
}

# 无意义停用词
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
    "那",
    "这个",
    "继续",
    "举例",
    "再说",
    "还有",
}

_FOLLOWUP_RE = re.compile(
    r"^(那|那么|它|这个|继续|再说|还有|不用|简单|详细|举例|为什么|怎么|讲短|讲长|换个|"
    r"我刚才|刚才的问题|换一种说法)"
)


class TopicProfile(TypedDict):
    label: str
    keywords: list[str]
    dimensions: dict[str, float]  # long-term
    short_term: dict[str, float]  # short-term (recent turns)
    turns: int
    last_seen: str
    signals: dict[str, int]
    recent_signals: list[dict[str, int]]  # 最近几轮的信号计数, 用于自适应权重


def _safe_profile_id(profile_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", profile_id.strip()).strip(".-")
    return safe or "default"


def _profile_path(workspace: anyio.Path, profile_id: str) -> anyio.Path:
    return workspace / "wiki" / "profiles" / f"{_safe_profile_id(profile_id)}.md"


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


def _finite_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except TypeError, ValueError:
        return default
    return _clamp(number) if math.isfinite(number) else default


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except TypeError, ValueError, OverflowError:
        return 0


def _ema(current: float, target: float, alpha: float) -> float:
    return _clamp(current + alpha * (target - current))


def _extract_keywords(text: str) -> list[str]:
    found: list[str] = []
    # 英文技术词
    eng_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_+#.-]{1,30}", text)
    for token in eng_tokens:
        token = token.strip(",.!?:;() ")
        if token and token.lower() not in _STOP_WORDS:
            found.append(token)
    # 中文短语
    zh_tokens = re.findall(r"[\u4e00-\u9fff]{2,8}", text)
    for token in zh_tokens:
        if token not in _STOP_WORDS and token not in found:
            found.append(token)
    # 去重
    clean = []
    seen = set()
    for w in found:
        lw = w.lower()
        if lw not in seen:
            seen.add(lw)
            clean.append(w)
    return clean[:10]


def _topic_jaccard(k1: list[str], k2: list[str]) -> float:
    s1 = {w.lower() for w in k1}
    s2 = {w.lower() for w in k2}
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


class UserProfile:
    def __init__(self, workspace: anyio.Path, profile_id: str = "default"):
        self.workspace = workspace
        self.profile_id = _safe_profile_id(profile_id)
        self.topics: dict[str, TopicProfile] = {}
        self.last_topic_key: str = ""
        self._lock = anyio.Lock()

    async def load(self) -> None:
        path = _profile_path(self.workspace, self.profile_id)
        legacy_path = self.workspace / "wiki" / f"{PROFILE_SLUG}.md"
        if not await path.exists() and self.profile_id == "default" and await legacy_path.exists():
            path = legacy_path
        if not await path.exists():
            return
        try:
            meta = _parse_page(await path.read_text(encoding="utf-8"))
        except OSError:
            return

        raw_topics = meta.get("topics")
        if not isinstance(raw_topics, dict) and isinstance(meta.get("history"), list):
            for row in meta["history"]:
                if isinstance(row, dict) and row.get("role") == "user" and isinstance(row.get("text"), str):
                    self.update(row["text"], "")
            return
        if not isinstance(raw_topics, dict):
            return
        for key, raw in raw_topics.items():
            if not isinstance(key, str) or not isinstance(raw, dict):
                logger.warning("Skipping malformed profile topic %r", key)
                continue
            dims_raw = raw.get("dimensions", {})
            short_raw = raw.get("short_term", {})
            dimensions = {
                name: _finite_float(dims_raw.get(name), default) if isinstance(dims_raw, dict) else default
                for name, default in DEFAULT_DIMENSIONS.items()
            }
            self.topics[key] = TopicProfile(
                label=str(raw.get("label", key)),
                keywords=[str(item) for item in raw.get("keywords", []) if isinstance(item, str)]
                if isinstance(raw.get("keywords"), list)
                else [],
                dimensions=dimensions,
                short_term={name: _finite_float(short_raw.get(name), dimensions[name]) for name in DEFAULT_DIMENSIONS}
                if isinstance(short_raw, dict)
                else dict(dimensions),
                turns=_nonnegative_int(raw.get("turns", 0)),
                last_seen=str(raw.get("last_seen", "")),
                signals={
                    sn: int(sc)
                    for sn, sc in raw.get("signals", {}).items()
                    if isinstance(sn, str) and isinstance(sc, int)
                }
                if isinstance(raw.get("signals"), dict)
                else {},
                recent_signals=[
                    {sn: int(sc) for sn, sc in rd.items() if isinstance(sn, str) and isinstance(sc, int)}
                    for rd in raw.get("recent_signals", [])
                    if isinstance(rd, dict)
                ]
                if isinstance(raw.get("recent_signals"), list)
                else [],
            )
        if self.topics:
            self._auto_merge_topics()
            normal_keys = [key for key in self.topics if key != GLOBAL_TOPIC_KEY]
            if normal_keys:
                self.last_topic_key = max(normal_keys, key=lambda key: self.topics[key]["last_seen"])

    async def save(self) -> None:
        self._auto_merge_topics()
        self._update_global_profile()

        ordered = dict(sorted(self.topics.items(), key=lambda item: item[1]["last_seen"], reverse=True))
        meta = {
            "title": "用户学习画像",
            "slug": PROFILE_SLUG,
            "profile_id": self.profile_id,
            "version": 2,
            "topics": ordered,
        }
        sections = ["# 按知识点划分的学习画像"]
        for topic in ordered.values():
            eff = self.effective_dimensions(topic)
            sections.extend(
                [
                    "",
                    f"## {topic['label']}",
                    f"- 关键词: {', '.join(topic['keywords'][:8])}",
                    f"- 对话轮次: {topic['turns']}",
                    f"- 深度(长/短/有效): {topic['dimensions']['depth']:.2f}/"
                    f"{topic['short_term']['depth']:.2f}/{eff['depth']:.2f}",
                    f"- 目标(长/短/有效): {topic['dimensions']['goal']:.2f}/"
                    f"{topic['short_term']['goal']:.2f}/{eff['goal']:.2f}",
                    f"- 熟悉度(长/短/有效): {topic['dimensions']['familiarity']:.2f}/"
                    f"{topic['short_term']['familiarity']:.2f}/{eff['familiarity']:.2f}",
                    f"- 回答策略: {self.teaching_hint(topic)}",
                ]
            )
        body = "\n".join(sections)
        page = f"---\n{yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)}---\n\n{body}\n"
        path = _profile_path(self.workspace, self.profile_id)
        await path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        await temporary.write_text(page, encoding="utf-8")
        await anyio.to_thread.run_sync(os.replace, str(temporary), str(path))

    async def record_turn(self, user_msg: str, agent_msg: str) -> str:
        async with self._lock:
            key = self.update(user_msg, agent_msg)
            await self.save()
            return key

    def _update_global_profile(self) -> None:
        normal_topics = [(k, t) for k, t in self.topics.items() if k != GLOBAL_TOPIC_KEY]
        if not normal_topics:
            return
        total_turns = sum(t["turns"] for _, t in normal_topics)
        if total_turns == 0:
            return
        global_dims = dict(DEFAULT_DIMENSIONS)
        for dim in DEFAULT_DIMENSIONS:
            weighted = sum(t["dimensions"][dim] * t["turns"] for _, t in normal_topics)
            global_dims[dim] = weighted / total_turns
        self.topics[GLOBAL_TOPIC_KEY] = TopicProfile(
            label="全局画像",
            keywords=[],
            dimensions=global_dims,
            short_term=global_dims,
            turns=total_turns,
            last_seen=datetime.now(UTC).isoformat(),
            signals={},
            recent_signals=[],
        )

    def _find_best_topic_key(self, keywords: list[str]) -> str:
        best_key = ""
        best_score = 0
        for key, topic in self.topics.items():
            if key == GLOBAL_TOPIC_KEY:
                continue
            overlap = len({w.lower() for w in keywords} & {w.lower() for w in topic["keywords"]})
            if overlap > best_score:
                best_score = overlap
                best_key = key
        return best_key if best_score >= 1 else ""

    def resolve_topic(self, user_msg: str) -> str:
        keywords = _extract_keywords(user_msg)
        if self.last_topic_key and _FOLLOWUP_RE.match(user_msg.strip()):
            return self.last_topic_key
        existing = self._find_best_topic_key(keywords)
        if existing:
            return existing
        label = keywords[0] if keywords else "通用交流"
        slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", label.lower()).strip("-")
        if not slug:
            slug = "general"
        count = 1
        while slug in self.topics:
            slug = f"{slug}-{count}"
            count += 1
        return slug

    def get_or_create_topic(self, key: str, keywords: list[str]) -> TopicProfile:
        if key not in self.topics:
            global_profile = self.topics.get(GLOBAL_TOPIC_KEY)
            init_dims = dict(DEFAULT_DIMENSIONS)
            if global_profile:
                for dim in DEFAULT_DIMENSIONS:
                    init_dims[dim] = global_profile["dimensions"][dim] * 0.7 + 0.5 * 0.3
            self.topics[key] = TopicProfile(
                label=keywords[0] if keywords else key,
                keywords=keywords,
                dimensions=init_dims,
                short_term=dict(init_dims),
                turns=0,
                last_seen="",
                signals={},
                recent_signals=[],
            )
        return self.topics[key]

    def get_topic(self, user_msg: str) -> tuple[str, TopicProfile]:
        key = self.resolve_topic(user_msg)
        keywords = _extract_keywords(user_msg)
        topic = self.get_or_create_topic(key, keywords)
        return key, topic

    def update(self, user_msg: str, _agent_msg: str) -> str:
        key, topic = self.get_topic(user_msg)
        long_dims = topic["dimensions"]
        short_dims = topic["short_term"]
        signals = topic["signals"]

        targets = {
            "depth": ("depth_high", 0.9, "depth_low", 0.2),
            "goal": ("goal_decision", 0.9, "goal_interest", 0.25),
            "familiarity": ("familiarity_high", 0.9, "familiarity_low", 0.2),
        }

        current_signals: dict[str, int] = {}
        for dim, (high_name, high_target, low_name, low_target) in targets.items():
            if re.search(_SIGNALS[low_name], user_msg, re.IGNORECASE):
                long_dims[dim] = _ema(long_dims[dim], low_target, EMA_ALPHA_LONG)
                short_dims[dim] = _ema(short_dims[dim], low_target, EMA_ALPHA_SHORT)
                signals[low_name] = signals.get(low_name, 0) + 1
                current_signals[low_name] = 1
            elif re.search(_SIGNALS[high_name], user_msg, re.IGNORECASE):
                long_dims[dim] = _ema(long_dims[dim], high_target, EMA_ALPHA_LONG)
                short_dims[dim] = _ema(short_dims[dim], high_target, EMA_ALPHA_SHORT)
                signals[high_name] = signals.get(high_name, 0) + 1
                current_signals[high_name] = 1

        # 隐式熟悉度: 如果用户展现了独立解决问题的能力, 提高熟悉度
        if re.search(_SIGNALS["implicit_familiarity"], user_msg, re.IGNORECASE):
            long_dims["familiarity"] = _ema(long_dims["familiarity"], 0.9, EMA_ALPHA_LONG)
            short_dims["familiarity"] = _ema(short_dims["familiarity"], 0.9, EMA_ALPHA_SHORT)
            signals["implicit_familiarity"] = signals.get("implicit_familiarity", 0) + 1
            current_signals["implicit_familiarity"] = 1

        # 保持最近信号记录, 用于自适应权重
        topic["recent_signals"].append(current_signals)
        if len(topic["recent_signals"]) > SHORT_TERM_WINDOW:
            topic["recent_signals"].pop(0)

        # 扩充关键词
        for kw in _extract_keywords(user_msg):
            if kw not in topic["keywords"]:
                topic["keywords"].append(kw)
        topic["keywords"] = topic["keywords"][:12]
        topic["turns"] += 1
        topic["last_seen"] = datetime.now(UTC).isoformat()
        self.last_topic_key = key
        return key

    def _auto_merge_topics(self) -> None:
        keys = [k for k in self.topics if k != GLOBAL_TOPIC_KEY]
        merged = set()
        new_topics: dict[str, TopicProfile] = {}
        for i, k1 in enumerate(keys):
            if k1 in merged:
                continue
            t1 = self.topics[k1]
            for j in range(i + 1, len(keys)):
                k2 = keys[j]
                if k2 in merged:
                    continue
                t2 = self.topics[k2]
                sim = _topic_jaccard(t1["keywords"], t2["keywords"])
                if sim >= TOPIC_MERGE_SIMILARITY:
                    total = t1["turns"] + t2["turns"]
                    for dim in DEFAULT_DIMENSIONS:
                        if total:
                            t1["dimensions"][dim] = (
                                t1["dimensions"][dim] * t1["turns"] + t2["dimensions"][dim] * t2["turns"]
                            ) / total
                            t1["short_term"][dim] = (
                                t1["short_term"][dim] * t1["turns"] + t2["short_term"][dim] * t2["turns"]
                            ) / total
                        else:
                            t1["dimensions"][dim] = (t1["dimensions"][dim] + t2["dimensions"][dim]) / 2
                            t1["short_term"][dim] = (t1["short_term"][dim] + t2["short_term"][dim]) / 2
                    t1["turns"] = total
                    t1["keywords"] = list(dict.fromkeys(t1["keywords"] + t2["keywords"]))[:12]
                    t1["last_seen"] = max(t1["last_seen"], t2["last_seen"])
                    for sig, cnt in t2["signals"].items():
                        t1["signals"][sig] = t1["signals"].get(sig, 0) + cnt
                    t1["recent_signals"].extend(t2["recent_signals"])
                    t1["recent_signals"] = t1["recent_signals"][-SHORT_TERM_WINDOW:]
                    merged.add(k2)
            new_topics[k1] = t1
        if GLOBAL_TOPIC_KEY in self.topics:
            new_topics[GLOBAL_TOPIC_KEY] = self.topics[GLOBAL_TOPIC_KEY]
        self.topics = new_topics

    def _signal_volatility(self, topic: TopicProfile, dimension: str) -> float:
        """计算最近信号的波动程度 (0~1), 用于自适应调整短期权重。"""
        recent = topic["recent_signals"]
        if len(recent) < 2:
            return 0.0
        changes = 0
        for i in range(1, len(recent)):
            prev = recent[i - 1].get(f"{dimension}_high", 0) or recent[i - 1].get(f"{dimension}_low", 0)
            curr = recent[i].get(f"{dimension}_high", 0) or recent[i].get(f"{dimension}_low", 0)
            if prev != curr:
                changes += 1
        return changes / (len(recent) - 1)  # 0 = 无变化, 1 = 每次都变

    def effective_dimensions(self, topic: TopicProfile) -> dict[str, float]:
        """自适应融合长期和短期画像。"""
        turns = topic["turns"]
        base_short_weight = max(0.0, 1.0 - turns * 0.15)  # 基础短期权重
        result = {}
        for dim in DEFAULT_DIMENSIONS:
            volatility = self._signal_volatility(topic, dim)
            # 如果最近几轮信号频繁变动, 则增加短期权重 (上限 0.9)
            adjusted_short = min(0.9, base_short_weight + volatility * 0.3)
            long_weight = 1.0 - adjusted_short
            result[dim] = topic["dimensions"][dim] * long_weight + topic["short_term"][dim] * adjusted_short
        return result

    def teaching_hint(self, topic: TopicProfile) -> str:
        eff = self.effective_dimensions(topic)
        hints: list[str] = []
        if eff["depth"] < 0.45:
            hints.append("先给一句话结论和类比, 控制细节数量")
        elif eff["depth"] > 0.65:
            hints.append("深入原理、推导、边界条件和实现细节")
        else:
            hints.append("先给框架, 再提供可选的深入层次")
        if eff["goal"] > 0.65:
            hints.append("突出风险、成本、对比、适用场景和落地建议")
        elif eff["goal"] < 0.4:
            hints.append("以理解概念和激发兴趣为主, 避免过早进入选型")
        if eff["familiarity"] < 0.4:
            hints.append("解释术语并使用生活化例子, 不预设背景知识")
        elif eff["familiarity"] > 0.65:
            hints.append("跳过基础定义, 直接讨论反例、边界和前沿")
        return "; ".join(hints)


_cache: dict[tuple[str, str], UserProfile] = {}


async def get_profile(
    workspace_raw: str = "",
    *,
    profile_id: str = "",
    user_id: str = "",
    session_id: str = "",
) -> UserProfile:
    ws = _bg.resolve_workspace(workspace_raw)
    if profile_id:
        identity = f"profile-{hashlib.sha256(profile_id.encode()).hexdigest()}"
    elif user_id:
        identity = f"user-{hashlib.sha256(user_id.encode()).hexdigest()}"
    elif session_id:
        identity = f"session-{hashlib.sha256(session_id.encode()).hexdigest()}"
    else:
        identity = "default"
    key = (str(ws), _safe_profile_id(identity))
    if key not in _cache:
        _cache[key] = UserProfile(ws, identity)
        await _cache[key].load()
    return _cache[key]
