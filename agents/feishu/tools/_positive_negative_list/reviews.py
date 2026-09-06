"""Private three-part review drafts for positive-negative coaching."""

# ruff: noqa: RUF001

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import _feishu_impl

from _positive_negative_list.models import LedgerRecord

send_message_impl = _feishu_impl.send_message_impl


@dataclass(frozen=True)
class ReviewDraft:
    review_id: str
    record_id: str
    subject_user_key: str
    writer_user_key: str
    started_at: float
    objective_reason: str = ""
    corrective_action: str = ""
    prevention_plan: str = ""
    status: str = "started"

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> ReviewDraft:
        if not isinstance(value, dict):
            raise TypeError("review draft must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown review fields: {', '.join(sorted(map(str, unknown)))}")
        return cls(**value)


def _review_dir(root: str | Path) -> Path:
    directory = Path(root) / "positive-negative-list" / "reviews"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


def _review_path(root: str | Path, review_id: str) -> Path:
    if not review_id.strip():
        raise ValueError("review_id is required")
    digest = hashlib.sha256(review_id.encode()).hexdigest()
    return _review_dir(root) / f"{digest}.json"


def save_review(root: str | Path, draft: ReviewDraft) -> Path:
    path = _review_path(root, draft.review_id)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(draft), ensure_ascii=False), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def load_review(root: str | Path, review_id: str) -> ReviewDraft | None:
    try:
        raw = json.loads(_review_path(root, review_id).read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError:
        return None
    try:
        return ReviewDraft.from_mapping(raw)
    except TypeError, ValueError, KeyError:
        return None


def find_active_reviews(root: str | Path, writer_user_key: str) -> tuple[ReviewDraft, ...]:
    """Find private, unfinished reviews owned by one trusted Feishu identity."""
    if not writer_user_key.strip():
        return ()
    directory = Path(root) / "positive-negative-list" / "reviews"
    matches: list[ReviewDraft] = []
    for path in directory.glob("*.json") if directory.exists() else ():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            draft = ReviewDraft.from_mapping(raw)
        except OSError, json.JSONDecodeError, TypeError, ValueError, KeyError:
            continue
        if draft.writer_user_key == writer_user_key.strip() and draft.status == "started":
            matches.append(draft)
    return tuple(matches)


def review_prompt(record: LedgerRecord) -> str:
    return "\n".join(
        (
            "我们一起把这条记录复盘成下一次可执行的改进。请分别回答：",
            f"记录事实：{record.fact_summary}",
            "1. 客观原因：当时发生了什么，哪些约束或信息导致了这个结果？",
            "2. 补足动作：现在准备采取什么具体动作来补救或完成闭环？",
            "3. 防止再犯：下次准备设置什么提醒、节点或工作方式？",
            "这是私聊复盘草稿，不会写回总表，也不产生分数或绩效结论。",
        )
    )


def review_feedback(draft: ReviewDraft) -> str:
    return "\n".join(
        (
            "复盘已记录。",
            f"客观原因：{draft.objective_reason}",
            f"补足动作：{draft.corrective_action}",
            f"防止再犯：{draft.prevention_plan}",
            "下一步建议：把补足动作落实到任务或消息中，并在约定节点主动同步结果。",
            "本复盘仅用于沟通和改进，不写回总表，不产生分数、排名或绩效结论。",
        )
    )


def new_review(record: LedgerRecord, writer_user_key: str, review_id: str) -> ReviewDraft:
    return ReviewDraft(
        review_id=review_id,
        record_id=record.record_id,
        subject_user_key=record.subject_user_key,
        writer_user_key=writer_user_key,
        started_at=time.time(),
    )


__all__ = [
    "ReviewDraft",
    "find_active_reviews",
    "load_review",
    "new_review",
    "review_feedback",
    "review_prompt",
    "save_review",
    "send_message_impl",
]
