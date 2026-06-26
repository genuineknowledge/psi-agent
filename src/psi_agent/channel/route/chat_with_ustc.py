from __future__ import annotations

import json
from collections.abc import Sequence

from aiohttp import ClientSession, ClientTimeout
from loguru import logger

ROUTER_BASE_URL = "https://api.llm.ustc.edu.cn/v1"
ROUTER_MODEL = "qwen-chat"
API_KEY = "sk-DjPSlg6HYX5ZMj9fhazboA"


def _build_router_system_prompt() -> str:
    return (
        "你是一个模型路由器, 只能从候选模型列表中选择一个最合适的模型名。"
        "先分析任务的具体信息, 而不是只看字数: 主题, 语言, 地域, 法律/合规要求, 平台生态, 输入数据和目标受众。"
        "如果任务明显与中国大陆, 中文语境, 国内平台, 国内政策法规或国内业务流程有关, 优先选择国内模型。"
        "如果任务明显与海外, 英文语境, 国外平台, 国外政策法规或国际业务有关, 优先选择国外模型。"
        "如果任务同时涉及中外两边, 优先选择与主要语境和目标用户更匹配的模型。"
        "如果地域不明显, 再结合任务复杂度, 代码/推理强度和候选模型能力做选择。"
        "只返回 JSON 对象, 格式必须是 {\"model\": \"候选模型名\"}, 不要输出任何解释。"
    )


def _build_router_messages(message: str, models: Sequence[str]) -> list[dict[str, str]]:
    models_text = "\n".join(f"{index + 1}. {model}" for index, model in enumerate(models))
    system_prompt = _build_router_system_prompt()
    user_prompt = (
        "候选模型(从简单到强大排序, 优先考虑地域和语言匹配):\n"
        f"{models_text}\n\n"
        "请结合任务的具体信息(地域, 语言, 合规, 平台背景, 目标受众)选择一个模型:\n"
        f"{message}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _extract_model_choice(text: str, models: Sequence[str]) -> str | None:
    normalized = text.strip()
    if not normalized:
        return None

    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        candidate = parsed.get("model")
        if isinstance(candidate, str) and candidate in models:
            return candidate

    for model in models:
        if normalized == model:
            return model

    for model in models:
        if model in normalized:
            return model

    return None


async def choose_model_via_ustc_api(
    message: str,
    *,
    models: Sequence[str],
) -> str | None:
    configured_models = tuple(model for model in models if model)
    if not configured_models:
        return None
    if len(configured_models) == 1:
        return configured_models[0]

    resolved_api_key = API_KEY
    endpoint = f"{ROUTER_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": ROUTER_MODEL,
        "messages": _build_router_messages(message, configured_models),
        "temperature": 0,
        "max_tokens": 32,
    }
    headers = {
        "Authorization": f"Bearer {resolved_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with ClientSession(timeout=ClientTimeout(total=30)) as session, session.post(
            endpoint,
            json=payload,
            headers=headers,
        ) as response:
            response.raise_for_status()
            data = await response.json()
    except Exception as exc:
        logger.debug(f"Router API request failed: {exc}")
        return None

    choices = data.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return None

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None

    message_part = first_choice.get("message")
    if not isinstance(message_part, dict):
        return None

    content = message_part.get("content")
    if not isinstance(content, str):
        return None

    return _extract_model_choice(content, configured_models)
