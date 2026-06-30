from __future__ import annotations

import sys

from psi_agent.call import Call, _temp_channel_socket


class TestTempChannelSocket:
    def test_returns_string(self):
        result = _temp_channel_socket()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_on_linux(self):
        result = _temp_channel_socket()
        if sys.platform != "win32":
            assert result.startswith("/tmp/psi-call-")
            assert result.endswith(".sock")
        else:
            assert result.startswith(r"\\.\pipe\psi-call-")

    def test_unique_per_call(self):
        r1 = _temp_channel_socket()
        r2 = _temp_channel_socket()
        assert r1 != r2


class TestCallDataclass:
    def test_default_fields(self):
        c = Call(workspace="/ws", ai_socket="/ai.sock", message="hi")
        assert c.workspace == "/ws"
        assert c.ai_socket == "/ai.sock"
        assert c.message == "hi"
        assert c.verbose is False
        assert c.session_id is None

    def test_all_fields(self):
        c = Call(
            workspace="/ws",
            ai_socket="/ai.sock",
            message="hi",
            verbose=True,
            session_id="abc123",
        )
        assert c.verbose is True
        assert c.session_id == "abc123"
