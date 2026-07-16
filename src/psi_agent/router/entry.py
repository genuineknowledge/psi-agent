from __future__ import annotations

import tyro

from .server import Router


def parse_router_argv(argv: list[str]) -> Router | None:
    if len(argv) < 2 or argv[0] != "ai" or argv[1] != "router":
        return None
    return tyro.cli(Router, args=argv[2:])


__all__ = ["Router", "parse_router_argv"]
