from __future__ import annotations

from ._routing import select_model_for_message
from .chat_with_ustc import ROUTER_BASE_URL, ROUTER_MODEL, choose_model_via_ustc_api

__all__ = [
    "ROUTER_BASE_URL",
    "ROUTER_MODEL",
    "choose_model_via_ustc_api",
    "select_model_for_message",
]
