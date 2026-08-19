"""Build one deterministic, privacy-conscious recruitment workflow summary."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDENTITY_NUMBER = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
_TECHNICAL_ID = re.compile(
    r"(?i)(?<![a-z0-9_-])(?:[a-f0-9]{16,64}|(?:rec|req|run|tbl|app)[a-z0-9_-]{6,})(?![a-z0-9_-])"
)
_ADDRESS_HINT = re.compile(r"(?:身份证|证件号|手机号|电话|邮箱|精确地址|家庭住址|现住址)")
_MAX_CANDIDATES = 100


def _decode(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _mapping(value: Any) -> dict[str, Any]:
    decoded = _decode(value)
    return {str(key): item for key, item in decoded.items()} if isinstance(decoded, Mapping) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    decoded = _decode(value)
    if not isinstance(decoded, list):
        return []
    return [_mapping(item) for item in decoded if isinstance(item, Mapping)]


def _safe_text(value: Any, *, fallback: str, max_chars: int = 180) -> str:
    if not isinstance(value, str):
        return fallback
    text = " ".join(value.split()).strip()
    if not text:
        return fallback
    if any(pattern.search(text) for pattern in (_EMAIL, _PHONE, _IDENTITY_NUMBER, _TECHNICAL_ID, _ADDRESS_HINT)):
        return fallback
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _safe_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 500:
        return ""
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (
            hostname not in {"feishu.cn", "larksuite.com"}
            and not hostname.endswith((".feishu.cn", ".larksuite.com"))
        )
    ):
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _counts(records: list[dict[str, Any]], failures: list[Any]) -> dict[str, int]:
    has_created_flag = any(isinstance(record.get("created"), bool) for record in records)
    if has_created_flag:
        succeeded = sum(record.get("created") is True for record in records)
        skipped = sum(record.get("created") is False for record in records)
    else:
        succeeded = len(records)
        skipped = 0
    failed = len(failures)
    return {
        "processed": succeeded + skipped + failed,
        "succeeded": succeeded,
        "skipped": skipped,
        "failed": failed,
    }


def _candidate(name: Any, grade: Any, role: Any, advice: Any, reason: Any) -> dict[str, str]:
    return {
        "姓名": _safe_text(name, fallback="候选人(已脱敏)", max_chars=40),
        "评级": _safe_text(grade, fallback="未提供", max_chars=20),
        "匹配岗位": _safe_text(role, fallback="未提供", max_chars=80),
        "面试建议": _safe_text(advice, fallback="未提供", max_chars=40),
        "理由": _safe_text(reason, fallback="详情已脱敏, 请在业务表中查看。"),
    }


def _render(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    state = "已完成" if summary["status"] == "complete" else "已阻断"
    lines = [
        f"Workflow: {summary['workflow_name']}",
        f"状态: {state}",
        (
            f"处理: {counts['processed']}; 成功: {counts['succeeded']}; "
            f"跳过: {counts['skipped']}; 失败: {counts['failed']}"
        ),
    ]
    candidates = summary["candidates"]
    if candidates:
        lines.append("候选人:")
        lines.extend(
            (
                f"- {item['姓名']} | 评级 {item['评级']} | 匹配岗位 {item['匹配岗位']} | "
                f"面试建议 {item['面试建议']} | 理由 {item['理由']}"
            )
            for item in candidates
        )
    else:
        lines.append("候选人: 无可安全展示结果。")
    if summary["resource_link"]:
        lines.append(f"业务资源: {summary['resource_link']}")
    if summary["status"] != "complete":
        lines.append("失败步骤: Workflow 业务结果校验。")
        lines.append("诊断: 上游结果未满足完整性要求, 未生成业务结论。")
    lines.append(f"下一步: {summary['next_action']}")
    return "\n".join(lines)


def _finalize(
    *,
    workflow: str,
    workflow_name: str,
    status: str,
    counts: dict[str, int],
    candidates: list[dict[str, str]],
    resource_link: str,
    next_action: str,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "schema_version": "1.0",
        "workflow": workflow,
        "workflow_name": workflow_name,
        "status": "complete" if status == "complete" and counts["failed"] == 0 else "blocked",
        "counts": counts,
        "candidates": candidates[:_MAX_CANDIDATES],
        "resource_link": resource_link,
        "next_action": next_action,
    }
    summary["text"] = _render(summary)
    return summary


def _resume_approval(inputs: dict[str, Any]) -> dict[str, Any]:
    manifest = _mapping(inputs.get("talent_pool_manifest"))
    validated = _mapping(inputs.get("validated_candidate_assessments"))
    records = _items(manifest.get("records"))
    failures = list(manifest.get("failed_candidates", [])) + list(manifest.get("errors", []))
    candidates = []
    for record in records:
        fingerprint = _mapping(record.get("row_fingerprint"))
        candidates.append(
            _candidate(
                fingerprint.get("姓名"),
                fingerprint.get("评级"),
                fingerprint.get("匹配岗位"),
                fingerprint.get("面试建议"),
                fingerprint.get("面试建议理由"),
            )
        )
    status = "complete" if manifest.get("status") == validated.get("status") == "complete" else "blocked"
    return _finalize(
        workflow="resume-approval",
        workflow_name="简历审批与初审入库",
        status=status,
        counts=_counts(records, failures),
        candidates=candidates,
        resource_link=_safe_url(manifest.get("base_url")),
        next_action="请在候选人才库核对结果, 仅明确设置每位候选人的「初审状态」。",
    )


def _interview_preparation(inputs: dict[str, Any]) -> dict[str, Any]:
    manifest = _mapping(inputs.get("interview_manifest"))
    stage = _mapping(inputs.get("interview_stage_bundle"))
    receipt = _mapping(inputs.get("interview_handoff_receipt"))
    records = _items(manifest.get("records"))
    failures = list(manifest.get("errors", [])) + list(receipt.get("errors", []))
    candidates = []
    for approved in _items(stage.get("approved")):
        assessment = _mapping(approved.get("assessment"))
        candidates.append(
            _candidate(
                assessment.get("candidate_name"),
                assessment.get("grade"),
                assessment.get("matched_role_name"),
                assessment.get("interview_recommendation"),
                assessment.get("interview_recommendation_reason"),
            )
        )
    status = (
        "complete"
        if stage.get("status") == manifest.get("status") == receipt.get("status") == "complete"
        else "blocked"
    )
    destination = _mapping(stage.get("destination"))
    return _finalize(
        workflow="resume-interview-preparation",
        workflow_name="面试准备与记录创建",
        status=status,
        counts=_counts(records, failures),
        candidates=candidates,
        resource_link=_safe_url(destination.get("base_url")),
        next_action="请在面试记录中补充纪要、四项明确评分和面试状态, 不要推断或代填 Human 决策。",
    )


def _interview_conclusion(inputs: dict[str, Any]) -> dict[str, Any]:
    validated = _mapping(inputs.get("validated_hiring_conclusions"))
    decisions = _mapping(inputs.get("final_decisions"))
    receipt = _mapping(inputs.get("result_write_receipt"))
    report = _mapping(inputs.get("report_result"))
    confirmed = _items(decisions.get("confirmed"))
    pending = list(decisions.get("pending", []))
    failures = pending + list(decisions.get("errors", [])) + list(receipt.get("errors", []))
    candidates = []
    for item in confirmed:
        conclusion = _mapping(item.get("conclusion"))
        assessment = _mapping(conclusion.get("assessment"))
        role = _mapping(conclusion.get("matched_role"))
        candidates.append(
            _candidate(
                item.get("candidate_name"),
                assessment.get("grade"),
                role.get("name"),
                item.get("decision"),
                conclusion.get("interview_summary"),
            )
        )
    status = (
        "complete"
        if validated.get("status")
        == decisions.get("status")
        == receipt.get("status")
        == report.get("status")
        == "complete"
        else "blocked"
    )
    resource_link = _safe_url(receipt.get("interview_table_url")) or _safe_url(report.get("document_url"))
    return _finalize(
        workflow="interview-conclusion",
        workflow_name="面试结论与 Human 最终确认",
        status=status,
        counts=_counts(confirmed, failures),
        candidates=candidates,
        resource_link=resource_link,
        next_action="请在面试记录和招聘审计文档中复核最终状态; 如需更改, 请明确指定业务字段和值。",
    )


def run(inputs: dict[str, Any]) -> dict[str, Any]:
    normalized = {str(key): _decode(value) for key, value in inputs.items()}
    if "final_decisions" in normalized:
        return _interview_conclusion(normalized)
    if "interview_manifest" in normalized:
        return _interview_preparation(normalized)
    if "talent_pool_manifest" in normalized:
        return _resume_approval(normalized)
    return _finalize(
        workflow="unknown",
        workflow_name="招聘 Workflow",
        status="blocked",
        counts={"processed": 0, "succeeded": 0, "skipped": 0, "failed": 1},
        candidates=[],
        resource_link="",
        next_action="请检查 Workflow 的最终摘要输入后重试。",
    )


def _load_inputs() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or not isinstance(payload.get("inputs"), dict):
        raise TypeError("Program stdin must contain an inputs object")
    return {str(key): value for key, value in payload["inputs"].items()}


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    summary = run(_load_inputs())
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
