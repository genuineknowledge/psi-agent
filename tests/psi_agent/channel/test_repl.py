from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientConnectorError

from psi_agent.channel.repl.client import run_repl


@pytest.mark.anyio
async def test_run_repl_eof() -> None:
    mock_prompt = AsyncMock(side_effect=EOFError)

    with (
        patch("psi_agent.channel.repl.client.ChannelCore") as mock_core_cls,
        patch("psi_agent.channel.repl.client.PromptSession") as mock_session_cls,
    ):
        mock_core_inst = AsyncMock()
        mock_core_cls.return_value.__aenter__.return_value = mock_core_inst
        mock_session_inst = AsyncMock()
        mock_session_inst.prompt_async = mock_prompt
        mock_session_cls.return_value = mock_session_inst

        await run_repl(session_socket="test.sock")
        mock_prompt.assert_called_once()


@pytest.mark.anyio
async def test_run_repl_connection_error() -> None:
    conn_key = MagicMock(host="localhost", port=80, ssl=False)
    err = ClientConnectorError(connection_key=conn_key, os_error=OSError("connection failed"))

    with patch("psi_agent.channel.repl.client.ChannelCore") as mock_core_cls:
        mock_core_cls.return_value.__aenter__.side_effect = err

        with pytest.raises(ClientConnectorError):
            await run_repl(session_socket="invalid.sock")
