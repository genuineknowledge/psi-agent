from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

from loguru import logger

from psi_agent.protocol import ToolFunction


def load_tools_from_workspace(tools_dir: Path) -> dict[str, ToolFunction]:
    tools: dict[str, ToolFunction] = {}
    if not tools_dir.is_dir():
        logger.warning(f"Tools directory not found: {tools_dir}")
        return tools

    for py_file in sorted(tools_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module_name = py_file.stem
        expected_func_name = module_name

        try:
            spec = importlib.util.spec_from_file_location(module_name, str(py_file))
            if spec is None or spec.loader is None:
                logger.warning(f"Could not load module spec for {py_file}")
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            logger.error(f"Failed to load tool module {py_file}: {e}")
            continue

        func = getattr(module, expected_func_name, None)
        if func is None:
            logger.warning(f"Function '{expected_func_name}' not found in {py_file}")
            continue

        if not inspect.iscoroutinefunction(func):
            logger.warning(f"Function '{expected_func_name}' in {py_file} is not async, skipping")
            continue

        if expected_func_name.startswith("_"):
            logger.debug(f"Skipping private function '{expected_func_name}'")
            continue

        tool_func = ToolFunction.from_callable(func)
        tools[expected_func_name] = tool_func
        logger.info(f"Loaded tool: {expected_func_name} from {py_file}")

    logger.info(f"Loaded {len(tools)} tool(s) from {tools_dir}")
    return tools
