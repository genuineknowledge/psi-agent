"""Generate an image file from a scene description (+ optional reference image paths)."""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _image_gen as _ig


async def generate_image(
    description: str,
    reference_images: str = "",
    output_path: str = "",
    width: int = 0,
    height: int = 0,
    seed: int = -1,
) -> str:
    """Create an image file from a drawable scene description.

    Pass a **scene description** (what to depict or change), not the user's
    polite request wrapper. Optional ``reference_images`` is a JSON array of
    absolute paths (``[]`` or empty = text-to-image).

    Calls **MiniMax** ``image-01`` via ``POST /v1/image_generation`` (BYOK).
    Returns ``ok: false`` with a clear ``message`` when credentials are unset
    or the upstream API fails.

    Env: workspace ``.env.multimodal`` (auto-loaded).
    ``MINIMAX_API_KEY`` (or ``IMAGE_GEN_API_KEY``), ``MINIMAX_API_HOST`` (default CN host),
    ``IMAGE_GEN_MODEL`` (default ``image-01``).

    Args:
        description: Drawable scene or change intent (required).
        reference_images: JSON list of image paths, e.g. '["D:/img/a.png"]'.
        output_path: Output file path. Empty = ``generated/images/gen-*.png``.
        width: Image width in pixels; 0 = default (512).
        height: Image height in pixels; 0 = default (512).
        seed: Reproducibility seed; -1 = random.

    Returns:
        JSON with ok, path, mode, seed, width, height, backend, message.
    """
    result = await _ig.generate_image_impl(
        description=description,
        reference_images_raw=reference_images,
        output_path=output_path,
        width=width,
        height=height,
        seed=seed,
    )
    return _ig.dumps_result(result)
