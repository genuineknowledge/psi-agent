from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import aiohttp
import anyio
from oracle import evaluate_response, extract_source

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_CASES = os.path.join(_HERE, "cases.json")
_SYSTEM_PROMPT = os.path.join(_HERE, "system_prompt.py")
_SKILL = os.path.join(
    _ROOT,
    "examples",
    "haitun-workspace",
    "skills",
    "fusion-flow-next",
    "SKILL.md",
)
_GRAMMAR = os.path.join(
    _ROOT,
    "examples",
    "haitun-workspace",
    "skills",
    "fusion-flow-next",
    "grammar",
    "FusionFlow.g4",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _write_json(path: str, value: object) -> None:
    await anyio.Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def _copy(source: str, destination: str) -> bytes:
    content = await anyio.Path(source).read_bytes()
    await anyio.Path(destination).write_bytes(content)
    return content


async def _drain(stream: Any, path: str) -> None:
    if stream is None:
        return
    async with await anyio.open_file(path, "wb") as output:
        async for chunk in stream:
            await output.write(chunk)


async def _stop_process(process: Any) -> int | None:
    with anyio.CancelScope(shield=True):
        if process.returncode is None:
            process.terminate()
        try:
            with anyio.fail_after(10):
                return await process.wait()
        except TimeoutError:
            process.kill()
            return await process.wait()


async def _wait_for_port(port: int, process: Any) -> None:
    with anyio.fail_after(30):
        while True:
            if process.returncode is not None:
                raise RuntimeError(f"service exited during startup with code {process.returncode}")
            try:
                stream = await anyio.connect_tcp("127.0.0.1", port)
            except OSError:
                await anyio.sleep(0.1)
            else:
                await stream.aclose()
                return


def _parse_sse(raw: bytes) -> tuple[str, str, bool]:
    content: list[str] = []
    reasoning: list[str] = []
    stream_error = False
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            stream_error = True
            continue
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            continue
        choice = choices[0]
        delta = choice.get("delta")
        if isinstance(delta, dict):
            if isinstance(delta.get("content"), str):
                content.append(delta["content"])
            if isinstance(delta.get("reasoning"), str):
                reasoning.append(delta["reasoning"])
        stream_error |= choice.get("finish_reason") == "error"
    return "".join(content), "".join(reasoning), stream_error


async def _run_sample(
    *,
    label: str,
    case: dict[str, Any],
    repetition: int,
    settings: dict[str, Any],
    run_dir: str,
    inputs_dir: str,
    ai_url: str,
) -> dict[str, Any]:
    case_id = case["id"]
    sample_id = f"{label}-{case_id}-r{repetition}-{uuid.uuid4().hex[:8]}"
    sample_dir = os.path.join(run_dir, "samples", case_id, f"r{repetition}")
    workspace = os.path.join(sample_dir, "workspace")
    systems = os.path.join(workspace, "systems")
    await anyio.Path(systems).mkdir(parents=True)
    await _copy(_SYSTEM_PROMPT, os.path.join(systems, "system.py"))
    request_body = {
        "model": settings["model"],
        "messages": [{"role": "user", "content": case["prompt"]}],
        "temperature": settings["temperature"],
        "max_tokens": settings["max_tokens"],
        "stream": True,
    }
    await _write_json(os.path.join(sample_dir, "request.json"), request_body)

    session_port = _free_port()
    session_url = f"http://127.0.0.1:{session_port}"
    command = [
        sys.executable,
        "-m",
        "psi_agent.cli",
        "session",
        "--ai-socket",
        ai_url,
        "--channel-socket",
        session_url,
        "--workspace",
        workspace,
        "--session-id",
        sample_id,
    ]
    session_env = os.environ.copy()
    session_env["PSI_SELECT_EVAL_INPUTS"] = inputs_dir
    session_env.pop("PSI_AI_API_KEY", None)
    started_at = _now()
    start = time.monotonic()
    raw = b""
    http_status: int | None = None
    error: str | None = None
    process = await anyio.open_process(
        command,
        cwd=_ROOT,
        env=session_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    async with anyio.create_task_group() as log_tasks:
        log_tasks.start_soon(_drain, process.stdout, os.path.join(sample_dir, "session.log"))
        try:
            await _wait_for_port(session_port, process)
            timeout = aiohttp.ClientTimeout(total=1800)
            async with (
                aiohttp.ClientSession(timeout=timeout) as client,
                client.post(f"{session_url}/chat/completions", json=request_body) as response,
            ):
                http_status = response.status
                raw = await response.read()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            process_returncode = await _stop_process(process)

    final, reasoning, stream_error = _parse_sse(raw)
    await anyio.Path(os.path.join(sample_dir, "raw.sse")).write_bytes(raw)
    await anyio.Path(os.path.join(sample_dir, "final.md")).write_text(final, encoding="utf-8")
    await anyio.Path(os.path.join(sample_dir, "reasoning.md")).write_text(reasoning, encoding="utf-8")
    await anyio.Path(os.path.join(sample_dir, "source.ff")).write_text(
        extract_source(final) or "",
        encoding="utf-8",
    )
    history_source = os.path.join(workspace, "histories", f"{sample_id}.jsonl")
    if await anyio.Path(history_source).exists():
        await _copy(history_source, os.path.join(sample_dir, "history.jsonl"))

    oracle = evaluate_response(case, final)
    if error is not None or http_status != 200 or stream_error:
        oracle["passed"] = False
        oracle["diagnostics"]["infrastructure"] = error or f"http_status={http_status}, stream_error={stream_error}"
    await _write_json(os.path.join(sample_dir, "result.json"), oracle)
    metadata = {
        "case_id": case_id,
        "label": label,
        "repetition": repetition,
        "session_id": sample_id,
        "session_port": session_port,
        "started_at": started_at,
        "ended_at": _now(),
        "duration_seconds": round(time.monotonic() - start, 3),
        "http_status": http_status,
        "stream_error": stream_error,
        "process_returncode": process_returncode,
        "error": error,
    }
    await _write_json(os.path.join(sample_dir, "metadata.json"), metadata)
    return {
        "case_id": case_id,
        "repetition": repetition,
        "passed": oracle["passed"],
        "sample": f"samples/{case_id}/r{repetition}",
    }


async def _freeze_inputs(inputs_dir: str, settings: dict[str, Any]) -> dict[str, Any]:
    await anyio.Path(inputs_dir).mkdir(parents=True)
    files = {
        "SKILL.md": _SKILL,
        "FusionFlow.g4": _GRAMMAR,
        "cases.json": _CASES,
        "system_prompt.py": _SYSTEM_PROMPT,
    }
    hashes: dict[str, str] = {}
    for name, source in files.items():
        content = await _copy(source, os.path.join(inputs_dir, name))
        hashes[name] = hashlib.sha256(content).hexdigest()
    revision = await anyio.run_process(
        ["git", "rev-parse", "HEAD"],
        cwd=_ROOT,
        check=True,
    )
    head = revision.stdout.decode().strip()
    await anyio.Path(os.path.join(inputs_dir, "git-head.txt")).write_text(head + "\n", encoding="utf-8")
    manifest = {
        "created_at": _now(),
        "git_head": head,
        "sha256": hashes,
        "settings": settings,
        "python": sys.version,
    }
    await _write_json(os.path.join(inputs_dir, "manifest.json"), manifest)
    return manifest


async def run(label: str) -> None:
    run_dir = os.path.join(_HERE, "runs", label)
    if await anyio.Path(run_dir).exists():
        raise FileExistsError(f"refusing to overwrite existing run: {run_dir}")
    await anyio.Path(run_dir).mkdir(parents=True)

    cases_document = json.loads(await anyio.Path(_CASES).read_text(encoding="utf-8"))
    settings = cases_document["global"]
    inputs_dir = os.path.join(run_dir, "inputs")
    manifest = await _freeze_inputs(inputs_dir, settings)

    key_file = os.environ.get("DEEPSEEK_KEY_FILE")
    if not key_file:
        raise RuntimeError("DEEPSEEK_KEY_FILE is required")
    api_key = (await anyio.Path(key_file).read_text(encoding="utf-8")).strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_KEY_FILE points to an empty file")

    ai_port = _free_port()
    ai_url = f"http://127.0.0.1:{ai_port}"
    ai_env = os.environ.copy()
    ai_env["PSI_AI_API_KEY"] = api_key
    ai_command = [
        sys.executable,
        "-m",
        "psi_agent.cli",
        "ai",
        "--session-socket",
        ai_url,
        "--provider",
        settings["provider"],
        "--model",
        settings["model"],
        "--base-url",
        settings["base_url"],
    ]
    ai_process = await anyio.open_process(
        ai_command,
        cwd=_ROOT,
        env=ai_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    samples: list[dict[str, Any]] = []
    async with anyio.create_task_group() as ai_tasks:
        ai_tasks.start_soon(_drain, ai_process.stdout, os.path.join(run_dir, "ai.log"))
        try:
            await _wait_for_port(ai_port, ai_process)
            limiter = anyio.CapacityLimiter(4)

            async def execute(case: dict[str, Any], repetition: int) -> None:
                async with limiter:
                    sample = await _run_sample(
                        label=label,
                        case=case,
                        repetition=repetition,
                        settings=settings,
                        run_dir=run_dir,
                        inputs_dir=inputs_dir,
                        ai_url=ai_url,
                    )
                    samples.append(sample)
                    sys.stdout.write(
                        f"{label} {sample['case_id']} r{sample['repetition']}: "
                        f"{'PASS' if sample['passed'] else 'FAIL'}\n"
                    )
                    sys.stdout.flush()

            async with anyio.create_task_group() as sample_tasks:
                for case in cases_document["cases"]:
                    for repetition in range(1, settings["repetitions"] + 1):
                        sample_tasks.start_soon(execute, case, repetition)
        finally:
            ai_returncode = await _stop_process(ai_process)

    samples.sort(key=lambda item: (item["case_id"], item["repetition"]))
    summary = {
        "label": label,
        "manifest": manifest,
        "ai_returncode": ai_returncode,
        "sample_count": len(samples),
        "passed": sum(bool(sample["passed"]) for sample in samples),
        "failed": sum(not sample["passed"] for sample in samples),
        "samples": samples,
    }
    await _write_json(os.path.join(run_dir, "summary.json"), summary)
    sys.stdout.write(f"{label}: {summary['passed']}/{summary['sample_count']} samples passed\n")


async def rescore(label: str) -> None:
    run_dir = os.path.join(_HERE, "runs", label)
    summary_path = os.path.join(run_dir, "summary.json")
    initial_summary_path = os.path.join(run_dir, "summary.initial.json")
    if not await anyio.Path(summary_path).exists():
        raise FileNotFoundError(f"run does not exist: {run_dir}")
    if await anyio.Path(initial_summary_path).exists():
        raise FileExistsError(f"refusing to rescore twice: {run_dir}")

    initial_summary = json.loads(await anyio.Path(summary_path).read_text(encoding="utf-8"))
    await _copy(summary_path, initial_summary_path)
    cases_document = json.loads(
        await anyio.Path(os.path.join(run_dir, "inputs", "cases.json")).read_text(encoding="utf-8")
    )
    samples: list[dict[str, Any]] = []
    for case in cases_document["cases"]:
        for repetition in range(1, cases_document["global"]["repetitions"] + 1):
            sample_dir = os.path.join(run_dir, "samples", case["id"], f"r{repetition}")
            result_path = os.path.join(sample_dir, "result.json")
            await _copy(result_path, os.path.join(sample_dir, "result.initial.json"))
            final = await anyio.Path(os.path.join(sample_dir, "final.md")).read_text(encoding="utf-8")
            result = evaluate_response(case, final)
            metadata = json.loads(
                await anyio.Path(os.path.join(sample_dir, "metadata.json")).read_text(encoding="utf-8")
            )
            if metadata["error"] is not None or metadata["http_status"] != 200 or metadata["stream_error"]:
                result["passed"] = False
                result["diagnostics"]["infrastructure"] = (
                    metadata["error"]
                    or f"http_status={metadata['http_status']}, stream_error={metadata['stream_error']}"
                )
            await _write_json(result_path, result)
            samples.append(
                {
                    "case_id": case["id"],
                    "repetition": repetition,
                    "passed": result["passed"],
                    "sample": f"samples/{case['id']}/r{repetition}",
                }
            )

    samples.sort(key=lambda item: (item["case_id"], item["repetition"]))
    summary = {
        **initial_summary,
        "oracle_correction": (
            "Use the grammar-documented catalog and inspect graph lowering without rejecting "
            "residual configuration assertions."
        ),
        "rescored_at": _now(),
        "passed": sum(bool(sample["passed"]) for sample in samples),
        "failed": sum(not sample["passed"] for sample in samples),
        "samples": samples,
    }
    await _write_json(summary_path, summary)
    sys.stdout.write(f"{label} rescored: {summary['passed']}/{summary['sample_count']} samples passed\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, choices=("baseline", "candidate", "candidate-v2"))
    parser.add_argument("--rescore", action="store_true")
    args = parser.parse_args()
    anyio.run(rescore if args.rescore else run, args.label)


if __name__ == "__main__":
    main()
