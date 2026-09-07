"""Fail-closed validation for the positive-negative-list Bitable contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

DEDUPLICATION_FIELDS = frozenset({"source_key", "canonical_incident_id", "cross_source_fingerprint"})
EXACT_QUERYABLE_FIELD_TYPES = frozenset({1})
SINGLE_SELECT_FIELD_TYPE = 3
RECOGNIZED_VIEW_PURPOSES = frozenset({"public_ledger", "deduplication_query", "record_link"})
RECOGNIZED_NOTIFICATION_PROVENANCE = frozenset({"trusted_feishu_context", "feishu_contact_resolution"})
FORBIDDEN_FIELD_TOKENS = frozenset(
    {
        "score",
        "ranking",
        "rank",
        "performance",
        "penalty",
        "pip",
        "termination",
        "terminate",
        "dismissal",
        "points",
        "绩效",
        "评分",
        "排名",
        "处罚",
        "解除",
    }
)


@dataclass(frozen=True)
class TableSchema:
    """The resolved Bitable contract used by the table adapter after preflight."""

    app_token: str
    table_id: str
    fields_by_id: Mapping[str, Mapping[str, object]]
    field_ids_by_semantic_name: Mapping[str, str]
    select_options_by_field_id: Mapping[str, frozenset[str]]
    deduplication_field_ids: Mapping[str, str]
    view_purposes: Mapping[str, str]
    can_create_records: bool
    notification_user_key: str


@dataclass(frozen=True)
class TableSchemaValidation:
    """A preflight outcome. Invalid outcomes intentionally carry no field mapping."""

    ok: bool
    errors: tuple[str, ...]
    schema: TableSchema | None


def validate_table_schema(
    fields: Sequence[Mapping[str, object]],
    required_fields: Mapping[str, Mapping[str, object]],
    enum_requirements: Mapping[str, Mapping[str, object]],
    *,
    app_token: str = "",
    target_table_id: str = "",
    candidate_table_ids: Sequence[str] = (),
    view_purposes: Mapping[str, str] | None = None,
    can_create_records: bool = False,
    notification_user_key: str = "",
    notification_identity_provenance: str = "",
    allow_deduplication_aliases: bool = False,
) -> TableSchemaValidation:
    """Validate a Bitable response without relying on column order or fuzzy names.

    ``required_fields`` is keyed by a workflow semantic name and each value must contain
    the deployed ``field_id``, exact ``field_name``, and Feishu numeric ``type``. Select
    requirements are keyed by the same semantic name and require ``field_id`` and the
    complete set of allowed option names.
    """

    resolved_view_purposes = dict(view_purposes or {})
    errors: list[str] = []
    fields_by_id: dict[str, Mapping[str, object]] = {}
    for field in fields:
        field_id = field.get("field_id")
        if not isinstance(field_id, str) or not field_id:
            errors.append("field_id.invalid")
            continue
        if field_id in fields_by_id:
            errors.append(f"{field_id}.duplicate")
            continue
        fields_by_id[field_id] = field

    if not target_table_id or tuple(candidate_table_ids) != (target_table_id,):
        errors.append("target_table.unique")
    if not app_token:
        errors.append("app_token.unresolved")
    if not can_create_records:
        errors.append("create_records.permission")
    if not notification_user_key or not notification_user_key.startswith("ou_"):
        errors.append("notification_identity.unresolved")
    if notification_identity_provenance not in RECOGNIZED_NOTIFICATION_PROVENANCE:
        errors.append("notification_identity.unresolved")
    if not resolved_view_purposes:
        errors.append("view_purposes.unresolved")
    elif any(
        not isinstance(view_id, str)
        or not view_id
        or not isinstance(purpose, str)
        or purpose not in RECOGNIZED_VIEW_PURPOSES
        for view_id, purpose in resolved_view_purposes.items()
    ):
        errors.append("view_purposes.invalid")

    field_ids_by_semantic_name: dict[str, str] = {}
    semantic_by_field_id: dict[str, str] = {}
    select_options_by_field_id: dict[str, frozenset[str]] = {}
    for semantic_name, requirement in required_fields.items():
        field_id = requirement.get("field_id")
        field_name = requirement.get("field_name")
        field_type = requirement.get("type")
        if not isinstance(field_id, str) or not field_id:
            errors.append(f"{semantic_name}.field_id")
            continue
        if field_id in semantic_by_field_id:
            previous = semantic_by_field_id[field_id]
            if not (
                allow_deduplication_aliases
                and semantic_name in DEDUPLICATION_FIELDS
                and previous in DEDUPLICATION_FIELDS
            ):
                errors.append(f"{semantic_name}.field_id.duplicate")
        semantic_by_field_id[field_id] = semantic_name

        field = fields_by_id.get(field_id)
        if field is None:
            errors.append(semantic_name)
            continue

        if field.get("field_name") != field_name:
            errors.append(f"{semantic_name}.name")
        if field.get("type") != field_type:
            errors.append(f"{semantic_name}.type")
        else:
            field_ids_by_semantic_name[semantic_name] = field_id

        if semantic_name in DEDUPLICATION_FIELDS and field.get("type") not in EXACT_QUERYABLE_FIELD_TYPES:
            errors.append(f"{semantic_name}.queryable")

        if _is_forbidden_field(field):
            errors.append(f"forbidden_field.{field_name}")

    for semantic_name, requirement in enum_requirements.items():
        field_id = requirement.get("field_id")
        expected_options = requirement.get("options")
        if not isinstance(field_id, str) or not isinstance(expected_options, (set, frozenset)):
            errors.append(f"{semantic_name}.options")
            continue

        field = fields_by_id.get(field_id)
        if field is None:
            errors.append(f"{semantic_name}.field")
            continue
        if field.get("type") != SINGLE_SELECT_FIELD_TYPE:
            errors.append(f"{semantic_name}.type")
        actual_options = _option_names(field)
        if not set(expected_options).issubset(actual_options):
            errors.append(f"{semantic_name}.options")
            continue
        select_options_by_field_id[field_id] = frozenset(actual_options)

    for semantic_name in DEDUPLICATION_FIELDS:
        if semantic_name not in field_ids_by_semantic_name:
            errors.append(semantic_name)

    for field in fields_by_id.values():
        if _is_forbidden_field(field):
            field_name = field.get("field_name")
            label = field_name if isinstance(field_name, str) and field_name else str(field.get("field_id"))
            errors.append(f"forbidden_field.{label}")

    if errors:
        return TableSchemaValidation(ok=False, errors=tuple(dict.fromkeys(errors)), schema=None)

    schema = TableSchema(
        app_token=app_token,
        table_id=target_table_id,
        fields_by_id=fields_by_id,
        field_ids_by_semantic_name=field_ids_by_semantic_name,
        select_options_by_field_id=select_options_by_field_id,
        deduplication_field_ids={name: field_ids_by_semantic_name[name] for name in DEDUPLICATION_FIELDS},
        view_purposes=resolved_view_purposes,
        can_create_records=can_create_records,
        notification_user_key=notification_user_key,
    )
    return TableSchemaValidation(ok=True, errors=(), schema=schema)


def _option_names(field: Mapping[str, object] | None) -> set[str]:
    if field is None:
        return set()
    property_ = field.get("property")
    if not isinstance(property_, Mapping):
        return set()
    options = property_.get("options")
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        return set()
    names: set[str] = set()
    for option in options:
        if isinstance(option, Mapping):
            name = option.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return names


def _is_forbidden_field(field: Mapping[str, object]) -> bool:
    """Reject fields that would turn the ledger into scoring or personnel tooling."""
    values = (field.get("field_id"), field.get("field_name"))
    for value in values:
        if not isinstance(value, str):
            continue
        lowered = value.casefold()
        if any(token in lowered or token in value for token in FORBIDDEN_FIELD_TOKENS):
            return True
    return False
