from __future__ import annotations

from collections.abc import Iterable

BLOCKED_SELINUX_TYPES = {
    "passwd_file_t",
    "shadow_t",
    "etc_t",
    "etc_runtime_t",
    "sudoers_t",
    "root_t",
    "user_home_t",
    "user_home_dir_t",
    "admin_home_t",
    "staff_home_t",
}


def filter_allow_rules(lines: Iterable[str]) -> list[str]:
    rules: list[str] = []
    for line in lines:
        normalized = line.strip()
        if not normalized.startswith("allow ") or not normalized.endswith(";"):
            continue
        tokens = normalized.split()
        if any(token.rstrip(":;{}") in BLOCKED_SELINUX_TYPES for token in tokens):
            continue
        rules.append(normalized)
    return rules


def build_policy_install_request(
    *,
    agent_id: str,
    rules: list[str],
    workspace_path: str,
) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "workspace_path": workspace_path,
        "extra_rules": rules,
    }
