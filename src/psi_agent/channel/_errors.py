"""Channel-layer exception hierarchy."""

from __future__ import annotations


class ChannelError(Exception):
    """Base for all channel-layer errors (transport, protocol, session)."""
