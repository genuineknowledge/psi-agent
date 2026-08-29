"""Minimal local entry point for Feishu Gateway (python -m psi_agent.feishu_gateway)."""

from __future__ import annotations

import argparse
import asyncio

from psi_agent.feishu_gateway import Gateway


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Feishu Gateway (Feishu Haitun backend)")
    parser.add_argument("--listen", default="http://127.0.0.1:8080")
    parser.add_argument("--socket-path", default="psi")
    parser.add_argument("--app-name", default="Haitun Agent")
    parser.add_argument("--feishu-ai-id", default="")
    parser.add_argument("--feishu-workspace-root", default="")
    parser.add_argument("--default-agent", default="")
    parser.add_argument("--default-workspace", default="")
    parser.add_argument("--appdata", default="")
    parser.add_argument("--scheduler-ai-id", default="")
    parser.add_argument("--auth-endpoint", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    asyncio.run(
        Gateway(
            listen=args.listen,
            socket_path=args.socket_path,
            app_name=args.app_name,
            feishu_ai_id=args.feishu_ai_id,
            feishu_workspace_root=args.feishu_workspace_root,
            default_agent=args.default_agent,
            default_workspace=args.default_workspace,
            appdata=args.appdata,
            scheduler_ai_id=args.scheduler_ai_id,
            auth_endpoint=args.auth_endpoint,
            verbose=args.verbose,
        ).run()
    )


if __name__ == "__main__":
    main()
