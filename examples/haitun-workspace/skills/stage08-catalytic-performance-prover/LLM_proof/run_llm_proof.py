#!/usr/bin/env python3
"""Run Stage08 catalytic-performance proof with one external LLM call per catalyst."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import aiohttp

DEFAULT_OUTPUT = "ows630-proof-llm.md"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_BASE_URL = "https://api.chatanywhere.tech/v1"
RETRYABLE_ERROR_NAMES = {
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "InternalServerError",
    "RateLimitError",
    "RetryableProofValidationError",
}


PROMPT_TEMPLATE = """# 角色设定
你是一位精通材料化学和光催化机制的顶尖专家。你需要对单一全水解光催化剂的催化性能进行深入、严谨、基于逻辑的理论推演和证明。

# 任务目标
请根据我提供的【单个催化剂名称及化学式】,输出一段独立、完整、且具有可解释性的逻辑推理过程,论证其催化性能。

# 推理原则
1. **优先论证有效性**:在推理过程中,请优先尝试论证该材料具有催化性能。你可以结合一般的材料化学规律或光催化机制进行推演。
2. **极其谨慎的否定**:只有当根据提供的化学身份信息完全无法推导任何催化性能,\
或者推理过程明确推翻了其催化可能性时,才能判定为无性能。
3. **理论与事实的边界**:所有的性能推理都是基于理论推导,**绝对不可**将推测出的性能或性能缺失表述为“已在实验中被证实”。
4. **禁止套路化分析**:不要使用任何可复用的决策清单、固定评分标准、阈值判断或固定的分析模板。\
必须基于该特定材料的具体化学性质进行定制化推理。

# 输出要求
1. **语言与篇幅**:必须使用**中文**输出。输出内容必须是**单独的一个自然段**,且字数**不少于600字**。
2. **内容连贯性**:由于此段落需要具备绝对的独立可读性,**禁止使用代词**\
(如“该材料”、“它”、“这种物质”等),必须在上下文中反复使用具体的催化剂全称和明确的化学专有名词,\
确保完全不了解上下文的化学家也能毫无障碍地阅读。
3. **结构顺序**:必须**先进行完整的论证和推理**,然后在该段落的最后一句给出最终结论。
4. **结论规范**:最终结论必须且只能从以下两个短语中二选一:
   - 如果认为有性能,最后一句结尾必须包含:`可能有催化性能`。
   - 如果确认完全无法推理出性能,最后一句结尾必须包含:`无催化性能`。

---
**【输入催化剂】**:{catalyst}
**【请开始你的推理证明过程】**:"""


JUDGEMENT_PROMPT_TEMPLATE = """你是催化性能结论审阅员。请根据下面给出的单个催化剂证明文本,\
判断证明文本是否给出了“无催化性能”的最终判断。

要求:
1. 只判断证明文本已经表达的最终判断,不要重新推理材料本身。
2. 如果证明文本的最终判断是无催化性能,输出:{{"no_catalytic_performance": true}}
3. 如果证明文本的最终判断不是无催化性能,输出:{{"no_catalytic_performance": false}}
4. 只输出JSON对象,不要输出解释文字。

催化剂:{catalyst}
证明文本:{proof}
"""


@dataclass(frozen=True)
class Catalyst:
    index: int
    name: str
    formula: str


@dataclass
class ProofResult:
    index: int
    name: str
    formula: str
    proof: str


class RetryableProofValidationError(RuntimeError):
    """Raised when an API response is syntactically successful but unusable."""


def find_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "AGENTS.md").exists() and (parent / ".agents").exists():
            return parent
    return Path.cwd()


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return cwd_path
    return find_repo_root() / path


def load_catalysts(input_json: Path, limit: int | None = None) -> list[Catalyst]:
    data = json.loads(input_json.read_text(encoding="utf-8"))
    records = data.get("retained_records")
    if not isinstance(records, list):
        raise ValueError(f"Missing retained_records list in {input_json}")

    catalysts: list[Catalyst] = []
    for idx, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"retained_records[{idx - 1}] must be an object")
        name = str(record.get("catalyst_name") or "").strip()
        formula = str(record.get("recommended_formula") or record.get("reduced_formula") or "").strip()
        if not name or not formula:
            raise ValueError(f"Missing catalyst_name or formula in retained_records[{idx - 1}]")
        catalysts.append(Catalyst(index=idx, name=name, formula=formula))
        if limit is not None and len(catalysts) >= limit:
            break
    return catalysts


def chinese_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def build_messages(catalyst: Catalyst) -> list[dict[str, str]]:
    user_prompt = PROMPT_TEMPLATE.format(catalyst=f"{catalyst.name} / {catalyst.formula}")
    return [{"role": "user", "content": user_prompt}]


def build_judgement_messages(result: ProofResult) -> list[dict[str, str]]:
    user_prompt = JUDGEMENT_PROMPT_TEMPLATE.format(
        catalyst=f"{result.name} / {result.formula}",
        proof=result.proof,
    )
    return [{"role": "user", "content": user_prompt}]


async def call_model(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
    catalyst: Catalyst,
    model: str,
    temperature: float,
    max_tokens: int,
    min_chinese_chars: int = 0,
) -> ProofResult:
    proof = await request_completion(
        session=session,
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=build_messages(catalyst),
        temperature=temperature,
        max_tokens=max_tokens,
    )
    proof = proof.strip()
    proof = re.sub(r"\s*\n+\s*", "", proof)
    if min_chinese_chars and chinese_char_count(proof) < min_chinese_chars:
        raise RetryableProofValidationError(
            f"Proof for index {catalyst.index} has {chinese_char_count(proof)} Chinese chars; "
            f"expected at least {min_chinese_chars}."
        )
    return ProofResult(
        index=catalyst.index,
        name=catalyst.name,
        formula=catalyst.formula,
        proof=proof,
    )


async def request_completion(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> str:
    async with session.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
    ) as response:
        response.raise_for_status()
        payload = await response.json()
    if not isinstance(payload, dict):
        raise ValueError("Chat completion response must be a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("Chat completion response must contain one choice")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ValueError("Chat completion choice must contain text content")
    return message["content"]


def is_retryable_api_error(exc: Exception) -> bool:
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.status == 429 or exc.status >= 500
    if isinstance(exc, (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError)):
        return True
    if exc.__class__.__name__ in RETRYABLE_ERROR_NAMES:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in ("429", "rate limit", "too many requests", "timeout"))


async def call_with_retries(
    call_factory: Any,
    retries: int,
    base_delay: float,
    label: str,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await call_factory()
        except Exception as exc:
            last_error = exc
            if attempt >= retries or not is_retryable_api_error(exc):
                raise
            delay = base_delay * (2**attempt)
            sys.stderr.write(f"[retry] {label} attempt={attempt + 1} delay={delay:.1f}s error={exc}\n")
            await asyncio.sleep(delay)
    raise RuntimeError(f"Retry loop exhausted for {label}: {last_error}")


def load_checkpoint_results(
    checkpoint_path: Path | None,
    catalysts: list[Catalyst],
    min_chinese_chars: int = 0,
) -> dict[int, ProofResult]:
    if checkpoint_path is None or not checkpoint_path.exists():
        return {}
    expected = {catalyst.index: catalyst for catalyst in catalysts}
    loaded: dict[int, ProofResult] = {}
    for line in checkpoint_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            result = ProofResult(
                index=int(payload["index"]),
                name=str(payload["name"]),
                formula=str(payload["formula"]),
                proof=str(payload["proof"]),
            )
        except KeyError, TypeError, ValueError, json.JSONDecodeError:
            continue
        catalyst = expected.get(result.index)
        if (
            catalyst
            and catalyst.name == result.name
            and catalyst.formula == result.formula
            and result.proof
            and chinese_char_count(result.proof) >= min_chinese_chars
        ):
            loaded[result.index] = result
    return loaded


def api_settings(args: argparse.Namespace, require_api_key: bool) -> tuple[str | None, str, str]:
    api_key = args.api_key or os.environ.get("LLM_PROOF_API_KEY")
    if not api_key and require_api_key:
        raise RuntimeError("Missing API key. Set LLM_PROOF_API_KEY or pass --api-key.")
    model = args.model or os.environ.get("LLM_PROOF_MODEL") or DEFAULT_MODEL
    base_url = args.base_url or os.environ.get("LLM_PROOF_BASE_URL") or DEFAULT_BASE_URL
    return api_key, model, base_url


async def run_batch(args: argparse.Namespace) -> list[ProofResult]:
    input_json = resolve_path(args.input_json)
    catalysts = load_catalysts(input_json, args.limit)

    api_key, model, base_url = api_settings(args, require_api_key=not args.dry_run)

    if args.dry_run:
        sys.stdout.write(
            f"Input JSON: {input_json}\nModel: {model}\nBase URL: {base_url}\nCatalysts: {len(catalysts)}\n"
        )
        if catalysts:
            sys.stdout.write(
                "First single-catalyst prompt:\n"
                f"{json.dumps(build_messages(catalysts[0]), ensure_ascii=False, indent=2)}\n"
            )
        return []

    if api_key is None:
        raise RuntimeError("Missing API key")
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=args.request_timeout))
    semaphore = asyncio.Semaphore(args.concurrency)
    checkpoint_path = resolve_path(args.checkpoint) if args.checkpoint else None
    checkpoint_results = load_checkpoint_results(checkpoint_path, catalysts, args.min_proof_chinese_chars)
    results: list[ProofResult] = list(checkpoint_results.values())
    pending_catalysts = [catalyst for catalyst in catalysts if catalyst.index not in checkpoint_results]
    if checkpoint_results:
        sys.stderr.write(f"[checkpoint] loaded {len(checkpoint_results)} completed proofs\n")

    async def guarded_call(catalyst: Catalyst) -> ProofResult:
        async with semaphore:
            result = await call_with_retries(
                lambda: call_model(
                    session=session,
                    base_url=base_url,
                    api_key=api_key,
                    catalyst=catalyst,
                    model=model,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                    min_chinese_chars=args.min_proof_chinese_chars,
                ),
                retries=args.request_retries,
                base_delay=args.retry_base_delay,
                label=f"proof {catalyst.index:02d} {catalyst.formula}",
            )
            sys.stderr.write(f"[done] {result.index:02d} {result.formula}\n")
            return result

    try:
        for completed in asyncio.as_completed([guarded_call(catalyst) for catalyst in pending_catalysts]):
            result = await completed
            results.append(result)
            if checkpoint_path:
                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                with checkpoint_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
    finally:
        await session.close()

    return sorted(results, key=lambda item: item.index)


def parse_judgement_json(content: str) -> bool:
    text = content.strip()
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"Judgement response is not JSON: {content!r}")
    payload = json.loads(match.group(0))
    value = payload.get("no_catalytic_performance")
    if not isinstance(value, bool):
        raise ValueError(f"Judgement JSON missing boolean no_catalytic_performance: {content!r}")
    return value


async def call_judgement_model(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
    result: ProofResult,
    model: str,
    max_tokens: int,
    retries: int = 2,
) -> bool:
    last_error: Exception | None = None
    for _ in range(retries + 1):
        content = await call_with_retries(
            lambda: request_completion(
                session=session,
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=build_judgement_messages(result),
                temperature=0,
                max_tokens=max_tokens,
            ),
            retries=2,
            base_delay=5.0,
            label=f"judgement {result.index:02d} {result.formula}",
        )
        try:
            return parse_judgement_json(content)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
    raise RuntimeError(f"Failed to parse judgement for index {result.index}: {last_error}")


async def run_judgement_audit(args: argparse.Namespace, results: list[ProofResult]) -> dict[str, Any]:
    api_key, model, base_url = api_settings(args, require_api_key=True)
    if api_key is None:
        raise RuntimeError("Missing API key")
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=args.request_timeout))
    semaphore = asyncio.Semaphore(args.concurrency)
    no_performance_entries: list[dict[str, Any]] = []

    async def guarded_judgement(result: ProofResult) -> tuple[ProofResult, bool]:
        async with semaphore:
            judged_no_performance = await call_judgement_model(
                session=session,
                base_url=base_url,
                api_key=api_key,
                result=result,
                model=model,
                max_tokens=args.judgement_max_tokens,
            )
            sys.stderr.write(f"[judged] {result.index:02d} {result.formula}\n")
            return result, judged_no_performance

    try:
        for completed in asyncio.as_completed([guarded_judgement(result) for result in results]):
            result, judged_no_performance = await completed
            if judged_no_performance:
                no_performance_entries.append(
                    {
                        "index": result.index,
                        "name": result.name,
                        "formula": result.formula,
                    }
                )
    finally:
        await session.close()

    no_performance_entries.sort(key=lambda item: item["index"])
    return {
        "no_catalytic_performance_count": len(no_performance_entries),
        "no_catalytic_performance_indices": [item["index"] for item in no_performance_entries],
        "no_catalytic_performance_entries": no_performance_entries,
    }


def write_markdown(results: list[ProofResult], output_path: Path) -> None:
    lines = ["# Stage08 候选催化性能外部模型独立论证", ""]
    for result in results:
        lines.append(f"### {result.index}. {result.name}({result.formula})")
        lines.append("")
        lines.append(result.proof)
        lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def normalized_sentence(sentence: str) -> str:
    normalized = re.sub(r"([A-Z][a-z]?\d*){2,}", "FORMULA", sentence)
    normalized = re.sub(r"[A-Z][a-z]?\d*[+-]", "ION", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def audit_repetition(results: list[ProofResult], top_n: int) -> dict[str, Any]:
    sentence_locations: dict[str, list[int]] = {}
    for result in results:
        for sentence in re.split(r"(?<=[。\uFF1B])", result.proof):
            sentence = sentence.strip()
            if not sentence:
                continue
            key = normalized_sentence(sentence)
            sentence_locations.setdefault(key, []).append(result.index)

    repeated = [
        {
            "count": len(indices),
            "indices": indices,
            "sentence": sentence,
        }
        for sentence, indices in sentence_locations.items()
        if len(indices) >= 3
    ]
    repeated.sort(key=lambda item: item["count"], reverse=True)
    return {
        "repeated_sentence_types_ge_3": len(repeated),
        "top_repeated_sentences": repeated[:top_n],
    }


def write_audit(
    results: list[ProofResult],
    audit_path: Path,
    top_n: int,
    no_catalytic_performance: dict[str, Any],
) -> None:
    char_counts = [
        {
            "index": result.index,
            "formula": result.formula,
            "chinese_chars": chinese_char_count(result.proof),
        }
        for result in results
    ]
    audit = {
        "total": len(results),
        "min_chinese_chars": min((item["chinese_chars"] for item in char_counts), default=0),
        "char_counts": char_counts,
        "no_catalytic_performance": no_catalytic_performance,
        "repetition": audit_repetition(results, top_n),
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call an external OpenAI-compatible LLM once per Stage08 catalyst and assemble a proof document."
    )
    parser.add_argument(
        "--input-json",
        required=True,
        help="Path to ROUND_PARALLEL_SYNTHESIS_INDEX.json or another compatible index containing retained_records.",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Markdown output path.")
    parser.add_argument("--audit-json", default=None, help="Optional audit JSON path. Defaults to <output>.audit.json.")
    parser.add_argument("--checkpoint", default=None, help="Optional JSONL checkpoint path for completed calls.")
    parser.add_argument("--api-key", default=None, help="API key. Prefer LLM_PROOF_API_KEY.")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL.")
    parser.add_argument("--model", default=None, help=f"Model name. Defaults to {DEFAULT_MODEL}.")
    parser.add_argument("--concurrency", type=int, default=20, help="Maximum concurrent API calls.")
    parser.add_argument("--request-retries", type=int, default=4, help="Retry count for retryable API request errors.")
    parser.add_argument("--retry-base-delay", type=float, default=5.0, help="Initial retry delay in seconds.")
    parser.add_argument("--request-timeout", type=float, default=90.0, help="Per-request API timeout in seconds.")
    parser.add_argument("--temperature", type=float, default=0.75, help="Sampling temperature.")
    parser.add_argument("--max-tokens", type=int, default=1800, help="Maximum tokens per catalyst response.")
    parser.add_argument(
        "--min-proof-chinese-chars",
        type=int,
        default=0,
        help="Reject and retry proof responses below this Chinese-character count.",
    )
    parser.add_argument("--judgement-max-tokens", type=int, default=80, help="Maximum tokens per judgement response.")
    parser.add_argument("--limit", type=int, default=None, help="Limit catalysts for a small test run.")
    parser.add_argument(
        "--audit-top-n", type=int, default=20, help="Number of repeated sentences stored in audit JSON."
    )
    parser.add_argument("--dry-run", action="store_true", help="Print source count and first prompt without API calls.")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    if args.concurrency < 1:
        raise ValueError("--concurrency must be at least 1")

    results = await run_batch(args)
    if args.dry_run:
        return 0

    output_path = resolve_path(args.output)
    audit_path = (
        resolve_path(args.audit_json)
        if args.audit_json
        else output_path.with_suffix(output_path.suffix + ".audit.json")
    )
    no_catalytic_performance = await run_judgement_audit(args, results)
    write_markdown(results, output_path)
    write_audit(results, audit_path, args.audit_top_n, no_catalytic_performance)

    sys.stderr.write(f"Wrote {output_path}\nWrote {audit_path}\n")
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
