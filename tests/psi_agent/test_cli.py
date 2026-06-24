from __future__ import annotations

from unittest.mock import MagicMock, patch

import tyro

from psi_agent.ai import Ai
from psi_agent.cli import main
from psi_agent.session import Session


def test_cli_parsing_ai_subcommand() -> None:
    """Test that 'ai' subcommand is parsed correctly."""
    with patch("tyro.cli") as mock_cli:
        mock_cli.return_value = MagicMock(spec=Ai)
        with patch("anyio.run"):
            main()
            mock_cli.assert_called_once()


def test_cli_parsing_session_subcommand() -> None:
    """Test that 'session' subcommand is parsed correctly."""
    # tyro.cli returns the command instance, whose .run() is then called by anyio.run()
    args = ["session", "--workspace", "/tmp", "--channel-socket", "c.sock", "--ai-socket", "ai.sock"]
    with patch("sys.argv", ["psi-agent", *args]):
        cmd = tyro.cli(Ai | Session)
        assert isinstance(cmd, Session)
        assert cmd.workspace == "/tmp"
        assert cmd.channel_socket == "c.sock"
        assert cmd.ai_socket == "ai.sock"


def test_cli_parsing_ai_full_args() -> None:
    """Test 'ai' subcommand with all optional arguments."""
    args = [
        "ai",
        "--session-socket",
        "ai.sock",
        "--provider",
        "openai",
        "--model",
        "gpt-4",
        "--api-key",
        "sk-...",
        "--base-url",
        "https://api.example.com",
        "--verbose",
    ]
    with patch("sys.argv", ["psi-agent", *args]):
        cmd = tyro.cli(Ai | Session)
        assert isinstance(cmd, Ai)
        assert cmd.session_socket == "ai.sock"
        assert cmd.provider == "openai"
        assert cmd.model == "gpt-4"
        assert cmd.api_key == "sk-..."
        assert cmd.base_url == "https://api.example.com"
        assert cmd.verbose is True
