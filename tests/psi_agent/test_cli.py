from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from psi_agent.cli import main


def test_cli_help(capsys):
    with patch.object(sys, 'argv', ['psi-agent', '--help']):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "Launch components defined in a YAML config file." in captured.out
    assert "ai" in captured.out
    assert "session" in captured.out
    assert "channel" in captured.out

def test_cli_ai_help(capsys):
    with patch.object(sys, 'argv', ['psi-agent', 'ai', '--help']):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "session-socket" in captured.out
    assert "provider" in captured.out

def test_cli_session_help(capsys):
    with patch.object(sys, 'argv', ['psi-agent', 'session', '--help']):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "workspace" in captured.out
    assert "channel-socket" in captured.out
    assert "ai-socket" in captured.out

def test_cli_channel_repl_help(capsys):
    with patch.object(sys, 'argv', ['psi-agent', 'channel', 'repl', '--help']):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "session-socket" in captured.out

def test_cli_channel_cli_help(capsys):
    with patch.object(sys, 'argv', ['psi-agent', 'channel', 'cli', '--help']):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "session-socket" in captured.out
    assert "message" in captured.out
