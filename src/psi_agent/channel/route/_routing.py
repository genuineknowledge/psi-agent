from __future__ import annotations

from collections.abc import Sequence

from loguru import logger

from psi_agent.channel.route.chat_with_ustc import choose_model_via_ustc_api


async def select_model_for_message(
    message: str,
    *,
    models: Sequence[str] = (),
) -> str | None:
    """Pick a model for one request using the USTC router API.

    The configured models should be ordered from simpler/faster to
    stronger/slower. We ask the router model to choose one candidate using a
    prompt-based judgment instead of local keyword heuristics.
    """
    configured_models = tuple(model for model in models if model)
    if not configured_models:
        return None

    if len(configured_models) == 1:
        selected_model = configured_models[0]
        logger.info(f"Selected model: {selected_model}")
        return selected_model

    selected_model = await choose_model_via_ustc_api(
        message,
        models=configured_models,
    )

    if selected_model in configured_models:
        logger.info(f"Selected model: {selected_model}")
        return selected_model

    fallback_model = configured_models[0]
    logger.warning(
        "Router API did not return a valid model; falling back to the first configured model",
    )
    logger.info(f"Selected model: {fallback_model}")
    return fallback_model
