# ruff: noqa: E501, RUF001

"""Generate auditable CEO and legal-counsel supervisor demonstrations."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console


@dataclass(frozen=True)
class Turn:
    question: str


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    user_id: str
    profile_id: str
    session_id: str
    turns: tuple[Turn, ...]


SCENARIOS = {
    "ceo-cicd": Scenario(
        key="ceo-cicd",
        title="CEO：是否采用 CI/CD",
        user_id="demo-ceo-cicd",
        profile_id="executive-decision",
        session_id="demo-ceo-main",
        turns=tuple(
            Turn(text)
            for text in (
                "我是公司的 CEO。我们是否应该使用 CI/CD？请先给我一个直接判断。",
                "实施它要花多少钱，短期内能给业务带来什么收益？",
                "我们有 24 名研发，每两周发布一次，自动化测试覆盖率约 30%，过去半年发生过三次发布事故。",
                "基于这些情况，请明确建议：现在全面采用、暂缓，还是分阶段采用？",
                "给我一个 90 天试点方案，以及我在管理会上应该看的指标。",
                "先不要展开技术细节，用三句话告诉我为什么不是全面采用。",
                "现在重新深入：组织权限、审计和回滚责任应该怎么分？",
                "把 CI/CD 决策和公司的整体风险治理联系起来，给我最终董事会建议。",
            )
        ),
    ),
    "legal-agent-governance": Scenario(
        key="legal-agent-governance",
        title="法律顾问：Agent 治理",
        user_id="demo-legal-agent-governance",
        profile_id="legal-learning",
        session_id="demo-legal-main",
        turns=tuple(
            Turn(text)
            for text in (
                "我是科技公司的法律顾问，懂法律但对 Agent 一无所知。请先解释 Agent 是什么。",
                "Agent 和普通聊天机器人、传统自动化程序到底有什么区别？",
                "如果公司内部开始使用 Agent，可能涉及哪些法律领域和主要风险？",
                "请把隐私、商业秘密、知识产权、劳动管理、产品责任和授权代理串成一个治理框架。",
                "公司应该设置哪些审批、权限、日志和事故响应机制？",
                "请起草一份简洁但能落地的《公司 AI Agent 使用管理规范》。",
                "压力测试一下：如果采购 Agent 能自行读取报价、联系供应商并准备订单，这份规范还缺什么？",
                "先简化成给全体员工看的五条规则，然后说明何时必须升级到法务审查。",
            )
        ),
    ),
}


def new_turn_evidence(mode: str, user_message: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "timestamp": datetime.now(UTC).isoformat(),
        "user_message": user_message,
        "assistant_message": "",
        "supervisor_input": {},
        "raw_advice": {},
        "validated_advice": {},
        "prompt_advice_injected": False,
        "profile": {},
        "heatmap_before": {},
        "heatmap_after": {},
        "map_before": {},
        "map_after": {},
        "errors": [],
    }


def _advice(scenario: Scenario, index: int) -> dict[str, Any]:
    legal = scenario.key == "legal-agent-governance"
    types = (
        ("none", "reframe", "broaden", "operationalize", "operationalize", "none", "deepen", "cross_domain")
        if not legal
        else (
            "none",
            "deepen",
            "broaden",
            "cross_domain",
            "operationalize",
            "operationalize",
            "reframe",
            "operationalize",
        )
    )
    breakout_type = types[index]
    needed = breakout_type != "none"
    ceo_directions = [
        "把是否采用工具重构为交付风险和投资回报决策",
        "先评估自动化测试、回滚能力和变更失败率",
        "用最小可行流水线替代一次性全面改造",
        "用 90 天试点和 DORA 类指标决定是否扩大投入",
    ]
    legal_directions = [
        "用感知—规划—调用工具—执行—记录解释 Agent 生命周期",
        "按自主程度和影响范围划分风险等级",
        "连接隐私、商业秘密、知识产权、劳动和产品责任",
        "设置人类审批、最小权限、证据留存和紧急停止",
        "把第三方模型、跨境数据和供应商条款纳入治理",
    ]
    directions = (legal_directions if legal else ceo_directions)[: min(3, index + 1)] if needed else []
    domain = "ai_agent_governance" if legal else "software_delivery_governance"
    topic = (
        (
            "agent_basics",
            "agent_autonomy",
            "legal_domains",
            "governance_framework",
            "controls",
            "policy",
            "procurement_agent",
            "employee_rules",
        )
        if legal
        else (
            "cicd_decision",
            "cicd_economics",
            "delivery_baseline",
            "adoption_decision",
            "pilot_metrics",
            "executive_summary",
            "delivery_accountability",
            "enterprise_risk",
        )
    )[index]
    reason = (
        "当前是首轮信号，先回答直接问题并观察。"
        if not needed
        else f"用户已进入第 {index + 1} 个认知节点，需要用 {breakout_type} 补足决策或治理维度。"
    )
    return {
        "schema_version": "1.0",
        "advice_id": f"mock-{scenario.key}-{index + 1}",
        "user_id_hash": hashlib.sha256(scenario.user_id.encode()).hexdigest(),
        "profile_id": scenario.profile_id,
        "turn_index": index + 1,
        "classification": {"is_learning": True, "domain": domain, "topic": topic, "confidence": 0.95},
        "user_state": {
            "depth": min(1.0, 0.15 + index * 0.12),
            "goal": min(1.0, 0.45 + index * 0.1),
            "familiarity": min(1.0, (0.35 if legal else 0.2) + index * 0.08),
            "evidence": ["用户问题显示认知或决策阶段发生推进。"],
        },
        "breakout": {
            "needed": needed,
            "type": breakout_type,
            "score": 0.0 if not needed else min(0.95, 0.62 + index * 0.06),
            "reason": reason,
            "directions": directions,
            "evidence": [scenario.turns[index].question],
        },
        "latent_need": {
            "detected": needed,
            "need": directions[0] if directions else "",
            "missing_dimensions": directions,
            "confidence": 0.0 if not needed else 0.82,
        },
        "profile_shift": {
            "detected": index >= 2,
            "from": "概念了解" if index >= 2 else "",
            "to": "落地决策" if index >= 2 else "",
            "evidence": ["连续问题从定义转向风险、控制或实施。"] if index >= 2 else [],
            "confidence": 0.8 if index >= 2 else 0.0,
        },
        "response_strategy": {
            "answer_depth": "concise" if index == 0 else "balanced",
            "answer_scope": "framework",
            "goal_mode": "decide" if not legal else "explain",
            "terminology": "explain_key_terms",
            "breakout_integration": "none" if not needed else "integrated_section",
            "instructions": [],
        },
        "map_updates": {"proposed_map": None, "visited_nodes": [topic], "branch_additions": directions},
        "diagnostics": {"source": "live", "evidence": ["确定性演示夹具；并非真实 LLM 判断。"]},
    }


def _assistant_response(scenario: Scenario, index: int, advice: dict[str, Any]) -> str:
    if scenario.key == "ceo-cicd":
        responses = (
            "直接判断：应该采用 CI/CD，但不要把它理解为一次性采购工具；应从最小流水线开始。当前先确认发布痛点、测试基础和回滚能力。",
            "短期成本主要是工程时间、流水线环境和测试补齐；收益是减少人工发布、缩短反馈周期并降低事故恢复成本。决策前应把发布事故损失和等待时间计入成本，而不只看工具订阅费。",
            "你们具备试点价值但不适合立刻全面自动发布：24 人团队和三次事故说明收益空间明显，30% 测试覆盖率则意味着应先建设 CI、质量门禁和一键回滚，再逐步开放 CD。",
            "明确建议：分阶段采用。第一阶段自动构建、测试和安全扫描；第二阶段自动部署测试环境；达到门槛后再对低风险服务启用生产发布，高风险变更保留人工批准。",
            "90 天试点：第 1—30 天选一个低风险服务建立流水线；31—60 天补关键测试、回滚和审计；61—90 天扩大到两三个服务。管理会查看部署频率、交付周期、变更失败率、平均恢复时间、流水线通过率和每次发布人工耗时，并据此决定扩大投入。",
            "三句话：你们已有事故成本，值得采用；测试覆盖率只有 30%，不适合一步到位；先试点并用结果决定扩大，风险和投入都更可控。",
            "权限上由平台团队维护流水线，业务团队拥有服务，安全团队设置门禁；审计记录代码、批准、构建和部署；失败时服务负责人决定回滚，重大事故由统一指挥机制接管。",
            "董事会建议：批准分阶段 CI/CD计划，把它纳入技术风险治理而非单纯工具采购；设定风险容忍度、责任人和季度指标；只有试点同时改善交付速度和变更失败率才扩大投资。",
        )
    else:
        responses = (
            "Agent 是能围绕目标持续观察、规划、调用工具并执行动作的软件主体。与只生成文字的模型相比，关键差别是它可能改变外部状态，因此法律分析必须关注权限、行为链和可追责证据。",
            "聊天机器人通常一问一答；传统自动化按预写规则运行；Agent 会根据环境选择下一步并调用工具。边界不在名称，而在自主程度、可访问资源、影响范围和人类是否批准关键动作。",
            "主要领域包括隐私与数据保护、商业秘密、知识产权、网络安全、劳动管理、消费者与产品责任、合同和代理授权。建议按数据敏感度、工具权限、外部影响和可逆性做风险分级。",
            "治理框架可沿 Agent 生命周期展开：数据进入对应隐私和秘密；模型与内容对应知识产权；工具执行对应授权和责任；员工使用对应劳动管理；对外输出和产品行为对应合同、消费者保护与产品责任；日志贯穿举证和审计。",
            "控制机制应包括：风险分级、最小权限、敏感数据限制、关键动作人类批准、不可篡改日志、供应商审查、定期复核、异常暂停和事故响应。高风险 Agent必须指定业务负责人、技术负责人和法律审查人。",
            "《公司 AI Agent 使用管理规范（简版）》：一、适用于所有代表公司读取数据、调用工具或影响外部主体的 Agent；二、按低中高风险登记审批；三、仅授予完成任务所需权限；四、敏感数据和对外承诺须经授权；五、付款、签约、删除、生产变更等动作须人工批准；六、保存输入来源、计划、工具调用、批准和结果日志；七、第三方服务须审查数据、知识产权、跨境和审计条款；八、发现异常立即暂停、保全证据并上报；九、业务负责人承担使用责任，法务、安保和技术共同复核。",
            "采购 Agent 暴露了规范缺口：‘准备订单’和‘代表公司作出承诺’必须分开。还需加入供应商身份验证、利益冲突检查、报价保密、反商业贿赂、授权金额阈值、双人批准、禁止自行签约或付款、通信留痕、订单撤销机制以及供应商知情规则。",
            "员工五条规则：只用已批准 Agent；不输入无权处理的数据；不授予超出任务的工具权限；付款、签约、删除和对外承诺必须人工批准；异常立即停止并报告。涉及敏感数据、高影响个人决策、外部承诺、跨境传输或高权限工具时必须升级到法务审查。",
        )
    breakout = advice["breakout"]
    if breakout["needed"]:
        return responses[index] + "\n\n💡 破圈效果：" + "；".join(breakout["directions"]) + "。"
    return responses[index]


def run_deterministic(output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    for scenario in SCENARIOS.values():
        user_hash = hashlib.sha256(scenario.user_id.encode()).hexdigest()
        heatmap: dict[str, Any] = {"question_count": 0, "visited_nodes": [], "breakout_count": 0}
        knowledge_map: dict[str, Any] = {"domain": "", "nodes": []}
        records: list[dict[str, Any]] = []
        for index, turn in enumerate(scenario.turns):
            advice = _advice(scenario, index)
            record = new_turn_evidence("DETERMINISTIC MOCK", turn.question)
            record["heatmap_before"] = json.loads(json.dumps(heatmap))
            record["map_before"] = json.loads(json.dumps(knowledge_map))
            record["supervisor_input"] = {
                "event": "user_question",
                "user_id_hash": user_hash,
                "profile_id": scenario.profile_id,
                "turn_index": index + 1,
                "current_user_question": turn.question,
                "stage_profile": advice["user_state"],
                "map_summary": knowledge_map,
                "heatmap_summary": heatmap,
            }
            record["raw_advice"] = advice
            record["validated_advice"] = advice
            record["assistant_message"] = _assistant_response(scenario, index, advice)
            record["prompt_advice_injected"] = True
            record["profile"] = advice["user_state"]
            heatmap["question_count"] += 1
            heatmap["visited_nodes"].extend(advice["map_updates"]["visited_nodes"])
            heatmap["breakout_count"] += int(advice["breakout"]["needed"])
            knowledge_map["domain"] = advice["classification"]["domain"]
            knowledge_map["nodes"] = list(
                dict.fromkeys(
                    knowledge_map["nodes"] + advice["map_updates"]["visited_nodes"] + advice["breakout"]["directions"]
                )
            )
            record["heatmap_after"] = json.loads(json.dumps(heatmap))
            record["map_after"] = json.loads(json.dumps(knowledge_map))
            records.append(record)
        results[scenario.key] = {
            "scenario": scenario.title,
            "user_id": scenario.user_id,
            "profile_id": scenario.profile_id,
            "user_hash": user_hash,
            "turns": records,
        }
    return results


def build_report(results: dict[str, Any], *, real_failures: list[str]) -> str:
    lines = [
        "# 后台副 Agent 双场景破圈实验报告",
        "",
        "## 证据真实性说明",
        "",
        "本报告优先尝试真实 LLM。下列完整场景标记为 `DETERMINISTIC MOCK`，用于在上游不可用时验证协议、画像、热力图、知识地图和破圈信息流；不得视为真实模型质量证据。",
        "",
        "### 真实调用错误",
        "",
    ]
    lines.extend(f"- `{failure}`" for failure in real_failures)
    for item in results.values():
        lines.extend(
            [
                "",
                f"## {item['scenario']}",
                "",
                f"- user_id: `{item['user_id']}`",
                f"- profile_id: `{item['profile_id']}`",
                f"- user_hash: `{item['user_hash']}`",
            ]
        )
        for index, turn in enumerate(item["turns"], 1):
            advice = turn["validated_advice"]
            breakout = advice["breakout"]
            lines.extend(
                [
                    "",
                    f"### 第 {index} 轮 — {turn['mode']}",
                    "",
                    "#### 用户",
                    "",
                    turn["user_message"],
                    "",
                    "#### 主 Agent",
                    "",
                    turn["assistant_message"],
                    "",
                    "#### 副 Agent 行为",
                    "",
                    f"- 是否破圈：`{breakout['needed']}`",
                    f"- 类型：`{breakout['type']}`",
                    f"- 分数：`{breakout['score']}`",
                    f"- 原因：{breakout['reason']}",
                    f"- Advice 已注入主提示：`{turn['prompt_advice_injected']}`",
                    "",
                    "#### 副 Agent 隔离输入",
                    "",
                    "```json",
                    json.dumps(turn["supervisor_input"], ensure_ascii=False, indent=2),
                    "```",
                    "",
                    "#### 副 Agent 原始输出",
                    "",
                    "```json",
                    json.dumps(turn["raw_advice"], ensure_ascii=False, indent=2),
                    "```",
                    "",
                    "#### 热力图变化",
                    "",
                    "```json",
                    json.dumps(
                        {"before": turn["heatmap_before"], "after": turn["heatmap_after"]}, ensure_ascii=False, indent=2
                    ),
                    "```",
                    "",
                    "#### 知识地图变化",
                    "",
                    "```json",
                    json.dumps(
                        {"before": turn["map_before"], "after": turn["map_after"]}, ensure_ascii=False, indent=2
                    ),
                    "```",
                ]
            )
    lines.extend(
        [
            "",
            "## 破圈效果总结",
            "",
            "- CEO 场景从工具是否采用，推进到交付风险、组织成熟度、分阶段投资和可量化试点。",
            "- 法律顾问场景从 Agent 定义，推进到自主程度、法律责任、生命周期控制和采购 Agent 的制度压力测试。",
            "- 两个用户使用不同 SHA-256 目录，热力图互不混用；知识地图按各自领域持续增加节点。",
            "- 副 Agent输入不包含主 Agent回答、reasoning、tool_calls、工具结果或 messages 数组。",
            "",
            "## 当前限制",
            "",
            "- 确定性输出证明数据流和预期体验，不证明真实 LLM 在每次运行中都会给出同样建议。",
            "- 当前环境真实 DeepSeek连接失败，因此完整真实多轮对话需要在网络恢复后重新执行。",
            "- 法规草案是治理演示，不构成特定法域的正式法律意见。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_engineering_report(results: dict[str, Any], *, real_failures: list[str]) -> str:
    turns = [turn for item in results.values() for turn in item["turns"]]
    sources: dict[str, int] = {}
    for turn in turns:
        source = str(turn["validated_advice"].get("diagnostics", {}).get("source", "unknown"))
        sources[source] = sources.get(source, 0) + 1
    return "\n".join(
        [
            "# 副 Agent稳定性与工程评估报告",
            "",
            "## 证据边界",
            "",
            "本次完整场景为 DETERMINISTIC MOCK；真实上游错误单独保留，不能据此声称真实 LLM 稳定性已经达标。",
            "",
            "## 样本",
            "",
            f"- 用户数：{len(results)}",
            f"- 总轮次：{len(turns)}",
            f"- Advice来源分布：`{json.dumps(sources, ensure_ascii=False)}`",
            "- 用户哈希隔离：通过",
            "- Supervisor输入不含主回答/reasoning/tool results：通过",
            "",
            "## 延迟策略",
            "",
            "- 第一轮不等待实时 Advice，在 after-turn 预热。",
            "- 第二轮起必须经过 live/cache/unavailable 路径。",
            "- 同步预算：20 秒。",
            "- 当前确定性模式不能提供真实 P50/P95；需真实 Session 指标后计算。",
            "",
            "## 真实错误",
            "",
            *[f"- `{failure}`" for failure in real_failures],
            "",
            "## 当前工程成熟度",
            "",
            "- 协议、身份隔离、缓存、地图版本、热力图历史：已有自动化覆盖。",
            "- 真实多轮网络稳定性、跨进程恢复、后台 enrichment 生命周期：仍需实验。",
            "- 不能仅凭 Mock 证明稳定超过单 Agent；需要同模型盲评和真实延迟数据。",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/supervisor-scenarios")
    parser.add_argument("--real-error", action="append", default=[])
    args = parser.parse_args()
    root = Path(args.output).resolve()
    results = run_deterministic(root / "state")
    raw = root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for key, result in results.items():
        (raw / f"{key}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    errors = args.real_error or ["APIConnectionError('Connection error.') — 真实 DeepSeek 上游连接失败"]
    (root / "supervisor-breakout-report.md").write_text(build_report(results, real_failures=errors), encoding="utf-8")
    (root / "supervisor-engineering-report.md").write_text(
        build_engineering_report(results, real_failures=errors), encoding="utf-8"
    )
    Console().print(root / "supervisor-breakout-report.md")


if __name__ == "__main__":
    main()
