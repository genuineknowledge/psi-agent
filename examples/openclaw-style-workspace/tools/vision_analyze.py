"""Vision analyze tool — stub requiring multimodal model support."""

from __future__ import annotations


async def vision_analyze(image_path: str, prompt: str = "Describe this image.") -> str:
    """Analyze an image using a vision-capable model.

    This tool requires the connected LLM provider to support multimodal
    (image) inputs. In the current psi-agent setup, images must be passed
    directly in the message content — this stub documents the interface.

    Args:
        image_path: Path to the image file to analyze.
        prompt: Question or instruction about the image.

    Returns:
        A note explaining how to use vision capabilities with psi-agent.
    """
    return (
        f"[vision_analyze] Image: {image_path}\n"
        f"Prompt: {prompt}\n\n"
        "Vision analysis requires a multimodal model (e.g. claude-3-5-sonnet, gpt-4o).\n"
        "To analyze an image with psi-agent, include the image as a base64-encoded\n"
        "content block in the user message. This tool is a placeholder that documents\n"
        "the interface — wire it up to your psi-ai-* component's vision support."
    )
