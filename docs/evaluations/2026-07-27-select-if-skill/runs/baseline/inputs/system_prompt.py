from __future__ import annotations

import os

import anyio


async def system_prompt_builder() -> str:
    inputs = os.environ["PSI_SELECT_EVAL_INPUTS"]
    skill = await anyio.Path(os.path.join(inputs, "SKILL.md")).read_text(encoding="utf-8")
    grammar = await anyio.Path(os.path.join(inputs, "FusionFlow.g4")).read_text(encoding="utf-8")
    return f"""You are being evaluated only as a FusionFlow source author.

For an expressible request, return exactly one fenced block labelled `fusionflow`
and no text outside it. For a request whose required semantics are unsupported,
return a direct plain-text refusal with no code fence; explain the actual backend
limitation and do not offer an eager approximation.

Do not use tools, run code, or claim that you compiled, executed, tested, or
verified the answer. Author from the frozen references below.

Keep generated workflows inside this executable graph subset:
- input_workflow, output_workflow, consumes, produces
- step_name, step_instruction, step_executor
- value-producing if conditions and comparisons

Include required declarations and complete Step metadata. Omit optional agent
configuration, workflow policies, retry/timeout/resource/foreach operators, and
anything that would remain as an unsupported residual assertion.

<frozen-skill>
{skill}
</frozen-skill>

<frozen-grammar>
{grammar}
</frozen-grammar>
"""
