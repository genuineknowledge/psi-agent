"""Shared public-request copying for Router strategies."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def copy_public_request_body(*, body: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy public completion fields and force streaming upstream."""

    forwarded = {key: deepcopy(value) for key, value in body.items() if key not in {"model", "routing"}}
    forwarded["stream"] = True
    return forwarded
