"""Run the experimental routing service with a local Qwen TCP topology."""

from __future__ import annotations

import anyio

from .routing import RoutingRouter, RoutingTarget


def main() -> None:
    """Start the Qwen routing demo until interrupted."""

    router = RoutingRouter(
        session_socket="http://127.0.0.1:18100",
        selector_socket="http://127.0.0.1:18101",
        targets=[
            RoutingTarget(
                candidate_id="general",
                socket="http://127.0.0.1:18102",
                description=(
                    "General conversation, explanations, translation, and summarization."
                ),
            ),
            RoutingTarget(
                candidate_id="strong-code",
                socket="http://127.0.0.1:18103",
                description=(
                    "Programming, debugging, testing, architecture, and code review."
                ),
            ),
        ],
        selector_timeout=60.0,
        target_timeout=180.0,
        verbose=False,
    )
    anyio.run(router.run)


if __name__ == "__main__":
    main()
