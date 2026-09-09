"""POC L2 probe — structured playbook runner (operable capability checks).

刻意为之: v1 only runs static assert_* steps against agent/workspace files and
tool exports. Live chat against the current Session would deadlock under the
turn lock; natural-language method_text alone is fail-closed (need a playbook).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

DISCLAIMER = (
    "L2 结果只说明「按剧本能否核对/复现」; 不等于组织验收通过, 也不等于数据已验真。无结构化方法/剧本时不得声称已做 L2。"
)

_VALID_KINDS = frozenset(
    {
        "assert_path",
        "assert_file_contains",
        "assert_tool",
        "unsupported",
    }
)


def _tools_dir() -> Path:
    return Path(__file__).resolve().parent


def _public_tool_names(tools_dir: Path | None = None) -> set[str]:
    root = tools_dir or _tools_dir()
    names: set[str] = set()
    for py in root.glob("*.py"):
        if py.name.startswith("_"):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except OSError, SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_"):
                names.add(node.name)
    return names


def _resolve_root(
    root: str,
    *,
    agent_dir: Path,
    workspace_dir: Path,
) -> Path | None:
    key = (root or "agent").strip().lower()
    if key == "agent":
        return agent_dir
    if key == "workspace":
        return workspace_dir
    return None


def _step_assert_path(
    step: dict[str, Any],
    *,
    agent_dir: Path,
    workspace_dir: Path,
) -> dict[str, Any]:
    sid = str(step.get("id", "")).strip() or "assert_path"
    root = _resolve_root(str(step.get("root", "agent")), agent_dir=agent_dir, workspace_dir=workspace_dir)
    if root is None:
        return {
            "id": sid,
            "ok": False,
            "kind": "assert_path",
            "detail": f"unknown root={step.get('root')!r} (use agent|workspace)",
        }
    rel = str(step.get("path", "")).strip()
    if not rel or ".." in Path(rel).parts:
        return {"id": sid, "ok": False, "kind": "assert_path", "detail": "path required; no .."}
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return {"id": sid, "ok": False, "kind": "assert_path", "detail": "path escapes root"}
    expect = str(step.get("expect", "exists")).strip().lower()
    exists = target.exists()
    if expect == "exists":
        ok = exists
        detail = "exists" if ok else f"missing: {rel}"
    elif expect in {"missing", "absent"}:
        ok = not exists
        detail = "absent" if ok else f"still exists: {rel}"
    else:
        return {
            "id": sid,
            "ok": False,
            "kind": "assert_path",
            "detail": f"unknown expect={expect!r}",
        }
    return {"id": sid, "ok": ok, "kind": "assert_path", "detail": detail, "path": rel}


def _step_assert_file_contains(
    step: dict[str, Any],
    *,
    agent_dir: Path,
    workspace_dir: Path,
) -> dict[str, Any]:
    sid = str(step.get("id", "")).strip() or "assert_file_contains"
    root = _resolve_root(str(step.get("root", "agent")), agent_dir=agent_dir, workspace_dir=workspace_dir)
    if root is None:
        return {
            "id": sid,
            "ok": False,
            "kind": "assert_file_contains",
            "detail": f"unknown root={step.get('root')!r}",
        }
    rel = str(step.get("path", "")).strip()
    if not rel or ".." in Path(rel).parts:
        return {
            "id": sid,
            "ok": False,
            "kind": "assert_file_contains",
            "detail": "path required; no ..",
        }
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return {
            "id": sid,
            "ok": False,
            "kind": "assert_file_contains",
            "detail": "path escapes root",
        }
    if not target.is_file():
        return {
            "id": sid,
            "ok": False,
            "kind": "assert_file_contains",
            "detail": f"not a file: {rel}",
        }
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "id": sid,
            "ok": False,
            "kind": "assert_file_contains",
            "detail": f"read failed: {exc}",
        }
    needles = step.get("must_contain") or []
    if not isinstance(needles, list) or not needles:
        return {
            "id": sid,
            "ok": False,
            "kind": "assert_file_contains",
            "detail": "must_contain must be a non-empty list",
        }
    missing = [str(n) for n in needles if str(n) not in text]
    ok = not missing
    return {
        "id": sid,
        "ok": ok,
        "kind": "assert_file_contains",
        "detail": "all substrings present" if ok else f"missing: {missing}",
        "path": rel,
    }


def _step_assert_tool(step: dict[str, Any], *, tools_dir: Path) -> dict[str, Any]:
    sid = str(step.get("id", "")).strip() or "assert_tool"
    name = str(step.get("tool", "")).strip()
    if not name:
        return {"id": sid, "ok": False, "kind": "assert_tool", "detail": "tool name required"}
    names = _public_tool_names(tools_dir)
    ok = name in names
    return {
        "id": sid,
        "ok": ok,
        "kind": "assert_tool",
        "detail": "exported" if ok else f"tool not found: {name}",
        "tool": name,
    }


def _step_unsupported(step: dict[str, Any]) -> dict[str, Any]:
    sid = str(step.get("id", "")).strip() or "unsupported"
    reason = str(step.get("reason", "")).strip() or "step marked unsupported"
    return {
        "id": sid,
        "ok": False,
        "kind": "unsupported",
        "detail": reason,
        "l2_gap": True,
    }


def load_playbook(
    *,
    playbook_json: str = "",
    playbook_path: str = "",
    agent_dir: Path,
    workspace_dir: Path,
) -> tuple[dict[str, Any] | None, str]:
    raw = playbook_json.strip()
    if not raw and playbook_path.strip():
        rel = playbook_path.strip()
        if ".." in Path(rel).parts:
            return None, "playbook_path must not contain .."
        candidates = [
            (agent_dir / rel).resolve(),
            (workspace_dir / rel).resolve(),
            Path(rel).resolve() if Path(rel).is_absolute() else None,
        ]
        path: Path | None = None
        for cand in candidates:
            if cand is None:
                continue
            if cand.is_file():
                # Absolute outside roots only when explicitly absolute input
                if cand.is_absolute() and Path(rel).is_absolute():
                    path = cand
                    break
                for root in (agent_dir.resolve(), workspace_dir.resolve()):
                    try:
                        cand.relative_to(root)
                        path = cand
                        break
                    except ValueError:
                        continue
                if path is not None:
                    break
        if path is None:
            return None, f"playbook not found: {rel}"
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            return None, f"cannot read playbook: {exc}"
    if not raw:
        return None, "playbook_json or playbook_path required"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"invalid playbook JSON: {exc}"
    if not isinstance(data, dict):
        return None, "playbook must be a JSON object"
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return None, "playbook.steps must be a non-empty list"
    return data, ""


def run_playbook(
    playbook: dict[str, Any],
    *,
    agent_dir: Path,
    workspace_dir: Path,
    tools_dir: Path | None = None,
) -> dict[str, Any]:
    tools_root = tools_dir or _tools_dir()
    results: list[dict[str, Any]] = []
    gaps: list[str] = []
    for raw_step in playbook.get("steps", []):
        if not isinstance(raw_step, dict):
            results.append(
                {
                    "id": "?",
                    "ok": False,
                    "kind": "invalid",
                    "detail": "step must be an object",
                }
            )
            gaps.append("invalid step object")
            continue
        kind = str(raw_step.get("kind", "")).strip()
        if kind not in _VALID_KINDS:
            results.append(
                {
                    "id": str(raw_step.get("id", "")).strip() or "?",
                    "ok": False,
                    "kind": kind or "unknown",
                    "detail": f"unsupported kind in v1: {kind!r}",
                    "l2_gap": True,
                }
            )
            gaps.append(f"unsupported kind: {kind}")
            continue
        if kind == "assert_path":
            r = _step_assert_path(raw_step, agent_dir=agent_dir, workspace_dir=workspace_dir)
        elif kind == "assert_file_contains":
            r = _step_assert_file_contains(raw_step, agent_dir=agent_dir, workspace_dir=workspace_dir)
        elif kind == "assert_tool":
            r = _step_assert_tool(raw_step, tools_dir=tools_root)
        else:
            r = _step_unsupported(raw_step)
        results.append(r)
        if r.get("l2_gap"):
            gaps.append(str(r.get("detail", "gap")))
        elif not r.get("ok"):
            gaps.append(f"{r.get('id')}: {r.get('detail')}")

    has_gap_kind = any(r.get("l2_gap") for r in results)
    only_gaps = bool(results) and all(r.get("l2_gap") for r in results)
    assert_failures = [r for r in results if not r.get("ok") and not r.get("l2_gap")]
    if only_gaps or (has_gap_kind and not assert_failures):
        # Unsupported / unknown kinds → N/A (do not claim verified)
        verdict = "l2_not_applicable"
        operable = False
        if has_gap_kind and not only_gaps:
            gaps.append("playbook contains unsupported steps; L2 incomplete")
    elif assert_failures:
        verdict = "fail"
        operable = True
    else:
        verdict = "pass"
        operable = True

    return {
        "ok": verdict == "pass",
        "verdict": verdict,
        "operable": operable,
        "playbook_id": str(playbook.get("id", "")),
        "title": str(playbook.get("title", "")),
        "steps": results,
        "gaps": gaps,
        "disclaimer": DISCLAIMER,
    }


def probe_from_method_text_only(method_text: str) -> dict[str, Any]:
    text = method_text.strip()
    return {
        "ok": False,
        "verdict": "l2_not_applicable",
        "operable": False,
        "playbook_id": "",
        "title": "",
        "steps": [],
        "gaps": [
            "v1 需要结构化 playbook_json / playbook_path;"
            "仅有自然语言「实验方法」时不自动猜步骤。" + (f" (收到 method_text {len(text)} chars)" if text else ""),
        ],
        "disclaimer": DISCLAIMER,
        "hint": ("改用 skills/poc-l2-reproduce/playbooks/ 下剧本,或把步骤写成 assert_* playbook。"),
    }


def run_probe(
    *,
    playbook_json: str = "",
    playbook_path: str = "",
    method_text: str = "",
    agent_dir: Path,
    workspace_dir: Path,
    tools_dir: Path | None = None,
) -> dict[str, Any]:
    if not playbook_json.strip() and not playbook_path.strip():
        return probe_from_method_text_only(method_text)
    playbook, err = load_playbook(
        playbook_json=playbook_json,
        playbook_path=playbook_path,
        agent_dir=agent_dir,
        workspace_dir=workspace_dir,
    )
    if playbook is None:
        return {
            "ok": False,
            "verdict": "l2_not_applicable",
            "operable": False,
            "playbook_id": "",
            "title": "",
            "steps": [],
            "gaps": [err],
            "disclaimer": DISCLAIMER,
        }
    return run_playbook(
        playbook,
        agent_dir=agent_dir,
        workspace_dir=workspace_dir,
        tools_dir=tools_dir,
    )
