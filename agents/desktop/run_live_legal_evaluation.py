"""Run seven legal prompts against a real Session and preserve raw evidence."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

import aiohttp
import anyio
from loguru import logger

QUESTIONS = (
    "公司投融资主要涉及什么、有哪些流程、涉及哪些法律文件? 请以中国大陆科技创业公司为背景回答。",
    "请审查这份虚拟股东协议, 指出风险点, 站在创始人角度。协议文件: [RECV:{agreement_a}]",
    "公司股权架构常有的设计模式有哪些? 请比较不同模式的优缺点, 并说明科技创业公司选择时要看什么。",
    (
        "请整理一篇法律文献库, 从两大框架出发: 一是股东协议依法需要覆盖或与法定公司治理衔接的基本事项, "
        "二是双方可以约定的自治条款。两大框架下分小类, 每类列出需要核验的现行中国大陆法律、司法解释或官方规则。"
        "要求全面, 并明确区分已确认、推断和需验证。"
    ),
    (
        "请逐条对比这两份虚拟股东协议, 分析风险点、不同设计的优缺点, 并说明创始人和投资人的立场差异。"
        "文件A: [RECV:{agreement_a}] 文件B: [RECV:{agreement_b}]"
    ),
    (
        "请基于协议B的虚拟交易背景, 起草一份可供测试的正式股东协议, 文字、编号和排版按合同标准格式处理, "
        "并生成DOCX文件。不要把它描述成正式法律意见。文件B: [RECV:{agreement_b}]"
    ),
    (
        "请为一家中国大陆科技创业公司构思完整的法务管理SOP, 覆盖合同、公司治理、印章、诉讼、合规、"
        "知识产权、数据、外部律师、档案、事故响应、指标和责任分工; 请生成可执行的文档。"
    ),
)


async def _post_turn(
    url: str,
    question: str,
    *,
    user_id: str,
    profile_id: str,
) -> tuple[str, list[str], list[str]]:
    body = {
        "model": "deepseek-chat",
        "stream": True,
        "user_id": user_id,
        "profile_id": profile_id,
        "messages": [{"role": "user", "content": question}],
    }
    content: list[str] = []
    errors: list[str] = []
    finish_reasons: list[str] = []
    timeout = aiohttp.ClientTimeout(total=600)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session, session.post(url, json=body) as response:
            async for raw_line in response.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    errors.append(f"invalid_sse:{payload[:200]}")
                    continue
                choices = chunk.get("choices") if isinstance(chunk, dict) else None
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                    continue
                choice = choices[0]
                delta = choice.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    content.append(delta["content"])
                finish_reason = choice.get("finish_reason")
                if isinstance(finish_reason, str):
                    finish_reasons.append(finish_reason)
                    if finish_reason == "error":
                        errors.append("finish_reason=error")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return "".join(content), errors, finish_reasons


async def run(
    session_url: str,
    output: anyio.Path,
    agreement_a: anyio.Path,
    agreement_b: anyio.Path,
    user_id: str,
    profile_id: str,
) -> None:
    await output.mkdir(parents=True, exist_ok=True)
    evidence: list[dict[str, object]] = []
    for index, template in enumerate(QUESTIONS, 1):
        question = template.format(agreement_a=agreement_a, agreement_b=agreement_b)
        started = datetime.now(UTC)
        answer, errors, finish_reasons = await _post_turn(
            session_url,
            question,
            user_id=user_id,
            profile_id=profile_id,
        )
        evidence.append(
            {
                "turn": index,
                "experiment_user_id": user_id,
                "started_at": started.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "question": question,
                "answer": answer,
                "errors": errors,
                "finish_reasons": finish_reasons,
            }
        )
        logger.info(f"turn={index} answer_chars={len(answer)} errors={errors} finish={finish_reasons}")
    evidence_path = output / "live-evidence.json"
    await evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--agreement-a", required=True)
    parser.add_argument("--agreement-b", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--profile-id", default="legal-learning")
    args = parser.parse_args()
    anyio.run(
        run,
        args.session_url,
        anyio.Path(args.output),
        anyio.Path(args.agreement_a),
        anyio.Path(args.agreement_b),
        args.user_id,
        args.profile_id,
    )


if __name__ == "__main__":
    main()
