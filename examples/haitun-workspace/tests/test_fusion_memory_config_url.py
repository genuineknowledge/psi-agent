"""URL policy for FUSION_MEMORY_MCP_URL — private-LAN plaintext relaxation.

``validate_mcp_url`` accepts https anywhere, http on loopback, and (this change)
http on RFC1918 private-network IPs so a same-LAN Fusion Memory deployment
without public TLS is reachable. Public-host http stays rejected.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import multiprocessing
import os
import stat
import sys
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = WORKSPACE_ROOT.parents[1]
CONFIG_PATHS = (
    WORKSPACE_ROOT / "tools" / "_fusion_memory_config.py",
    REPOSITORY_ROOT / "examples" / "fusion-memory-workspace" / "tools" / "_fusion_memory_config.py",
)


def _load(path: Path, prefix: str) -> Any:
    name = f"{prefix}_{hashlib.sha256(os.urandom(16)).hexdigest()}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _write_token_entries_worker(
    config_path: str,
    token_map_path: str,
    prefix: str,
    count: int,
    start_event: Any,
) -> None:
    module = _load(Path(config_path), f"fusion_memory_writer_{os.getpid()}")
    start_event.wait()
    for index in range(count):
        module._write_token_map_entry_sync(
            token_map_path,
            f"{prefix}_{index}",
            f"token-{prefix}-{index}",
            "workspace-a",
        )


def _write_same_user_worker(
    config_path: str,
    token_map_path: str,
    token: str,
    start_event: Any,
    results: Any,
) -> None:
    module = _load(Path(config_path), f"fusion_memory_same_user_writer_{os.getpid()}")
    start_event.wait()
    results.put(
        module._write_token_map_entry_sync(
            token_map_path,
            "ou_shared",
            token,
            "workspace-a",
        )
    )


@pytest.fixture(params=CONFIG_PATHS, ids=("haitun", "fusion-memory"))
def config_module(request: pytest.FixtureRequest) -> Any:
    return _load(request.param, "fusion_memory_config")


@pytest.mark.parametrize(
    "url",
    [
        "https://memory.example.com/mcp",
        "http://localhost:9000/mcp",
        "http://127.0.0.1/mcp",
        "http://[::1]/mcp",
        # RFC1918 private-network hosts over plain http (same-LAN deployment).
        "http://192.168.63.71:8700/mcp",
        "http://10.0.0.5/mcp",
        "http://172.16.0.9:8080/mcp",
    ],
)
def test_validate_mcp_url_accepts_secure_and_trusted_plaintext(config_module: Any, url: str) -> None:
    assert config_module.validate_mcp_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        # Public host over plain http — still rejected.
        "http://memory.example.com/mcp",
        "http://8.8.8.8/mcp",
        # Exact-path / credential / query / fragment rules unaffected.
        "https://memory.example.com/other",
        "https://memory.example.com/mcp/",
        "https://user:pass@memory.example.com/mcp",
        "https://memory.example.com/mcp?x=1",
        "https://memory.example.com/mcp#frag",
    ],
)
def test_validate_mcp_url_rejects_untrusted_or_malformed(config_module: Any, url: str) -> None:
    with pytest.raises(ValueError):
        config_module.validate_mcp_url(url)


def test_validate_mcp_url_empty_returns_empty(config_module: Any) -> None:
    assert config_module.validate_mcp_url("") == ""
    assert config_module.validate_mcp_url("   ") == ""


def test_trusted_plaintext_host_predicate(config_module: Any) -> None:
    trusted = config_module._is_trusted_plaintext_host
    assert trusted("localhost") is True
    assert trusted("127.0.0.1") is True
    assert trusted("192.168.1.1") is True
    assert trusted("10.1.2.3") is True
    assert trusted("172.31.255.255") is True
    assert trusted("8.8.8.8") is False
    assert trusted("memory.example.com") is False


async def test_auto_registration_creates_appdata_token_map(
    config_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    calls: list[str] = []

    async def register(**kwargs: object) -> dict[str, object]:
        calls.append(str(kwargs["feishu_open_id"]))
        return {"ok": True, "result": {"token": "issued-token"}}

    monkeypatch.setattr(config_module, "_register_feishu_user", register)
    config = config_module.build_memory_config(
        {
            "FUSION_MEMORY_MCP_URL": "https://memory.example.com/mcp",
            "FUSION_MEMORY_AUTO_REGISTER_FEISHU": "true",
            "FUSION_MEMORY_ORGANIZATION_ID": "org-a",
            "PSI_FEISHU_APP_ID": "cli-a",
            "PSI_FEISHU_APP_SECRET": "secret-a",
        }
    )

    resolved = await config_module.resolve_memory_config("feishu-ou_a", config)

    token_map = appdata / "fusion-memory" / "tokens.json"
    assert resolved.token == "issued-token"
    assert calls == ["ou_a"]
    assert json.loads(token_map.read_text(encoding="utf-8"))["ou_a"]["token"] == "issued-token"
    assert stat.S_IMODE(token_map.stat().st_mode) == 0o600


async def test_auto_registration_classifies_appdata_resolution_failure(
    config_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_appdata_resolution() -> str:
        raise OSError("secret path detail")

    monkeypatch.setattr(config_module, "resolve_appdata_root", reject_appdata_resolution)
    config = config_module.build_memory_config(
        {
            "FUSION_MEMORY_MCP_URL": "https://memory.example.com/mcp",
            "FUSION_MEMORY_AUTO_REGISTER_FEISHU": "true",
            "FUSION_MEMORY_ORGANIZATION_ID": "org-a",
            "PSI_FEISHU_APP_ID": "cli-a",
            "PSI_FEISHU_APP_SECRET": "secret-a",
        }
    )

    with pytest.raises(config_module.MemoryConfigError) as raised:
        await config_module.resolve_memory_config("feishu-ou_a", config)

    assert raised.value.code == "token_map_unavailable"
    assert "secret path detail" not in str(raised.value)


async def test_auto_registration_creates_explicit_missing_token_map(
    config_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_map = tmp_path / "secrets" / "tokens.json"

    async def register(**_kwargs: object) -> dict[str, object]:
        return {"ok": True, "result": {"token": "issued-token"}}

    monkeypatch.setattr(config_module, "_register_feishu_user", register)
    config = config_module.build_memory_config(
        {
            "FUSION_MEMORY_MCP_URL": "https://memory.example.com/mcp",
            "FUSION_MEMORY_TOKEN_MAP_FILE": str(token_map),
            "FUSION_MEMORY_AUTO_REGISTER_FEISHU": "true",
            "FUSION_MEMORY_ORGANIZATION_ID": "org-a",
            "PSI_FEISHU_APP_ID": "cli-a",
            "PSI_FEISHU_APP_SECRET": "secret-a",
        }
    )

    resolved = await config_module.resolve_memory_config("feishu-ou_a", config)

    assert resolved.token == "issued-token"
    assert json.loads(token_map.read_text(encoding="utf-8"))["ou_a"]["token"] == "issued-token"


async def test_refresh_registration_replaces_stale_token(
    config_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_map = tmp_path / "tokens.json"
    token_map.write_text(json.dumps({"ou_a": {"token": "stale-token"}}), encoding="utf-8")

    async def register(**_kwargs: object) -> dict[str, object]:
        return {"ok": True, "result": {"token": "fresh-token"}}

    monkeypatch.setattr(config_module, "_register_feishu_user", register)
    config = config_module.build_memory_config(
        {
            "FUSION_MEMORY_MCP_URL": "https://memory.example.com/mcp",
            "FUSION_MEMORY_TOKEN_MAP_FILE": str(token_map),
            "FUSION_MEMORY_AUTO_REGISTER_FEISHU": "true",
            "FUSION_MEMORY_ORGANIZATION_ID": "org-a",
            "PSI_FEISHU_APP_ID": "cli-a",
            "PSI_FEISHU_APP_SECRET": "secret-a",
        }
    )

    resolved = await config_module.refresh_feishu_registration("feishu-ou_a", config)

    assert resolved.token == "fresh-token"
    assert json.loads(token_map.read_text(encoding="utf-8"))["ou_a"]["token"] == "fresh-token"


@pytest.mark.parametrize(
    ("status_code", "expected_code", "expected_retryable"),
    [
        (400, "registration_rejected", False),
        (408, "registration_unavailable", True),
        (429, "registration_unavailable", True),
        (503, "registration_unavailable", True),
    ],
)
async def test_registration_http_failure_is_safely_classified(
    config_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_code: str,
    expected_retryable: bool,
) -> None:
    token_map = tmp_path / "tokens.json"
    token_map.write_text("{}", encoding="utf-8")

    async def reject(**_kwargs: object) -> dict[str, object]:
        request = httpx.Request("POST", "https://memory.example.com/feishu/register")
        response = httpx.Response(status_code, text="secret-value", request=request)
        raise httpx.HTTPStatusError("secret-value", request=request, response=response)

    monkeypatch.setattr(config_module, "_register_feishu_user", reject)
    config = config_module.build_memory_config(
        {
            "FUSION_MEMORY_MCP_URL": "https://memory.example.com/mcp",
            "FUSION_MEMORY_TOKEN_MAP_FILE": str(token_map),
            "FUSION_MEMORY_AUTO_REGISTER_FEISHU": "true",
            "FUSION_MEMORY_ORGANIZATION_ID": "org-a",
            "PSI_FEISHU_APP_ID": "cli-a",
            "PSI_FEISHU_APP_SECRET": "secret-a",
        }
    )

    with pytest.raises(config_module.MemoryConfigError) as raised:
        await config_module.resolve_memory_config("feishu-ou_a", config)

    assert raised.value.code == expected_code
    assert raised.value.retryable is expected_retryable
    assert "secret-value" not in str(raised.value)


async def test_registration_rejects_malformed_success_response_safely(
    config_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_map = tmp_path / "tokens.json"
    token_map.write_text("{}", encoding="utf-8")

    async def malformed(**_kwargs: object) -> dict[str, object]:
        raise json.JSONDecodeError("secret response detail", "not-json", 0)

    monkeypatch.setattr(config_module, "_register_feishu_user", malformed)
    config = config_module.build_memory_config(
        {
            "FUSION_MEMORY_MCP_URL": "https://memory.example.com/mcp",
            "FUSION_MEMORY_TOKEN_MAP_FILE": str(token_map),
            "FUSION_MEMORY_AUTO_REGISTER_FEISHU": "true",
            "FUSION_MEMORY_ORGANIZATION_ID": "org-a",
            "PSI_FEISHU_APP_ID": "cli-a",
            "PSI_FEISHU_APP_SECRET": "secret-a",
        }
    )

    with pytest.raises(config_module.MemoryConfigError) as raised:
        await config_module.resolve_memory_config("feishu-ou_a", config)

    assert raised.value.code == "registration_failed"
    assert "secret response detail" not in str(raised.value)


async def test_registration_classifies_token_map_write_failure(
    config_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_map = tmp_path / "tokens.json"
    token_map.write_text("{}", encoding="utf-8")

    async def register(**_kwargs: object) -> dict[str, object]:
        return {"ok": True, "result": {"token": "issued-token"}}

    async def reject_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("secret path detail")

    monkeypatch.setattr(config_module, "_register_feishu_user", register)
    monkeypatch.setattr(config_module, "_write_token_map_entry", reject_write)
    config = config_module.build_memory_config(
        {
            "FUSION_MEMORY_MCP_URL": "https://memory.example.com/mcp",
            "FUSION_MEMORY_TOKEN_MAP_FILE": str(token_map),
            "FUSION_MEMORY_AUTO_REGISTER_FEISHU": "true",
            "FUSION_MEMORY_ORGANIZATION_ID": "org-a",
            "PSI_FEISHU_APP_ID": "cli-a",
            "PSI_FEISHU_APP_SECRET": "secret-a",
        }
    )

    with pytest.raises(config_module.MemoryConfigError) as raised:
        await config_module.resolve_memory_config("feishu-ou_a", config)

    assert raised.value.code == "token_map_unavailable"
    assert "secret path detail" not in str(raised.value)


def test_token_map_updates_are_process_safe(config_module: Any, tmp_path: Path) -> None:
    token_map = tmp_path / "tokens.json"
    token_map.write_text("{}", encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    processes = [
        context.Process(
            target=_write_token_entries_worker,
            args=(str(Path(config_module.__file__)), str(token_map), f"writer{index}", 30, start_event),
        )
        for index in range(4)
    ]

    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=30)

    assert [process.exitcode for process in processes] == [0, 0, 0, 0]
    payload = json.loads(token_map.read_text(encoding="utf-8"))
    assert len(payload) == 120
    assert set(payload) == {f"writer{writer}_{index}" for writer in range(4) for index in range(30)}


def test_concurrent_same_user_writers_return_committed_token(config_module: Any, tmp_path: Path) -> None:
    token_map = tmp_path / "tokens.json"
    token_map.write_text("{}", encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_write_same_user_worker,
            args=(str(Path(config_module.__file__)), str(token_map), f"token-{index}", start_event, results),
        )
        for index in range(4)
    ]

    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=30)

    assert [process.exitcode for process in processes] == [0, 0, 0, 0]
    committed = json.loads(token_map.read_text(encoding="utf-8"))["ou_shared"]
    returned = [results.get(timeout=5) for _process in processes]
    assert returned == [committed, committed, committed, committed]


async def test_token_map_atomic_replace_failure_preserves_existing_file(
    config_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_map = tmp_path / "tokens.json"
    original = '{"ou_existing":{"token":"existing-token","workspace_id":"workspace-a"}}\n'
    token_map.write_text(original, encoding="utf-8")

    def reject_replace(_source: str, _destination: str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(config_module.os, "replace", reject_replace)

    with pytest.raises(OSError, match="replace failed"):
        await config_module._write_token_map_entry(
            str(token_map),
            "ou_new",
            "new-token",
            "workspace-a",
        )

    assert token_map.read_text(encoding="utf-8") == original
    assert [path async for path in anyio.Path(str(tmp_path)).glob("*.tmp")] == []
