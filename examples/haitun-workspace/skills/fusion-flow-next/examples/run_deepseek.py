from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from typing import cast

import anyio
from any_llm.api import ChatCompletion, acompletion

from psi_agent.workflow_execution import ResourceCapacity

from .run_workflow import execute_workflow


def _parse_mapping(value: str, *, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} must be a JSON object") from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ValueError(f"{label} must be a JSON object with string keys")
    return cast(dict[str, object], parsed)


def _parse_resource_capacities(value: str, *, label: str) -> dict[str, ResourceCapacity]:
    parsed = _parse_mapping(value, label=label)
    capacities: dict[str, ResourceCapacity] = {}
    for resource_id, capacity in parsed.items():
        if type(capacity) is int:
            capacities[resource_id] = capacity
            continue
        if isinstance(capacity, list) and all(isinstance(instance_id, str) for instance_id in capacity):
            capacities[resource_id] = tuple(cast(list[str], capacity))
            continue
        raise ValueError(
            f"{label} value for {resource_id!r} must be an integer capacity or an array of resource instance IDs"
        )
    return capacities


async def _read_mapping(value: str | None, path: str | None, *, label: str) -> dict[str, object]:
    if value is not None:
        return _parse_mapping(value, label=label)
    if path is None:
        raise ValueError(f"{label} or {label} file is required")
    source = await anyio.Path(path).read_text(encoding="utf-8")
    return _parse_mapping(source, label=f"{label} file")


async def _read_inputs(value: str | None, path: str | None) -> dict[str, object]:
    return await _read_mapping(value, path, label="inputs")


async def _read_resource_capacities(
    value: str | None,
    path: str | None,
) -> Mapping[str, ResourceCapacity] | None:
    if value is None and path is None:
        return None
    raw = value if value is not None else await anyio.Path(cast(str, path)).read_text(encoding="utf-8")
    label = "resource capacities" if value is not None else "resource capacities file"
    return _parse_resource_capacities(raw, label=label)


async def _complete_with_deepseek(prompt: str) -> dict[str, object]:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    with anyio.fail_after(120):
        response = cast(
            ChatCompletion,
            await acompletion(
                provider="deepseek",
                model="deepseek-v4-flash",
                api_key=api_key,
                api_base="https://api.deepseek.com/v1",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Execute one workflow step. Return exactly one JSON "
                            "object whose keys are the requested output artifact names."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=512,
                temperature=0.0,
                response_format={"type": "json_object"},
                stream=False,
            ),
        )
    if not response.choices:
        raise RuntimeError("DeepSeek returned no choices")
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DeepSeek returned no text")
    return _parse_mapping(content, label="DeepSeek response")


async def _contextual_complete_with_deepseek(
    prompt: str,
    context: object,
) -> dict[str, object]:
    """Adapt DeepSeek's named JSON result to the contextual runner contract."""

    del context
    return await _complete_with_deepseek(prompt)


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Run one FusionFlow Next example with DeepSeek.")
    parser.add_argument("workflow", help="Path to a .workflow example")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--inputs", help="JSON object containing every workflow input artifact")
    inputs.add_argument("--inputs-file", help="UTF-8 JSON file containing every workflow input artifact")
    resources = parser.add_mutually_exclusive_group()
    resources.add_argument(
        "--resource-capacities",
        help='JSON object such as \'{"gpu_device": 2}\' or \'{"gpu_device": ["cuda:0", "cuda:1"]}\'',
    )
    resources.add_argument(
        "--resource-capacities-file",
        help="UTF-8 JSON file containing resource capacities or concrete instance IDs",
    )
    parser.add_argument(
        "--strict-executors",
        action="store_true",
        help="require every executor to declare exactly one Agent, Human, or Program concept",
    )
    args = parser.parse_args()
    source = await anyio.Path(args.workflow).read_text(encoding="utf-8")
    result = await execute_workflow(
        source,
        inputs=await _read_inputs(args.inputs, args.inputs_file),
        contextual_complete=_contextual_complete_with_deepseek,
        resource_capacities=await _read_resource_capacities(
            args.resource_capacities,
            args.resource_capacities_file,
        ),
        strict_executors=args.strict_executors,
    )
    sys.stdout.write(f"{json.dumps(result, ensure_ascii=False, indent=2)}\n")


if __name__ == "__main__":
    anyio.run(_main)
