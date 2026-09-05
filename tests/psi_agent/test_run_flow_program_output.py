from __future__ import annotations

import base64
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "agents" / "feishu" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_run_flow: Any = importlib.import_module("run_flow")

_NON_UTF8_GBK = b"\xb2\xe2\xca\xd4"  # "测试" encoded as GBK; invalid UTF-8.


def _invocation() -> SimpleNamespace:
    return SimpleNamespace(
        binding_name="work_step",
        output_ids=("out",),
        dispatch=SimpleNamespace(iteration_index=None, invocation_id=""),
    )


def _result(*, exit_code: int, stdout: bytes, stderr: bytes) -> Any:
    return _run_flow._ProgramProcessResult(
        argv=("worker",),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def _program_error(outputs: dict[str, object]) -> dict[str, Any]:
    value: Any = outputs["out"]
    return value[_run_flow._PROGRAM_ERROR_KEY]


def test_program_result_allows_non_utf8_stderr_on_success() -> None:
    outputs = _run_flow._program_result_outputs(
        _invocation(),
        [_result(exit_code=0, stdout=b"ok", stderr=_NON_UTF8_GBK)],
    )

    assert outputs == {"out": "ok"}


def test_program_result_still_rejects_non_utf8_stdout() -> None:
    outputs = _run_flow._program_result_outputs(
        _invocation(),
        [_result(exit_code=0, stdout=b"\xff", stderr=b"")],
    )

    error = _program_error(outputs)
    assert error["phase"] == "output_format"
    assert error["kind"] == "invalid_utf8"
    assert error["message"] == "Program stdout must be valid UTF-8 text."
    attempt = error["attempts"][0]
    assert attempt["stdout"] is None
    assert attempt["stdout_base64"] == base64.b64encode(b"\xff").decode("ascii")


def test_nonzero_exit_with_non_utf8_stderr_is_execution_error() -> None:
    outputs = _run_flow._program_result_outputs(
        _invocation(),
        [_result(exit_code=7, stdout=b"", stderr=_NON_UTF8_GBK)],
    )

    error = _program_error(outputs)
    assert error["phase"] == "execution"
    assert error["kind"] == "nonzero_exit"
    assert error["message"] == "Program exited with code 7."
    attempt = error["attempts"][0]
    assert attempt["stderr_base64"] == base64.b64encode(_NON_UTF8_GBK).decode("ascii")


def test_program_diagnostic_prefers_declared_locale_and_keeps_raw_bytes(monkeypatch: Any) -> None:
    monkeypatch.setattr(_run_flow.locale, "getencoding", lambda: "cp936")

    payload = _run_flow._program_attempt_payload(_result(exit_code=0, stdout=b"ok", stderr=_NON_UTF8_GBK))

    assert payload["stderr"] == "测试"
    assert payload["stderr_encoding"] == "cp936"
    assert payload["stderr_base64"] == base64.b64encode(_NON_UTF8_GBK).decode("ascii")


def test_program_diagnostic_uses_reversible_fallback_when_encoding_is_unknown(monkeypatch: Any) -> None:
    monkeypatch.setattr(_run_flow.locale, "getencoding", lambda: "ascii")
    monkeypatch.setattr(_run_flow.os, "name", "posix")
    raw = b"warning:\xff"

    payload = _run_flow._program_attempt_payload(_result(exit_code=0, stdout=b"ok", stderr=raw))

    assert payload["stderr"] == r"warning:\xff"
    assert payload["stderr_encoding"] == "utf-8/backslashreplace"
    assert payload["stderr_base64"] == base64.b64encode(raw).decode("ascii")
