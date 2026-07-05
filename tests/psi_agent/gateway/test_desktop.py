from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway import Gateway


def test_desktop_field_exists_and_defaults_false():
    g = Gateway()
    assert g.desktop is False


def test_desktop_constructor_accepts_params():
    g = Gateway(desktop=True, browser=True, tray="/some/icon.png")
    assert g.desktop is True


@pytest.mark.anyio
async def test_desktop_flag_outputs_gateway_addr(capsys):
    g = Gateway(desktop=True, listen="http://127.0.0.1:0")

    async with anyio.create_task_group() as tg:
        tg.start_soon(g.run)
        await anyio.sleep(0.5)
        tg.cancel_scope.cancel()

    captured = capsys.readouterr()
    assert "GATEWAY_ADDR=http://127.0.0.1:" in captured.out
