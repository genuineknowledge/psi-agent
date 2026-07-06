"""Shared YAML header parsing utility."""

from __future__ import annotations

import re
from typing import Any

import yaml
from loguru import logger


def parse_yaml_header(content: str) -> tuple[dict[str, Any] | None, str]:
    """Extract YAML front matter from markdown content.

    Returns (header_dict | None, body_without_header).
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        logger.debug("No YAML front matter found in content")
        return None, content
    try:
        header = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        logger.warning(f"Failed to parse YAML header: {e}")
        return None, content
    if not isinstance(header, dict):
        logger.warning(f"YAML header is not a dict, got {type(header).__name__}")
        return None, content
    logger.debug(f"Parsed YAML header: {header}")
    body = content[match.end() :]
    return header, body
