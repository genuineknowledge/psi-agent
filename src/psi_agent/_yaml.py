"""Shared YAML header parsing utility."""

from __future__ import annotations

import re

import yaml
from loguru import logger


def parse_yaml_header(content: str) -> tuple[dict | None, str]:
    """Extract YAML front matter from markdown content.

    Returns (header_dict | None, body_without_header).
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return None, content
    try:
        header = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        logger.warning(f"Failed to parse YAML header: {e}")
        return None, content
    body = content[match.end() :]
    return header, body
