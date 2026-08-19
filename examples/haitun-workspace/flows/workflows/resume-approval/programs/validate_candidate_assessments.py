"""Validate dynamic-role candidate assessments before any Feishu write."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from copy import deepcopy
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_ID = re.compile(r"^[0-9a-f]{16}$")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_UNKNOWN_MISMATCH = re.compile(
    r"(?:unknown|not\s+mentioned|未提及|未说明|未知|不详|无法判断|待核实|无相关信息|信息缺失)",
    re.IGNORECASE,
)
_ASSESSMENT_FIELDS = {
    "schema_version",
    "status",
    "batch_id",
    "candidate_id",
    "candidate_name",
    "source",
    "grade",
    "education",
    "education_background",
    "resume_summary",
    "total_score",
    "matched_role_key",
    "matched_role_name",
    "match_points",
    "mismatch_points",
    "interview_recommendation",
    "interview_recommendation_reason",
    "verification_questions",
    "document_revisions",
}
_FAILURE_FIELDS = {
    "schema_version",
    "status",
    "batch_id",
    "candidate_id",
    "candidate_name",
    "source",
    "document_revisions",
    "failure",
}
_SOURCE_FIELDS = {
    "name",
    "sha256",
    "format",
    "extraction_mode",
    "extraction_quality",
    "extraction_warnings",
}
_POINT_FIELDS = {"requirement", "resume_evidence"}
_QUESTION_FIELDS = {
    "question",
    "category",
    "evidence_anchor",
    "purpose",
    "positive_signal",
    "risk_signal",
}
_REVISION_FIELDS = {"resume_scoring_sha256", "role_information_sha256"}
_FORMATS = {".pdf", ".docx", ".md", ".txt"}
_EXTRACTION_MODES = {"workspace_text", "read_pdf_tool"}
_EDUCATION_LEVELS = ("博士", "硕士", "本科", "专科", "高中及以下", "unknown")
_EDUCATION_STAGES = ("专科", "本科", "硕士", "博士")
_INTERVIEW_RECOMMENDATION_GRADES = ("A", "B")
_QUESTION_CATEGORIES = ("真实性核验", "岗位匹配", "风险澄清")
_PROTECTED_ATTRIBUTE = re.compile(
    r"(?:年龄|周岁|出生日期|生日|性别|婚姻|已婚|未婚|结婚|生育|备孕|怀孕|民族|宗教|"
    r"信仰|健康状况|身体健康|病史|疾病|残疾|残障|家庭住址|家庭地址|详细地址|现住址|"
    r"居住地址|户籍|籍贯|\bage\b|date\s+of\s+birth|\bmarital\b|\bpregnan(?:t|cy)\b|"
    r"\bfertility\b|\bethnicity\b|\breligion\b|health\s+condition|medical\s+history|"
    r"\bdisab(?:ility|led)\b|home\s+address|residential\s+address)",
    re.IGNORECASE,
)
_DEFINITIVE_NEGATIVE = re.compile(
    r"(?:没有|不具备|缺乏|无法|不能|不会|从未|未做过|does\s+not\s+have|doesn't\s+have|"
    r"\blacks?\b|\bcannot\b|\bcan't\b|\bnever\b)",
    re.IGNORECASE,
)
_UNKNOWN_WORDING = re.compile(
    r"(?:unknown|not\s+mentioned|未体现|未提及|未说明|未知|不详|无法判断|无相关信息|信息缺失|证据不足)",
    re.IGNORECASE,
)
_CAUTIOUS_VERIFICATION = re.compile(
    r"(?:需|需要|请|待)(?:在面试中)?(?:核实|确认|澄清|补充)|是否|能否|请说明|请举例|"
    r"核实|确认|澄清|verify|confirm|clarify|ask\s+about",
    re.IGNORECASE,
)
_INSTITUTION_STAGE = re.compile(r"^(专科|本科|硕士|博士)\uFF1A(.+)$")
_MULTI_INSTITUTION_SEPARATORS = re.compile(r"[/\uFF0F+\uFF0B→]|;")
_INSTITUTION_NOISE = re.compile(
    r"[()\uFF08\uFF09\uFF0C,]|\d|(?:专业|研究方向|方向|在读|毕业|应届|届|工作年限|工作经验|经历|"
    r"背景|保研|海归|双一流|全奖|排名|奖学金|GPA|学历|硕博连读|985|211)",
    re.IGNORECASE,
)


def _canonical_sha(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _document_map(reference_documents: Any) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    if not isinstance(reference_documents, list):
        return {}, ["reference_documents must be a list"]
    documents: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(reference_documents):
        prefix = f"reference_documents[{index}]"
        if not isinstance(value, dict):
            errors.append(f"{prefix} must be an object")
            continue
        purpose = value.get("purpose")
        if purpose not in {"resume_scoring", "role_information"}:
            errors.append(f"{prefix}.purpose is invalid")
            continue
        if purpose in documents:
            errors.append(f"reference_documents.{purpose} is duplicated")
            continue
        content = value.get("content")
        revision = value.get("content_sha256")
        if not isinstance(content, str) or not content.strip():
            errors.append(f"reference_documents.{purpose}.content must be non-empty text")
            continue
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if revision != expected:
            errors.append(f"reference_documents.{purpose}.content_sha256 does not match content")
            continue
        documents[purpose] = value
    for purpose in ("resume_scoring", "role_information"):
        if purpose not in documents:
            errors.append(f"reference_documents.{purpose} is required")
    return documents, errors


def _parse_threshold(token: str, total_max: int) -> tuple[int, int] | None:
    compact = re.sub(r"\s+", "", token).replace("\u2013", "-").replace("—", "-")
    if compact.endswith("+") and compact[:-1].isdigit():
        return int(compact[:-1]), total_max
    if compact.startswith("<") and compact[1:].isdigit():
        return 0, int(compact[1:]) - 1
    match = re.fullmatch(r"(\d+)-(\d+)", compact)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def parse_scoring_contract(reference_documents: Any) -> tuple[dict[str, Any], list[str]]:
    """Parse and prove the fixed batch's total and A-F ranges from the online document."""
    documents, document_errors = _document_map(reference_documents)
    errors = list(document_errors)
    scoring = documents.get("resume_scoring")
    if scoring is None:
        return {}, errors
    content = str(scoring["content"])
    declared = {int(value) for value in re.findall(r"满分\s*(\d+)\s*分", content)}
    if len(declared) != 1:
        errors.append("scoring_contract.must_declare_one_total")
        return {}, errors
    total_max = declared.pop()
    dimensions = [
        {"name": name.strip(), "max": int(maximum)}
        for name, maximum in re.findall(
            r"^\s*\d+\s*[.\uFF0E、]\s*([^\n\uFF08(]+?)\s*[\uFF08(]\s*(\d+)\s*分\s*[\uFF09)]",
            content,
            re.MULTILINE,
        )
    ]
    if not dimensions:
        errors.append("scoring_contract.dimensions_must_be_non_empty")
    elif sum(item["max"] for item in dimensions) != total_max:
        errors.append("scoring_contract.dimension_maxima_must_sum_to_declared_total")

    marker = "评级对照"
    if marker not in content:
        errors.append("scoring_contract.rating_section_is_required")
        return {}, errors
    rating_content = content.split(marker, maxsplit=1)[1]
    grades = re.findall(r"^\s*([A-F])\s*$", rating_content, re.MULTILINE)
    threshold_tokens = re.findall(
        r"^\s*(\d+\s*\+|\d+\s*[-\u2013—]\s*\d+|<\s*\d+)\s*$",
        rating_content,
        re.MULTILINE,
    )
    if grades != list("ABCDEF"):
        errors.append("scoring_contract.grades_must_equal_A_through_F")
    if len(threshold_tokens) != 6:
        errors.append("scoring_contract.must_define_six_grade_thresholds")
    grade_ranges: dict[str, list[int]] = {}
    if grades == list("ABCDEF") and len(threshold_tokens) == 6:
        for grade, token in zip(grades, threshold_tokens, strict=True):
            parsed = _parse_threshold(token, total_max)
            if parsed is None:
                errors.append(f"scoring_contract.invalid_threshold:{grade}")
                continue
            grade_ranges[grade] = [parsed[0], parsed[1]]
        covered: list[int] = []
        for grade in "ABCDEF":
            if grade not in grade_ranges:
                continue
            low, high = grade_ranges[grade]
            if low < 0 or high > total_max or low > high:
                errors.append(f"scoring_contract.invalid_range:{grade}")
            else:
                covered.extend(range(low, high + 1))
        if sorted(covered) != list(range(total_max + 1)) or len(covered) != len(set(covered)):
            errors.append("scoring_contract.grade_ranges_must_cover_total_without_gaps_or_overlap")

    if errors:
        return {}, errors
    return {
        "schema_version": "1.0",
        "source_sha256": scoring["content_sha256"],
        "total_max": total_max,
        "dimensions": dimensions,
        "grade_ranges": grade_ranges,
    }, []


def _active_roles(role_catalog: Any, role_revision: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    if not isinstance(role_catalog, dict):
        return {}, ["role_catalog must be an object"]
    if role_catalog.get("source_document_sha256") != role_revision:
        errors.append("role_catalog.source_document_sha256 does not match the fixed document")
    values = role_catalog.get("roles")
    if not isinstance(values, list) or not values:
        errors.append("role_catalog.roles must be a non-empty list")
        return {}, errors
    roles: dict[str, dict[str, Any]] = {}
    for index, role in enumerate(values):
        if not isinstance(role, dict):
            errors.append(f"role_catalog.roles[{index}] must be an object")
            continue
        if role.get("status") != "active":
            continue
        key = role.get("role_key")
        if not isinstance(key, str) or not key:
            errors.append(f"role_catalog.roles[{index}].role_key must be non-empty text")
            continue
        if key in roles:
            errors.append(f"role_catalog.roles[{index}].role_key is duplicated")
            continue
        if not isinstance(role.get("name"), str) or not role["name"].strip():
            errors.append(f"role_catalog.roles[{index}] must define name")
            continue
        roles[key] = role
    if not roles:
        errors.append("role_catalog must contain an active role")
    return roles, errors


def _normalize_assessment(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"candidate_assessments"}:
        return value["candidate_assessments"]
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        parsed = json.loads(text)
    except TypeError, ValueError:
        return value
    if isinstance(parsed, dict) and set(parsed) == {"candidate_assessments"}:
        return parsed["candidate_assessments"]
    return parsed


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalized_link_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff+#]+", "", value).lower()


def _exact_source_link(value: str, sources: set[str]) -> bool:
    normalized = _normalized_link_text(value)
    return bool(normalized) and normalized in {_normalized_link_text(source) for source in sources}


def _question_links_to_anchor(question: str, anchor: str) -> bool:
    normalized_question = _normalized_link_text(question)
    normalized_anchor = _normalized_link_text(anchor)
    if len(normalized_anchor) >= 2 and (
        normalized_anchor in normalized_question or normalized_question in normalized_anchor
    ):
        return True
    latin_token = re.compile(r"[a-zA-Z0-9+#]{2,}")
    question_tokens = {token.lower() for token in latin_token.findall(question)}
    anchor_tokens = {token.lower() for token in latin_token.findall(anchor)}
    if question_tokens & anchor_tokens:
        return True
    generic_bigrams = {
        "项目",
        "经历",
        "经验",
        "能力",
        "岗位",
        "要求",
        "简历",
        "说明",
        "情况",
        "相关",
        "工作",
        "负责",
    }

    def chinese_bigrams(text: str) -> set[str]:
        chunks = re.findall(r"[\u4e00-\u9fff]+", text)
        return {
            chunk[index : index + 2]
            for chunk in chunks
            for index in range(len(chunk) - 1)
            if chunk[index : index + 2] not in generic_bigrams
        }

    return bool(chinese_bigrams(question) & chinese_bigrams(anchor))


def _unsupported_negative_claim(text: str, grounded_sources: set[str]) -> bool:
    if _DEFINITIVE_NEGATIVE.search(text):
        normalized = _normalized_link_text(text)
        grounded_negative = any(
            _DEFINITIVE_NEGATIVE.search(source) and _normalized_link_text(source) in normalized
            for source in grounded_sources
        )
        if not grounded_negative:
            return True
    return bool(_UNKNOWN_WORDING.search(text) and not _CAUTIOUS_VERIFICATION.search(text))


def render_verification_questions(value: Any) -> str:
    """Render the only public question-bank representation in stable source order."""
    if not isinstance(value, list) or not 3 <= len(value) <= 6:
        raise ValueError("verification_questions must contain 3 to 6 questions")
    lines: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != _QUESTION_FIELDS:
            raise ValueError(f"verification_questions[{index}] fields do not match the contract")
        category = item.get("category")
        question = item.get("question")
        if category not in _QUESTION_CATEGORIES:
            raise ValueError(f"verification_questions[{index}].category is invalid")
        if not _non_empty_text(question):
            raise ValueError(f"verification_questions[{index}].question must be non-empty text")
        lines.append(f"{index + 1}. [{category}] {question.strip()}")
    return "\n".join(lines)


def _question_source_sets(value: dict[str, Any], role: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    role_requirements = _role_requirements(role)
    match_evidence: set[str] = set()
    mismatch_evidence: set[str] = set()
    mismatch_requirements: set[str] = set()
    for field, evidence_target in (("match_points", match_evidence), ("mismatch_points", mismatch_evidence)):
        points = value.get(field)
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict):
                continue
            evidence = point.get("resume_evidence")
            if isinstance(evidence, list):
                evidence_target.update(item.strip() for item in evidence if _non_empty_text(item))
            if field == "mismatch_points" and _non_empty_text(point.get("requirement")):
                mismatch_requirements.add(point["requirement"].strip())
    return match_evidence | mismatch_evidence, role_requirements, mismatch_requirements | mismatch_evidence


def _validate_verification_questions(
    value: Any,
    prefix: str,
    assessment: dict[str, Any],
    role: dict[str, Any],
) -> list[str]:
    if not isinstance(value, list) or not 3 <= len(value) <= 6:
        return [f"{prefix} must contain 3 to 6 questions"]
    resume_sources, role_sources, risk_sources = _question_source_sets(assessment, role)
    allowed_by_category = {
        "真实性核验": resume_sources,
        "岗位匹配": role_sources | resume_sources,
        "风险澄清": risk_sources,
    }
    errors: list[str] = []
    categories: set[str] = set()
    seen_questions: set[str] = set()
    for index, item in enumerate(value):
        item_prefix = f"{prefix}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_prefix} must be an object")
            continue
        missing = sorted(_QUESTION_FIELDS - set(item))
        extra = sorted(set(item) - _QUESTION_FIELDS)
        if missing:
            errors.append(f"{item_prefix}.missing_fields:{','.join(missing)}")
        if extra:
            errors.append(f"{item_prefix}.unexpected_fields:{','.join(extra)}")
        for field in _QUESTION_FIELDS:
            if not _non_empty_text(item.get(field)):
                errors.append(f"{item_prefix}.{field} must be non-empty text")
        category = item.get("category")
        if category not in _QUESTION_CATEGORIES:
            errors.append(f"{item_prefix}.category must be one of:{','.join(_QUESTION_CATEGORIES)}")
            continue
        categories.add(category)
        question = item.get("question")
        anchor = item.get("evidence_anchor")
        if not _non_empty_text(question) or not _non_empty_text(anchor):
            continue
        normalized_question = _normalized_link_text(question)
        if normalized_question in seen_questions:
            errors.append(f"{item_prefix}.question is duplicated")
        seen_questions.add(normalized_question)
        sources = allowed_by_category[category]
        if not _exact_source_link(anchor, sources):
            errors.append(f"{item_prefix}.evidence_anchor is not linked to the assessment or selected role")
        if not _question_links_to_anchor(question, anchor):
            errors.append(f"{item_prefix}.question is not linked to its evidence_anchor")
        for field in _QUESTION_FIELDS - {"category"}:
            text = item.get(field)
            if _non_empty_text(text) and _PROTECTED_ATTRIBUTE.search(text):
                errors.append(f"{item_prefix}.{field} references a protected attribute")
        grounded_sources = sources | {anchor}
        for field in ("question", "purpose"):
            text = item.get(field)
            if _non_empty_text(text) and _unsupported_negative_claim(text, grounded_sources):
                errors.append(f"{item_prefix}.{field} treats unknown information as a negative fact")
    for category in _QUESTION_CATEGORIES[:2]:
        if category not in categories:
            errors.append(f"{prefix} must include category {category}")
    if assessment.get("mismatch_points") and "风险澄清" not in categories:
        errors.append(f"{prefix} must include category 风险澄清 for material risks or evidence gaps")
    return errors


def _privacy_errors(value: Any, path: str) -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            errors.extend(_privacy_errors(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_privacy_errors(item, f"{path}[{index}]"))
    elif isinstance(value, str) and (_EMAIL.search(value) or _PHONE.search(value)):
        errors.append(f"{path} contains forbidden contact information")
    return errors


def _validate_source(value: Any, prefix: str, *, failed: bool) -> tuple[str | None, list[str]]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return None, [f"{prefix} must be an object"]
    missing = sorted(_SOURCE_FIELDS - set(value))
    extra = sorted(set(value) - _SOURCE_FIELDS)
    if missing:
        errors.append(f"{prefix}.missing_fields:{','.join(missing)}")
    if extra:
        errors.append(f"{prefix}.unexpected_fields:{','.join(extra)}")
    if not _non_empty_text(value.get("name")):
        errors.append(f"{prefix}.name must be non-empty text")
    source_sha = value.get("sha256")
    if not isinstance(source_sha, str) or _SHA256.fullmatch(source_sha) is None:
        errors.append(f"{prefix}.sha256 must be 64 lowercase hexadecimal characters")
        source_sha = None
    if value.get("format") not in _FORMATS:
        errors.append(f"{prefix}.format is invalid")
    if value.get("extraction_mode") not in _EXTRACTION_MODES:
        errors.append(f"{prefix}.extraction_mode is invalid")
    expected_quality = "unusable" if failed else "good"
    if value.get("extraction_quality") != expected_quality:
        errors.append(f"{prefix}.extraction_quality must equal {expected_quality}")
    warnings = value.get("extraction_warnings")
    if warnings != [] and (not isinstance(warnings, list) or not all(_non_empty_text(item) for item in warnings)):
        errors.append(f"{prefix}.extraction_warnings must be a list of non-empty text")
    return source_sha, errors


def _validate_revisions(value: Any, prefix: str, expected: dict[str, str]) -> list[str]:
    if not isinstance(value, dict):
        return [f"{prefix} must be an object"]
    errors: list[str] = []
    missing = sorted(_REVISION_FIELDS - set(value))
    extra = sorted(set(value) - _REVISION_FIELDS)
    if missing:
        errors.append(f"{prefix}.missing_fields:{','.join(missing)}")
    if extra:
        errors.append(f"{prefix}.unexpected_fields:{','.join(extra)}")
    for name, revision in expected.items():
        if value.get(name) != revision:
            errors.append(f"{prefix}.{name} does not match the fixed document")
    return errors


def _role_requirements(role: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for collection in ("responsibilities", "hard_requirements", "preferences"):
        items = role.get(collection)
        if isinstance(items, list):
            values.update(item.strip() for item in items if _non_empty_text(item))
    return values


def _validate_points(value: Any, prefix: str, role: dict[str, Any], *, mismatch: bool) -> list[str]:
    if not isinstance(value, list):
        return [f"{prefix} must be a list"]
    if not value:
        return [f"{prefix} must contain at least one point"]
    errors: list[str] = []
    allowed = _role_requirements(role)
    seen: set[str] = set()
    for index, point in enumerate(value):
        item_prefix = f"{prefix}[{index}]"
        if not isinstance(point, dict):
            errors.append(f"{item_prefix} must be an object")
            continue
        if set(point) != _POINT_FIELDS:
            missing = sorted(_POINT_FIELDS - set(point))
            extra = sorted(set(point) - _POINT_FIELDS)
            if missing:
                errors.append(f"{item_prefix}.missing_fields:{','.join(missing)}")
            if extra:
                errors.append(f"{item_prefix}.unexpected_fields:{','.join(extra)}")
        requirement = point.get("requirement")
        if requirement not in allowed:
            errors.append(f"{item_prefix}.requirement must exactly match the selected catalog role")
        elif requirement in seen:
            errors.append(f"{item_prefix}.requirement is duplicated")
        else:
            seen.add(requirement)
        evidence = point.get("resume_evidence")
        if not isinstance(evidence, list) or not evidence or not all(_non_empty_text(item) for item in evidence):
            errors.append(f"{item_prefix}.resume_evidence must be non-empty")
        elif mismatch and any(_UNKNOWN_MISMATCH.search(item) for item in evidence):
            errors.append(f"{item_prefix}.resume_evidence treats unknown as mismatch")
    return errors


def _grade_for_score(score: int, grade_ranges: dict[str, list[int]]) -> str | None:
    for grade in "ABCDEF":
        low, high = grade_ranges[grade]
        if low <= score <= high:
            return grade
    return None


def _expected_interview_recommendation(value: dict[str, Any], role: dict[str, Any]) -> str:
    hard_requirements = {item.strip() for item in role.get("hard_requirements", []) if _non_empty_text(item)}
    matched_requirements = {
        point.get("requirement")
        for point in value.get("match_points", [])
        if isinstance(point, dict) and _non_empty_text(point.get("requirement"))
    }
    mismatched_requirements = {
        point.get("requirement")
        for point in value.get("mismatch_points", [])
        if isinstance(point, dict) and _non_empty_text(point.get("requirement"))
    }
    eligible = (
        value.get("grade") in _INTERVIEW_RECOMMENDATION_GRADES
        and hard_requirements <= matched_requirements
        and hard_requirements.isdisjoint(mismatched_requirements)
    )
    return "建议面试" if eligible else "不建议面试"


def _validate_education_fields(value: dict[str, Any], prefix: str) -> list[str]:
    errors: list[str] = []
    education = value.get("education")
    if not _non_empty_text(education):
        errors.append(f"{prefix}.education must be non-empty text")
    elif education not in _EDUCATION_LEVELS:
        errors.append(f"{prefix}.education must be one of:{','.join(_EDUCATION_LEVELS)}")

    background = value.get("education_background")
    if not _non_empty_text(background):
        errors.append(f"{prefix}.education_background must be non-empty text")
        return errors
    if background == "unknown":
        return errors
    if _MULTI_INSTITUTION_SEPARATORS.search(background):
        errors.append(
            f"{prefix}.education_background must use semicolon-separated stage labels for multiple institutions"
        )
        return errors

    segments = background.split("\uff1b")
    parsed: list[tuple[str | None, str]] = []
    for segment in segments:
        text = segment.strip()
        match = _INSTITUTION_STAGE.fullmatch(text)
        if match is not None:
            parsed.append((match.group(1), match.group(2).strip()))
        elif "\uff1a" in text or ":" in text:
            errors.append(f"{prefix}.education_background has an invalid education stage label")
            return errors
        else:
            errors.append(f"{prefix}.education_background must label each institution with its education stage")
            return errors

    labels = [stage for stage, _ in parsed if stage is not None]
    if len(labels) != len(set(labels)):
        errors.append(f"{prefix}.education_background must not repeat an education stage")
    if labels and labels != sorted(labels, key=_EDUCATION_STAGES.index):
        errors.append(f"{prefix}.education_background education stages must be ordered from lower to higher")
    if any(not institution or _INSTITUTION_NOISE.search(institution) for _, institution in parsed):
        errors.append(f"{prefix}.education_background must contain institution names only")
    return errors


def _validate_resume_summary(value: Any, prefix: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 5:
        return [f"{prefix}.resume_summary must contain 1 to 5 bullet items"]
    errors: list[str] = []
    if any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{prefix}.resume_summary items must be non-empty strings")
    if any(
        isinstance(item, str) and item.strip() and (not item.startswith("- ") or not item[2:].strip()) for item in value
    ):
        errors.append(f"{prefix}.resume_summary items must start with '- '")
    return errors


def _validate_table_points(value: Any, prefix: str) -> list[str]:
    if not isinstance(value, list):
        return [f"{prefix} must be a table-writeable list"]
    errors: list[str] = []
    for index, item in enumerate(value):
        item_prefix = f"{prefix}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_prefix} must be a table-writeable object")
            continue
        if not isinstance(item.get("requirement"), str):
            errors.append(f"{item_prefix}.requirement must be table-writeable text")
        evidence = item.get("resume_evidence")
        if not isinstance(evidence, list) or any(not isinstance(entry, str) for entry in evidence):
            errors.append(f"{item_prefix}.resume_evidence must be a table-writeable text list")
    return errors


def _validate_table_writeability(value: dict[str, Any], prefix: str) -> list[str]:
    """Check only the shape and primitive types required by the Feishu row mapper."""
    errors: list[str] = []
    if not isinstance(value.get("candidate_id"), str):
        errors.append(f"{prefix}.candidate_id must be table-writeable text")
    status = value.get("status")
    if status == "extraction_failed":
        return errors
    if status != "assessed":
        errors.append(f"{prefix}.status must identify assessed or extraction_failed JSON")
        return errors

    for field in (
        "candidate_name",
        "grade",
        "education",
        "education_background",
        "matched_role_name",
        "interview_recommendation",
        "interview_recommendation_reason",
    ):
        if not isinstance(value.get(field), str):
            errors.append(f"{prefix}.{field} must be table-writeable text")

    grade = value.get("grade")
    if isinstance(grade, str) and grade not in set("ABCDEF"):
        errors.append(f"{prefix}.grade is not a writeable Feishu option")
    recommendation = value.get("interview_recommendation")
    if isinstance(recommendation, str) and recommendation not in {"建议面试", "不建议面试"}:
        errors.append(f"{prefix}.interview_recommendation is not a writeable Feishu option")

    summary = value.get("resume_summary")
    if not isinstance(summary, list) or any(not isinstance(item, str) for item in summary):
        errors.append(f"{prefix}.resume_summary must be a table-writeable JSON string array")

    total_score = value.get("total_score")
    if isinstance(total_score, bool) or not isinstance(total_score, (int, float)) or not math.isfinite(total_score):
        errors.append(f"{prefix}.total_score must be a table-writeable number")

    errors.extend(_validate_table_points(value.get("match_points"), f"{prefix}.match_points"))
    errors.extend(_validate_table_points(value.get("mismatch_points"), f"{prefix}.mismatch_points"))
    try:
        render_verification_questions(value.get("verification_questions"))
    except (TypeError, ValueError) as exc:
        errors.append(f"{prefix}.verification_questions is not table-writeable: {exc}")
    return errors


def _identity_errors(value: dict[str, Any], prefix: str, batch_id: str, source_sha: str | None) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") != "3.0":
        errors.append(f"{prefix}.schema_version must equal 3.0")
    if value.get("batch_id") != batch_id:
        errors.append(f"{prefix}.batch_id must equal the workflow batch_id")
    candidate_id = value.get("candidate_id")
    if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
        errors.append(f"{prefix}.candidate_id must be 16 lowercase hexadecimal characters")
    elif source_sha is not None and candidate_id != source_sha[:16]:
        errors.append(f"{prefix}.candidate_id must equal the source hash prefix")
    return errors


def _validate_assessed(
    value: Any,
    *,
    index: int,
    batch_id: str,
    scoring: dict[str, Any],
    roles: dict[str, dict[str, Any]],
    revisions: dict[str, str],
) -> list[str]:
    prefix = f"candidate_assessments[{index}]"
    if not isinstance(value, dict):
        return [f"{prefix} must be an object"]
    errors: list[str] = []
    missing = sorted(_ASSESSMENT_FIELDS - set(value))
    extra = sorted(set(value) - _ASSESSMENT_FIELDS)
    if missing:
        errors.append(f"{prefix}.missing_fields:{','.join(missing)}")
    if extra:
        errors.append(f"{prefix}.unexpected_fields:{','.join(extra)}")
    if value.get("status") != "assessed":
        errors.append(f"{prefix}.status must equal assessed")
    source_sha, source_errors = _validate_source(value.get("source"), f"{prefix}.source", failed=False)
    errors.extend(source_errors)
    errors.extend(_identity_errors(value, prefix, batch_id, source_sha))
    errors.extend(_validate_revisions(value.get("document_revisions"), f"{prefix}.document_revisions", revisions))
    if not _non_empty_text(value.get("candidate_name")):
        errors.append(f"{prefix}.candidate_name must be non-empty text")
    errors.extend(_validate_education_fields(value, prefix))
    errors.extend(_validate_resume_summary(value.get("resume_summary"), prefix))

    role_key = value.get("matched_role_key")
    role = roles.get(role_key) if isinstance(role_key, str) else None
    if role is None:
        errors.append(f"{prefix}.matched_role_key must identify an active catalog role")
    else:
        if value.get("matched_role_name") != role.get("name"):
            errors.append(f"{prefix}.matched_role_name must equal the catalog role name")
        errors.extend(_validate_points(value.get("match_points"), f"{prefix}.match_points", role, mismatch=False))
        errors.extend(_validate_points(value.get("mismatch_points"), f"{prefix}.mismatch_points", role, mismatch=True))
        errors.extend(
            _validate_verification_questions(
                value.get("verification_questions"),
                f"{prefix}.verification_questions",
                value,
                role,
            )
        )

    total = value.get("total_score")
    total_max = scoring["total_max"]
    if not isinstance(total, int) or isinstance(total, bool) or not 0 <= total <= total_max:
        errors.append(f"{prefix}.total_score must be an integer from 0 to {total_max}")
    else:
        expected_grade = _grade_for_score(total, scoring["grade_ranges"])
        if value.get("grade") != expected_grade:
            errors.append(f"{prefix}.grade must equal {expected_grade} for total_score {total}")
    if value.get("interview_recommendation") not in {"建议面试", "不建议面试"}:
        errors.append(f"{prefix}.interview_recommendation is invalid")
    elif role is not None:
        expected_recommendation = _expected_interview_recommendation(value, role)
        if value.get("interview_recommendation") != expected_recommendation:
            errors.append(
                f"{prefix}.interview_recommendation must equal {expected_recommendation} "
                "under the deterministic grade and hard-requirement gate"
            )
    if not _non_empty_text(value.get("interview_recommendation_reason")):
        errors.append(f"{prefix}.interview_recommendation_reason must be non-empty text")
    errors.extend(_privacy_errors(value, prefix))
    return errors


def _validate_failure(
    value: Any,
    *,
    index: int,
    batch_id: str,
    revisions: dict[str, str],
) -> list[str]:
    prefix = f"candidate_assessments[{index}]"
    if not isinstance(value, dict):
        return [f"{prefix} must be an object"]
    errors: list[str] = []
    missing = sorted(_FAILURE_FIELDS - set(value))
    extra = sorted(set(value) - _FAILURE_FIELDS)
    if missing:
        errors.append(f"{prefix}.missing_fields:{','.join(missing)}")
    if extra:
        errors.append(f"{prefix}.unexpected_fields:{','.join(extra)}")
    if value.get("status") != "extraction_failed":
        errors.append(f"{prefix}.status must equal extraction_failed")
    source_sha, source_errors = _validate_source(value.get("source"), f"{prefix}.source", failed=True)
    errors.extend(source_errors)
    errors.extend(_identity_errors(value, prefix, batch_id, source_sha))
    errors.extend(_validate_revisions(value.get("document_revisions"), f"{prefix}.document_revisions", revisions))
    if value.get("candidate_name") != "unknown":
        errors.append(f"{prefix}.candidate_name must equal unknown")
    source = value.get("source")
    if isinstance(source, dict) and (
        source.get("format") != ".pdf" or source.get("extraction_mode") != "read_pdf_tool"
    ):
        errors.append(f"{prefix}.source must identify a read_pdf_tool PDF failure")
    failure = value.get("failure")
    if not isinstance(failure, dict):
        errors.append(f"{prefix}.failure must be an object")
    else:
        if failure.get("stage") != "pdf_ocr" or failure.get("code") != "pdf_extraction_failed":
            errors.append(f"{prefix}.failure must identify pdf_extraction_failed")
        if not _non_empty_text(failure.get("message")):
            errors.append(f"{prefix}.failure.message must be non-empty text")
    errors.extend(_privacy_errors(value, prefix))
    return errors


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    del workspace_root
    errors: list[str] = []
    writeability_errors: list[str] = []
    final_writeability_only = "candidate_assessments_repaired" in inputs
    batch_id = inputs.get("batch_id")
    if not _non_empty_text(batch_id):
        errors.append("batch_id must be non-empty text")
        writeability_errors.append("batch_id must be non-empty text")
        batch_id = ""

    documents, document_errors = _document_map(inputs.get("reference_documents"))
    errors.extend(document_errors)
    scoring, scoring_errors = parse_scoring_contract(inputs.get("reference_documents"))
    for error in scoring_errors:
        if error not in errors:
            errors.append(error)
    revisions = {
        "resume_scoring_sha256": str(documents.get("resume_scoring", {}).get("content_sha256", "")),
        "role_information_sha256": str(documents.get("role_information", {}).get("content_sha256", "")),
    }
    roles, role_errors = _active_roles(inputs.get("role_catalog"), revisions["role_information_sha256"])
    errors.extend(role_errors)

    assessments = inputs.get("candidate_assessments_repaired", inputs.get("candidate_assessments"))
    if not isinstance(assessments, list) or not assessments:
        errors.append("candidate_assessments must be a non-empty list")
        writeability_errors.append("candidate_assessments must be a non-empty list")
        assessments = []

    validated: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    authorities_ready = bool(scoring) and bool(roles) and not document_errors and not scoring_errors and not role_errors
    for index, raw in enumerate(assessments):
        value = _normalize_assessment(raw)
        if not isinstance(value, dict):
            errors.append(f"candidate_assessments[{index}] must be an object")
            writeability_errors.append(f"candidate_assessments[{index}] must be a table-writeable object")
            continue
        candidate_writeability_errors = _validate_table_writeability(value, f"candidate_assessments[{index}]")
        writeability_errors.extend(candidate_writeability_errors)
        candidate_errors: list[str]
        if value.get("status") == "extraction_failed":
            candidate_errors = _validate_failure(value, index=index, batch_id=batch_id, revisions=revisions)
            if not candidate_errors or (final_writeability_only and not candidate_writeability_errors):
                failed.append(deepcopy(value))
        elif authorities_ready:
            candidate_errors = _validate_assessed(
                value,
                index=index,
                batch_id=batch_id,
                scoring=scoring,
                roles=roles,
                revisions=revisions,
            )
            if not candidate_errors or (final_writeability_only and not candidate_writeability_errors):
                validated.append(deepcopy(value))
        else:
            candidate_errors = []
        if final_writeability_only:
            for error in candidate_errors:
                if ".verification_questions" in error and error not in writeability_errors:
                    writeability_errors.append(error)
        errors.extend(candidate_errors)
        candidate_id = value.get("candidate_id")
        source = value.get("source")
        source_sha = source.get("sha256") if isinstance(source, dict) else None
        if isinstance(candidate_id, str):
            if candidate_id in seen_ids:
                errors.append(f"candidate_assessments[{index}].candidate_id is duplicated")
            seen_ids.add(candidate_id)
        if isinstance(source_sha, str):
            if source_sha in seen_hashes:
                errors.append(f"candidate_assessments[{index}].source.sha256 is duplicated")
            seen_hashes.add(source_sha)

    revision = ""
    if authorities_ready:
        revision = _canonical_sha(
            {
                "contract": "candidate-assessment-v3-question-bank-v1",
                "document_revisions": revisions,
                "scoring_contract": scoring,
                "role_catalog": inputs.get("role_catalog"),
            }
        )
    if final_writeability_only and not revision:
        writeability_errors.append("assessment_revision cannot be generated for table write")
    blocking_errors = writeability_errors if final_writeability_only else errors
    constraint_warnings = errors if final_writeability_only else []
    if blocking_errors:
        validated = []
        failed = []
    else:
        for assessment in validated:
            assessment["assessment_revision"] = revision
    status = "complete" if not blocking_errors else "blocked"
    bundle = {
        "schema_version": "3.0",
        "status": status,
        "batch_id": batch_id,
        "document_revisions": revisions if all(_SHA256.fullmatch(value) for value in revisions.values()) else {},
        "assessment_revision": revision,
        "assessments": validated,
        "failed_candidates": failed,
        "errors": blocking_errors,
        "constraint_warnings": constraint_warnings,
    }
    return {
        "errors": blocking_errors,
        "validated_candidate_assessments": bundle,
        "assessment_validation_manifest": {
            "schema_version": "1.0",
            "status": status,
            "assessment_count": len(validated),
            "failed_candidate_count": len(failed),
            "error_count": len(blocking_errors),
            "errors": blocking_errors,
            "constraint_warning_count": len(constraint_warnings),
            "constraint_warnings": constraint_warnings,
            "assessment_revision": revision,
        },
    }


def _load_inputs() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or not isinstance(payload.get("inputs"), dict):
        raise TypeError("Program stdin must contain an inputs object")
    return payload["inputs"]


def _program_outputs(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "validated_candidate_assessments": result["validated_candidate_assessments"],
        "assessment_validation_manifest": result["assessment_validation_manifest"],
    }


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    result = run(_load_inputs())
    sys.stdout.write(json.dumps(_program_outputs(result), ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
