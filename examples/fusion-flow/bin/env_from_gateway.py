#!/usr/bin/env python3
"""Generate the Fusion Flow psi-engine .env from a gateway's saved AI config.

You already typed provider / model / api_key / base_url into the gateway Web
Console; the gateway persists them to AppData ``state/latest.json`` (platformdirs
``user_data_dir`` / ``PSI_APP_DATA_ROOT``) under ``ais[]`` (fields: provider, model,
api_key, base_url). This script reads that entry and renders the shim ``.env`` so
you never hand-copy the key.

The AI backend forwards ``provider``/``model`` verbatim to any-llm
(``acompletion(provider=..., model=...)``), so the gateway's ``provider`` value
(e.g. "deepseek") is used as-is for ``FLOW_PSI_AI`` — no openai remapping.

Usage:
    python bin/env_from_gateway.py \\
        --state <AppData>/state/latest.json \\
        --executor-workspace <abs path to FLOW_PSI_WORKSPACE> \\
        [--ai-id <id>] [--out <path/to/.env>] [--psi-cmd "uv run --no-sync --project <repo> psi-agent"]

If --out is omitted the .env text is printed to stdout (redirect it yourself).
By default it picks the first AI entry; use --ai-id to select a specific one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# The shim lives next to this file; its absolute path goes into FLOW_PSI_COMMAND_ARGS.
SHIM_PATH = (Path(__file__).resolve().parent / "session_shim.py").as_posix()


def _pick_ai(ais: list[dict], ai_id: str | None) -> dict:
    if not ais:
        raise SystemExit("[env-from-gateway] no AI configured in state (ais[] is empty)")
    if ai_id:
        for a in ais:
            if a.get("id") == ai_id:
                return a
        raise SystemExit(f"[env-from-gateway] no AI with id={ai_id!r}; have: {[a.get('id') for a in ais]}")
    return ais[0]


def _render(ai: dict, executor_workspace: str, psi_cmd: str, state_dir: str) -> str:
    # gateway stores base_url with a trailing /v1 for openai-compatible upstreams;
    # keep it verbatim — any-llm + the provider decide how to use it.
    python_exe = "python" if os.name == "nt" else "python3"
    lines = [
        "# Auto-generated from gateway state by bin/env_from_gateway.py — do not hand-edit the key.",
        "# Re-run that script to refresh after changing the AI in the gateway.",
        "FLOW_ENGINE=psi",
        f"FLOW_PSI_WORKSPACE={executor_workspace}",
        "",
        "# route the bundle's old-style `psi-agent run` through the shim (see README.stateful.md)",
        f"FLOW_PSI_COMMAND={python_exe}",
        f"FLOW_PSI_COMMAND_ARGS={SHIM_PATH}",
        f"PSI_CMD={psi_cmd}",
        f"FUSION_SHIM_STATE_DIR={state_dir}",
        "",
        "# AI backend params copied from the gateway (provider forwarded verbatim to any-llm)",
        f"FLOW_PSI_AI={ai.get('provider', '')}",
        f"FLOW_PSI_MODEL={ai.get('model', '')}",
        f"FLOW_PSI_BASE_URL={ai.get('base_url', '')}",
        f"FLOW_PSI_API_KEY={ai.get('api_key', '')}",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Render Fusion Flow psi .env from gateway state.")
    p.add_argument("--state", required=True, help="path to gateway state/latest.json")
    p.add_argument("--executor-workspace", required=True, help="abs path for FLOW_PSI_WORKSPACE")
    p.add_argument("--ai-id", default=None, help="pick a specific AI id (default: first entry)")
    p.add_argument("--out", default=None, help="write .env here (default: stdout)")
    p.add_argument(
        "--psi-cmd",
        default=os.environ.get("PSI_CMD", "uv run --no-sync psi-agent"),
        help="command prefix the shim uses to start psi-agent",
    )
    p.add_argument(
        "--state-dir",
        default=None,
        help="FUSION_SHIM_STATE_DIR (default: OS temp / fusion-shim-run)",
    )
    args = p.parse_args(argv)

    state_path = Path(args.state)
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except OSError as e:
        raise SystemExit(f"[env-from-gateway] cannot read state {state_path}: {e}") from e
    except json.JSONDecodeError as e:
        raise SystemExit(f"[env-from-gateway] state is not valid JSON: {e}") from e

    ai = _pick_ai(data.get("ais", []), args.ai_id)

    if args.state_dir:
        state_dir = args.state_dir
    else:
        base = os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp"
        state_dir = Path(base, "fusion-shim-run").as_posix()

    text = _render(ai, args.executor_workspace, args.psi_cmd, state_dir)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        # never echo the key
        sys.stderr.write(
            f"[env-from-gateway] wrote {args.out} (provider={ai.get('provider')!r}, model={ai.get('model')!r})\n"
        )
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
