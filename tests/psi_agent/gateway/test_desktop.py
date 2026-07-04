from __future__ import annotations

import socket

import anyio
import pytest

from psi_agent.gateway import Gateway


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def test_desktop_field_exists_and_defaults_false():
    g = Gateway()
    assert g.desktop is False


def test_desktop_constructor_accepts_params():
    g = Gateway(desktop=True, browser=True, tray="/some/icon.png")
    assert g.desktop is True


@pytest.mark.anyio
async def test_desktop_flag_outputs_gateway_addr(capsys):
    port = _free_port()
    g = Gateway(desktop=True, listen=f"http://127.0.0.1:{port}")

    async with anyio.create_task_group() as tg:
        tg.start_soon(g.run)
        await anyio.sleep(0.5)
        tg.cancel_scope.cancel()

    captured = capsys.readouterr()
    assert f"GATEWAY_ADDR=http://127.0.0.1:{port}" in captured.out
